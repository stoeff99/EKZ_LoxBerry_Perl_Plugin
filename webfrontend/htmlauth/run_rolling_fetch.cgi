#!/usr/bin/perl
use strict;
use warnings;

use CGI::Carp qw(fatalsToBrowser);
use CGI;
use JSON::PP;
use File::Spec;
use POSIX qw(strftime);
use LoxBerry::System;
use LoxBerry::Log;
use FindBin;
require "$FindBin::Bin/common.pl";

our ($lbpdatadir, $lbpurl, $lbptemplatedir, $lbplogdir);

my $q = CGI->new;
print $q->header('application/json; charset=utf-8');

my $log = LoxBerry::Log->new(
  name      => 'fetch',
  filename  => "$lbplogdir/fetch.log",
  append    => 1,
  loglevel  => 6,
  addtime   => 1,
  stderr    => 0,
  nosession => 1,
);

LOGSTART("run_rolling_fetch started");

# -------- Normalization helpers --------
sub _norm_unit_name {
  my ($u) = @_;
  return 'CHF_kWh' if defined $u && lc($u) eq 'chf_kwh';
  return 'CHF_M'   if defined $u && lc($u) eq 'chf_m';
  return 'CHF_kWh' if defined $u && lc($u) =~ /^chf[_-]?kwh$/;
  return 'CHF_M'   if defined $u && lc($u) =~ /^chf[_-]?m$/;
  return $u // 'CHF_kWh';
}

sub _ordered_cost_array {
  my ($arr) = @_;
  $arr ||= [];
  my %v;
  for my $e (@$arr) {
    next unless ref $e eq 'HASH';
    my $unit = _norm_unit_name($e->{unit});
    $v{$unit} = $e->{value} + 0 if exists $e->{value};
  }
  return [
    { unit => 'CHF_M',   value => ($v{'CHF_M'}   // 0) + 0 },
    { unit => 'CHF_kWh', value => ($v{'CHF_kWh'} // 0) + 0 },
  ];
}

sub _norm_one_block {
  my ($p) = @_;
  return {
    start_timestamp => $p->{start_timestamp},
    end_timestamp   => $p->{end_timestamp},
    electricity     => _ordered_cost_array($p->{electricity}),
    grid            => _ordered_cost_array($p->{grid}),
    integrated      => _ordered_cost_array($p->{integrated}),
    regional_fees   => _ordered_cost_array($p->{regional_fees}),
  };
}

sub _tz_offset_colon {
  my $z = strftime('%z', localtime);   # e.g. +0100 or -0530
  $z =~ s/(\+|-)(\d{2})(\d{2})/$1$2:$3/;
  return $z;
}

sub normalize_prices_doc {
  my ($payload) = @_;

  my $rows = $payload->{prices};
  $rows = $payload->{rows} if !defined $rows;
  $rows ||= [];

  my @sorted = sort {
    ($a->{start_timestamp} // '') cmp ($b->{start_timestamp} // '')
  } grep { ref $_ eq 'HASH' && $_->{start_timestamp} } @$rows;

  my @out = map { _norm_one_block($_) } @sorted;

  my $pub = $payload->{publication_timestamp};
  if (!defined $pub || $pub eq '') {
    $pub = strftime('%Y-%m-%dT%H:%M:%S', localtime) . _tz_offset_colon();
  }

  return {
    publication_timestamp => $pub,
    prices                => \@out,
  };
}

sub write_json_file {
  my ($path, $doc) = @_;
  my $json = JSON::PP->new->canonical(1)->pretty(1)->encode($doc);
  open my $fh, '>', $path or die "Cannot write $path: $!";
  print $fh $json;
  close $fh;
  chmod 0640, $path;
}
# --------------------------------------

my $ok = eval {
  my $cfg = load_cfg();

  my ($link_status, $link_url) = try_ensure_linked($cfg);
  if ($link_status eq 'not_signed_in') {
    print encode_json({ error => 'not_signed_in', message => 'User not signed in. Please sign in via the plugin UI.' });
    return 1;
  }
  if ($link_status eq 'link_required') {
    print encode_json({
      error => 'link_required',
      message => 'EMS is not linked to customer account. Redirect customer to linking flow.',
      linking_process_redirect_uri => $link_url,
    });
    return 1;
  }
  if ($link_status eq 'error') {
    my (undef, undef, $err) = try_ensure_linked($cfg);
    print encode_json({ error => 'link_check_failed', message => $err // 'Unknown error checking link status' });
    return 1;
  }

  my ($start_iso, $end_iso) = build_scheduled_window();

  my $access = ensure_access_token($cfg);
  my ($payload, $source) = fetch_window($cfg, $access, $start_iso, $end_iso);

  # Defensive swap if fetch_window returns reversed values
  if (!defined $payload || ref($payload) ne 'HASH') {
    if (defined $source && ref($source) eq 'HASH') {
      ($payload, $source) = ($source, $payload);
    }
  }
  unless (defined $payload && ref($payload) eq 'HASH') {
    my $ptype = defined $payload ? ref($payload) || 'SCALAR' : 'UNDEF';
    my $stype = defined $source  ? ref($source)  || 'SCALAR' : 'UNDEF';
    my $msg = "Unexpected response from fetch_window: payload_type=$ptype, source_type=$stype";
    LOGERR($msg);
    print encode_json({ error => 'invalid_fetch_response', message => $msg });
    return 1;
  }

  # Normalize for file + HTTP response
  my $norm = normalize_prices_doc($payload);

  # Write normalized latest file
  my $latest = File::Spec->catfile($lbpdatadir, 'tariffs_latest.json');
  write_json_file($latest, $norm);

  # Optional: calendar-day file based on first interval
  if (@{ $norm->{prices} // [] }) {
    my $day = substr($norm->{prices}[0]{start_timestamp} // '', 0, 10);
    if ($day && $day =~ /^\d{4}-\d{2}-\d{2}$/) {
      my $byday = File::Spec->catfile($lbpdatadir, "tariffs_${day}.json");
      write_json_file($byday, $norm);
    }
  }

  # Publish (kept as-is; if you need normalized for MQTT, we can switch this too)
  eval { publish_tariffs_to_mqtt($cfg, $payload, $source, $start_iso, $end_iso); 1 };

  # Return normalized data to the browser as well
  my $prices = $norm->{prices} // [];
  my $out = {
    from                    => $start_iso,
    to                      => $end_iso,
    source                  => $source // 'unknown',
    publication_timestamp   => $norm->{publication_timestamp},
    prices                  => $prices,              # new, normalized field
    rows                    => $prices,              # backward-compatible alias
    interval_count          => scalar(@$prices),
  };
  print JSON::PP->new->canonical(1)->encode($out);
  return 1;
};

if (!$ok) {
  my $err = $@ // 'Unknown exception';
  eval {
    my $logfile = File::Spec->catfile($lbpdatadir, 'fetch.log');
    if (open my $fh, '>>', $logfile) {
      print $fh scalar(localtime) . " - run_rolling_fetch CGI died: $err\n";
      close $fh;
    }
    1;
  };
  print encode_json({ error => 'internal_error', message => "$err" });
}

exit 0;
