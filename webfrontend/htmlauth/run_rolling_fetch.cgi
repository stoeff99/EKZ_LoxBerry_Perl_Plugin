#!/usr/bin/perl
use strict;
use warnings;

use CGI::Carp qw(fatalsToBrowser);
use CGI;
use JSON::PP;
use File::Spec;
use POSIX qw(strftime);
use Tie::IxHash;           # <-- preserve object key order
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

# Return a TIED ordered hashref with keys in the exact order we want
sub _ordered_block {
  my ($p) = @_;
  tie my %o, 'Tie::IxHash';
  %o = (
    start_timestamp => $p->{start_timestamp},
    end_timestamp   => $p->{end_timestamp},
    electricity     => _ordered_cost_array($p->{electricity}),
    grid            => _ordered_cost_array($p->{grid}),
    integrated      => _ordered_cost_array($p->{integrated}),
    regional_fees   => _ordered_cost_array($p->{regional_fees}),
  );
  return \%o;
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

  my @out = map { _ordered_block($_) } @sorted;

  my $pub = $payload->{publication_timestamp};
  if (!defined $pub || $pub eq '') {
    $pub = strftime('%Y-%m-%dT%H:%M:%S', localtime) . _tz_offset_colon();
  }

  # Top-level document with ordered keys too
  tie my %doc, 'Tie::IxHash';
  %doc = (
    publication_timestamp => $pub,
    prices                => \@out,
  );
  return \%doc;
}

sub write_json_file {
  my ($path, $doc) = @_;
  my $json = JSON::PP->new->pretty(1)->encode($doc);   # no canonical => preserve Tie::IxHash order
  open my $fh, '>', $path or die "Cannot write $path: $!";
  print $fh $json;
  close $fh;
  chmod 0640, $path;
}
# --------------------------------------

# Helper: calendar-day equality (localtime)
sub same_calendar_day {
  my ($t1, $t2) = @_;
  return 0 unless defined $t1 && defined $t2;
  my @a = localtime($t1);
  my @b = localtime($t2);
  return ($a[5] == $b[5] && $a[4] == $b[4] && $a[3] == $b[3]); # year, month, mday
}

my $ok = eval {
  my $cfg = load_cfg();

  # allow force via query param: run_rolling_fetch.cgi?force=1 will bypass schedule checks
  my $force = ($q->param('force') // '') eq '1' ? 1 : 0;

  # Enforce schedule guard BEFORE performing any fetching to avoid accidental downloads.
  # Read configured schedule (values: '1','2','12','24') - default to '1' (18:00) if unset.
  my $schedule = $cfg->{fetch_schedule} // '1';

  # Path to record last successful fetch
  my $last_file = File::Spec->catfile($lbpdatadir, 'last_fetch.json');

  if (!$force) {
    # Only apply the "already fetched today" guard for strictly once-per-day schedules.
    # For other schedules (2, 12, 24) we rely on the hour-of-day check below.
    if (($schedule // '') eq '1') {
      if (-f $last_file) {
        if (open my $lf, '<', $last_file) {
          local $/ = undef;
          my $raw = <$lf>;
          close $lf;
          my $j = eval { decode_json($raw) } || {};
          my $last_ts = $j->{last_success_epoch};
          if (defined $last_ts && same_calendar_day(time, $last_ts)) {
            LOGINF("Skipped fetch: already fetched today (once-per-day schedule; last_success_epoch=%d)", $last_ts);
            print JSON::PP->new->encode({ skipped => JSON::PP::true, reason => 'already_fetched_today' });
            return 1;
          }
        } else {
          LOGWARN("Could not open last_fetch file '%s' for reading: %s", $last_file, $!);
        }
      }
    }

    # Ensure current local hour matches configured schedule
    my $hour = (localtime(time))[2]; # 0..23
    my $allowed = 0;
    if ($schedule eq '1') {
      $allowed = ($hour == 18) ? 1 : 0;
    } elsif ($schedule eq '2') {
      $allowed = ($hour == 6 || $hour == 18) ? 1 : 0;
    } elsif ($schedule eq '12') {
      $allowed = ($hour % 2 == 0) ? 1 : 0; # even hours
    } elsif ($schedule eq '24') {
      $allowed = 1; # any hour
    } else {
      $allowed = 0; # unknown/disabled
    }

    unless ($allowed) {
      LOGINF("Skipped fetch: not scheduled now (hour=$hour schedule=$schedule)");
      print JSON::PP->new->encode({ skipped => JSON::PP::true, reason => 'not_scheduled_now', hour => $hour, schedule => $schedule });
      return 1;
    }
  } else {
    LOGINF("Force fetch requested via ?force=1");
  }

  # ---- existing link / auth checks ----
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

  # Build window and fetch data from EMS
  my ($start_iso, $end_iso) = build_scheduled_window();

  my $access = ensure_access_token($cfg);
  my ($payload, $source) = fetch_window($cfg, $access, $start_iso, $end_iso);

  if (!defined $payload || ref($payload) ne 'HASH') {
    if (defined $source && ref($source) eq 'HASH') {
      ($payload, $source) = ($source, $payload);
    }
  }
  unless (defined $payload && ref($payload) eq 'HASH') {
    my $msg = "Unexpected response from fetch_window";
    LOGERR($msg);
    print encode_json({ error => 'invalid_fetch_response', message => $msg });
    return 1;
  }

  # Normalize for file + HTTP response
  my $norm = normalize_prices_doc($payload);

  # Write normalized latest file
  my $latest = File::Spec->catfile($lbpdatadir, 'tariffs_latest.json');
  write_json_file($latest, $norm);

  # Record last successful fetch (epoch) so next runs on same calendar day can be skipped
  eval {
    my $last = { last_success_epoch => time() };
    my $lfh = File::Spec->catfile($lbpdatadir, 'last_fetch.json');
    if (open my $fh, '>', $lfh) {
      print $fh JSON::PP->new->canonical(1)->encode($last);
      close $fh;
      chmod 0640, $lfh;
    }
    1;
  };

  # Optional: calendar-day file based on first interval
  if (@{ $norm->{prices} // [] }) {
    my $day = substr($norm->{prices}[0]{start_timestamp} // '', 0, 10);
    if ($day && $day =~ /^\d{4}-\d{2}-\d{2}$/) {
      my $byday = File::Spec->catfile($lbpdatadir, "tariffs_${day}.json");
      write_json_file($byday, $norm);
    }
  }

  # Publish using raw payload (leave as-is). If you need normalized, call publish with $norm.
  eval { publish_tariffs_to_mqtt($cfg, $payload, $source, $start_iso, $end_iso); 1 };

  # Trigger computed publishes (intervals + hourly) via compute_costs.cgi (without nopublish)
  eval {
    my $compute = File::Spec->catfile($FindBin::Bin, 'compute_costs.cgi');
    # run compute_costs CGI directly to publish computed topics (no 'nopublish')
    my $out = qx{/usr/bin/perl $compute};
    LOGINF("compute_costs.cgi invoked to publish intervals/hourly.");
    1;
  };

  # Return normalized data (rows alias kept)
  my $prices = $norm->{prices} // [];
  my $out = {
    from                  => $start_iso,
    to                    => $end_iso,
    source                => $source // 'unknown',
    publication_timestamp => $norm->{publication_timestamp},
    prices                => $prices,
    rows                  => $prices,
    interval_count        => scalar(@$prices),
  };
  print JSON::PP->new->pretty(1)->encode($out);  # preserve Tie::IxHash order
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
