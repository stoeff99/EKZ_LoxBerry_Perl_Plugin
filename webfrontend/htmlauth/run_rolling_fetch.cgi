#!/usr/bin/perl
use strict;
use warnings;
use Time::Piece;
use CGI::Carp qw(fatalsToBrowser);
use CGI;
use JSON::PP;
use File::Spec;
use POSIX qw(strftime);
use Tie::IxHash;
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

sub _tz_offset_colon {
  my $z = strftime('%z', localtime);   # e.g. +0100 or -0530
  $z =~ s/(\+|-)(\d{2})(\d{2})/$1$2:$3/;
  return $z;
}

my $ok = eval {
  my $cfg = load_cfg();
  my $force    = ($q->param('force') // '') eq '1' ? 1 : 0;
  my $wantToday= ($q->param('today') // '') eq '1' ? 1 : 0;
  my $schedule = $cfg->{fetch_schedule} // '1';

  my $now_epoch = time();
  my $now_hour  = (localtime($now_epoch))[2];

  # Schedule guard
  if (!$force) {
    my $allowed = 0;
    if    ($schedule eq '1')  { $allowed = ($now_hour >= 18) ? 1 : 0; }
    elsif ($schedule eq '2')  { $allowed = ($now_hour == 6 || $now_hour >= 18) ? 1 : 0; }
    elsif ($schedule eq '12') { $allowed = ($now_hour % 2 == 0) ? 1 : 0; }
    elsif ($schedule eq '24') { $allowed = 1; }
    else                      { $allowed = 0; }

    unless ($allowed) {
      LOGINF("Skipped fetch: not scheduled now (hour=$now_hour schedule=$schedule)");
      print JSON::PP->new->encode({ skipped => JSON::PP::true, reason => 'not_scheduled_now', hour => $now_hour, schedule => $schedule });
      return 1;
    }
  } else {
    LOGINF("Force fetch requested via ?force=1");
  }

  # Decide window: today vs next-day
  my ($start_iso, $end_iso);
  my $now = localtime($now_epoch);
  if ($wantToday || ($force && $now_hour < 18) || (!$force && $now_hour < 18)) {
    LOGINF("Building TODAY window (00:00..24:00 local)");
    my $start = Time::Piece->strptime($now->strftime('%Y-%m-%d') . ' 00:00:00', '%Y-%m-%d %H:%M:%S');
    my $end   = $start + 24*3600;
    my $off = $now->strftime('%z'); $off =~ s/^([+-])(\d{2})(\d{2})$/$1$2:$3/;
    $start_iso = $start->strftime('%Y-%m-%dT%H:%M:%S') . $off;
    $end_iso   = $end->strftime('%Y-%m-%dT%H:%M:%S') . $off;
  } else {
    LOGINF("Building NEXT-DAY window (tomorrow 00:00..24:00 local)");
    ($start_iso, $end_iso) = build_scheduled_window();
    if (!$force && $now_hour < 18) {
      LOGINF("Skipping next-day fetch: not published yet (hour=%d)", $now_hour);
      print JSON::PP->new->encode({ skipped => JSON::PP::true, reason => 'not_published_yet', hour => $now_hour });
      return 1;
    }
  }

  # Link/auth checks
  my ($link_status, $link_url, $link_err) = try_ensure_linked($cfg);
  if ($link_status eq 'not_signed_in') {
    print encode_json({ error => 'not_signed_in', message => 'Sign in via plugin UI.' }); return 1;
  }
  if ($link_status eq 'link_required') {
    print encode_json({ error => 'link_required', linking_process_redirect_uri => $link_url }); return 1;
  }
  if ($link_status eq 'error') {
    print encode_json({ error => 'link_check_failed', message => ($link_err // 'Unknown error') }); return 1;
  }

  # Fetch
  my $access = ensure_access_token($cfg);
  my ($payload, $source) = fetch_window($cfg, $access, $start_iso, $end_iso);

  unless (defined $payload && ref($payload) eq 'HASH') {
    print encode_json({ error => 'invalid_fetch_response', message => 'Unexpected response from fetch_window' });
    return 1;
  }

  # Guard: only write complete payloads; for next-day after 18:00 use fallback if needed
  my $is_complete = payload_is_complete($payload, $start_iso, $end_iso);
  if (!$is_complete && $now_hour >= 18 && !$wantToday) {
    my $fallback = $cfg->{fallback_tariff_name} // '';
    if ($fallback ne '') {
      LOGINF("Next-day payload incomplete after 18:00. Attempting fallback tariff_name=%s", $fallback);
      my $fb = fetch_public_tariffs_by_name($cfg, $start_iso, $end_iso, $fallback);
      if (defined $fb && payload_is_complete($fb, $start_iso, $end_iso)) {
        $payload = $fb; $source = 'public';
        LOGINF("Fallback tariff applied.");
      } else {
        print encode_json({ error => 'incomplete_nextday', message => 'Next-day incomplete; fallback also incomplete. Keeping previous file.' });
        return 1;
      }
    } else {
      print encode_json({ error => 'incomplete_nextday', message => 'Next-day incomplete and no fallback configured. Keeping previous file.' });
      return 1;
    }
  } elsif (!$is_complete) {
    print encode_json({ error => 'incomplete_payload', message => 'Incomplete data for requested window. Keeping previous file.' });
    return 1;
  }

  # Also dump raw payload for diagnostics
  eval {
    my $raw_path = File::Spec->catfile($lbpdatadir, 'tariffs_raw_latest.json');
    my $tmp = "$raw_path.tmp.$$";
    open my $rfh, '>', $tmp or die "Cannot write $tmp: $!";
    print $rfh encode_json($payload); close $rfh; chmod 0640, $tmp;
    rename $tmp, $raw_path or die "Cannot rename $tmp to $raw_path: $!";
    1;
  };

  # Normalize and write
  my $rows = $payload->{rows} // $payload->{prices} // [];
  tie my %doc, 'Tie::IxHash';
  %doc = (
    publication_timestamp => strftime('%Y-%m-%dT%H:%M:%S', localtime) . _tz_offset_colon(),
    prices                => $rows,
  );
  my $latest = File::Spec->catfile($lbpdatadir, 'tariffs_latest.json');
  my $tmpf   = "$latest.tmp.$$";
  open my $fh, '>', $tmpf or die "Cannot write $tmpf: $!";
  print $fh JSON::PP->new->pretty(1)->encode(\%doc);
  close $fh; chmod 0640, $tmpf; rename $tmpf, $latest;

  # Record last successful fetch
  eval {
    my $lfh = File::Spec->catfile($lbpdatadir, 'last_fetch.json');
    open my $f, '>', $lfh; print $f JSON::PP->new->canonical(1)->encode({ last_success_epoch => time() }); close $f; chmod 0640, $lfh;
    1;
  };

  # Per-day file
  if (@$rows) {
    my $day = substr($rows->[0]{start_timestamp} // '', 0, 10);
    if ($day && $day =~ /^\d{4}-\d{2}-\d{2}$/) {
      my $byday = File::Spec->catfile($lbpdatadir, "tariffs_${day}.json");
      open my $bf, '>', "$byday.tmp.$$"; print $bf JSON::PP->new->pretty(1)->encode(\%doc); close $bf;
      chmod 0640, "$byday.tmp.$$"; rename "$byday.tmp.$$", $byday;
    }
  }

  # Publish raw payload to MQTT
  eval { publish_tariffs_to_mqtt($cfg, $payload, $source, $start_iso, $end_iso); 1 };

  # Trigger compute publish
  eval {
    my $compute = File::Spec->catfile($FindBin::Bin, 'compute_costs.cgi');
    my $out = qx{/usr/bin/perl $compute};
    LOGINF("compute_costs.cgi invoked to publish intervals/hourly.");
    1;
  };

  # Response
  print JSON::PP->new->pretty(1)->encode({
    from           => $start_iso,
    to             => $end_iso,
    source         => $source // 'unknown',
    interval_count => scalar(@$rows),
    prices         => $rows,
    rows           => $rows,
  });
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
