#!/usr/bin/perl
use strict;
use warnings;

use CGI::Carp qw(fatalsToBrowser);
use CGI;
use JSON::PP;
use File::Spec;
use POSIX qw(strftime);
use Time::Local;
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

LOGSTART("run_raw_fetch started");

sub tz_offset_colon {
  my $z = strftime('%z', localtime);   # +0100
  $z =~ s/(\+|-)(\d{2})(\d{2})/$1$2:$3/;
  return $z;
}

# Build midnight->next-midnight local calendar window (NOT rolling)
sub build_calendar_day_window {
  my @lt = localtime();
  my ($Y,$m,$d) = ($lt[5]+1900, $lt[4]+1, $lt[3]);
  my $tz = tz_offset_colon();

  my $start_iso = sprintf('%04d-%02d-%02dT00:00:00%s', $Y, $m, $d, $tz);

  # next day (let the system handle month/year rollover)
  my $t = timelocal(0,0,0,$d,$m-1,$Y-1900) + 24*3600;
  my @n = localtime($t);
  my ($Y2,$m2,$d2) = ($n[5]+1900, $n[4]+1, $n[3]);
  my $end_iso = sprintf('%04d-%02d-%02dT00:00:00%s', $Y2, $m2, $d2, $tz);

  return ($start_iso, $end_iso);
}

my $ok = eval {
  my $cfg = load_cfg();

  # Ensure linked
  my ($link_status, $link_url, $link_err) = try_ensure_linked($cfg);
  if ($link_status eq 'not_signed_in') {
    print encode_json({ error => 'not_signed_in', message => 'Please sign in via the plugin UI.' });
    return 1;
  }
  if ($link_status eq 'link_required') {
    print encode_json({
      error => 'link_required',
      message => 'EMS is not linked. Complete linking first.',
      linking_process_redirect_uri => $link_url,
    });
    return 1;
  }
  if ($link_status eq 'error') {
    print encode_json({ error => 'link_check_failed', message => $link_err // 'Unknown link status error' });
    return 1;
  }

  # Get access token (may die)
  my $access = ensure_access_token($cfg);

  # Try to fetch RAW as provided by the host
  my ($payload, $source);

  # Path A: If common.pl exposes a raw fetcher, use it (no window params)
  if (defined &fetch_raw) {
    ($payload, $source) = fetch_raw($cfg, $access);
  }
  # Path B: If it exposes a daily/customer fetcher, try that
  elsif (defined &fetch_customer_daily) {
    ($payload, $source) = fetch_customer_daily($cfg, $access);
  }
  # Path C: Fallback to a plain calendar-day window (midnight -> midnight), not rolling
  else {
    my ($start_iso, $end_iso) = build_calendar_day_window();
    if (defined &fetch_window) {
      ($payload, $source) = fetch_window($cfg, $access, $start_iso, $end_iso);
    } else {
      die "No suitable fetch function found (need fetch_raw, fetch_customer_daily, or fetch_window)";
    }
  }

  # If some fetchers return reversed tuple, swap
  if (!defined $payload || ref($payload) ne 'HASH') {
    if (defined $source && ref($source) eq 'HASH') {
      ($payload, $source) = ($source, $payload);
    }
  }
  unless (defined $payload && ref($payload) eq 'HASH') {
    my $ptype = defined $payload ? ref($payload) || 'SCALAR' : 'UNDEF';
    my $stype = defined $source  ? ref($source)  || 'SCALAR' : 'UNDEF';
    my $msg = "Invalid fetch response: payload_type=$ptype, source_type=$stype";
    print encode_json({ error => 'invalid_fetch_response', message => $msg });
    return 1;
  }

  # Save EXACTLY what we received (pretty for readability; no sorting/normalization)
  my $out_file = File::Spec->catfile($lbpdatadir, 'tariffs_latest.json');
  my $json = JSON::PP->new->pretty(1)->encode($payload);
  open my $fh, '>', $out_file or die "Cannot write $out_file: $!";
  print $fh $json;
  close $fh;
  chmod 0640, $out_file;

  # Publish to MQTT using the same raw payload
  eval { publish_tariffs_to_mqtt($cfg, $payload, $source, undef, undef); 1 };

  # Return the RAW payload to the browser
  print $json;
  return 1;
};

if (!$ok) {
  my $err = $@ // 'Unknown exception';
  eval {
    my $logfile = File::Spec->catfile($lbpdatadir, 'fetch.log');
    if (open my $fh, '>>', $logfile) {
      print $fh scalar(localtime) . " - run_raw_fetch died: $err\n";
      close $fh;
    }
    1;
  };
  print encode_json({ error => 'internal_error', message => "$err" });
}

exit 0;
