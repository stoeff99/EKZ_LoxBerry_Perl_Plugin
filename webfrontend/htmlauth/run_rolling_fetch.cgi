#!/usr/bin/perl
use strict;
use warnings;

# Show Perl runtime errors in the browser for debugging (remove later if desired)
use CGI::Carp qw(fatalsToBrowser);

use CGI;
use JSON::PP;
use File::Spec;
use POSIX qw(strftime);
use LoxBerry::System;
use LoxBerry::Log;   # <-- REQUIRED for LOGSTART/LOGINF/LOGERR
use FindBin;
require "$FindBin::Bin/common.pl";

# Include $lbplogdir here
our ($lbpdatadir, $lbpurl, $lbptemplatedir, $lbplogdir);

my $q = CGI->new;
print $q->header('application/json; charset=utf-8');

# Initialize plugin logger
my $log = LoxBerry::Log->new(
  name      => 'fetch',
  filename  => "$lbplogdir/fetch.log",
  append    => 1,
  loglevel  => 6,   # INFO
  addtime   => 1,
  stderr    => 0,
  nosession => 1,
);

LOGSTART("run_rolling_fetch started");

# -------------------------
# Normalization helpers
# -------------------------
sub _norm_unit_name {
  my ($u) = @_;
  return 'CHF_kWh' if defined $u && lc($u) eq 'chf_kwh';
  return 'CHF_M'   if defined $u && lc($u) eq 'chf_m';
  # fallbacks (sloppy variants)
  return 'CHF_kWh' if defined $u && lc($u) =~ /^chf[_-]?kwh$/;
  return 'CHF_M'   if defined $u && lc($u) =~ /^chf[_-]?m$/;
  return $u // 'CHF_kWh';
}

sub _ordered_cost_array {
  my ($arr) = @_;
  $arr ||= [];
  my (%v);
  for my $e (@$arr) {
    next unless ref $e eq 'HASH';
    my $unit = _norm_unit_name($e->{unit});
    $v{$unit} = $e->{value} + 0 if exists $e->{value};
  }
  # fixed order and stable object key order
  return [
    { unit => 'CHF_M',   value => ($v{'CHF_M'}   // 0) + 0 },
    { unit => 'CHF_kWh', value => ($v{'CHF_kWh'} // 0) + 0 },
  ];
}

sub _norm_one_block {
  my ($p) = @_;
  my %o;
  $o{start_timestamp} = $p->{start_timestamp};
  $o{end_timestamp}   = $p->{end_timestamp};
  $o{electricity}     = _ordered_cost_array($p->{electricity});
  $o{grid}            = _ordered_cost_array($p->{grid});
  $o{integrated}      = _ordered_cost_array($p->{integrated});
  $o{regional_fees}   = _ordered_cost_array($p->{regional_fees});
  return \%o;
}

sub normalize_prices_doc {
  my ($payload) = @_;

  # Accept either {prices=>[]} or legacy {rows=>[]}
  my $rows = $payload->{prices};
  $rows = $payload->{rows} if !defined $rows;

  $rows ||= [];

  # Sort by start_timestamp ascending (ISO8601 sorts lexically)
  my @sorted = sort {
    ($a->{start_timestamp} // '') cmp ($b->{start_timestamp} // '')
  } grep { ref $_ eq 'HASH' && $_->{start_timestamp} } @$rows;

  my @out = map { _norm_one_block($_) } @sorted;

  my $pub = $payload->{publication_timestamp};
  if (!defined $pub || $pub eq '') {
    # Local time with offset +HH:MM
    my $t = time;
    my $lt = localtime($t);
    my $gmt = gmtime($t);
    my $off = (timelocal(0,(localtime)[1,2,3,4,5]) - timelocal(0,(gmtime)[1,2,3,4,5]))/3600; # rough offset hours
    my $sign = $off >= 0 ? '+' : '-';
    my $abs  = abs($off);
    my $hh   = int($abs);
    my $mm   = int(($abs - $hh) * 60);
    my $ts   = strftime('%Y-%m-%dT%H:%M:%S', localtime($t)) . sprintf('%s%02d:%02d', $sign, $hh, $mm);
    $pub = $ts;
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

# -------------------------

# Run the main logic inside an eval to capture any die() and return JSON error details
my $ok = eval {
    my $cfg = load_cfg();

    # Ensure linked; if not, instruct user to link first (return structured JSON)
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

    # Build window: now 18:00 local → +24h
    my ($start_iso, $end_iso) = build_scheduled_window();

    # Try customer tariffs first, fallback to public tariffs
    my $access = ensure_access_token($cfg);      # may die -> caught by eval
    my ($payload, $source) = fetch_window($cfg, $access, $start_iso, $end_iso);

    # Defensive normalization: ensure $payload is a HASH ref and $source is a string label
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

    # Normalize to the required structure and write JSON files
    my $norm = normalize_prices_doc($payload);

    my $latest = File::Spec->catfile($lbpdatadir, 'tariffs_latest.json');
    write_json_file($latest, $norm);

    # Optional: write calendar-day sidecar file tariffs_YYYY-MM-DD.json (based on first interval day)
    if (@{ $norm->{prices} // [] }) {
      my $day = substr($norm->{prices}[0]{start_timestamp} // '', 0, 10); # YYYY-MM-DD
      if ($day && $day =~ /^\d{4}-\d{2}-\d{2}$/) {
        my $byday = File::Spec->catfile($lbpdatadir, "tariffs_${day}.json");
        write_json_file($byday, $norm);
      }
    }

    # Still call the original publish (uses the raw payload to avoid breaking existing consumers)
    eval { publish_tariffs_to_mqtt($cfg, $payload, $source, $start_iso, $end_iso); 1 };

    # Return JSON to caller (original shape kept for compatibility)
    my $out = {
      from           => $start_iso,
      to             => $end_iso,
      source         => $source // 'unknown',
      rows           => $payload->{rows} // $payload->{prices} // [],
      interval_count => $payload->{interval_count} // 0,
    };
    print encode_json($out);
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
