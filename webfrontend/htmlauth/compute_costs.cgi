#!/usr/bin/perl
use strict;
use warnings;

use CGI::Carp qw(fatalsToBrowser);
use CGI;
use JSON::PP;
use File::Spec;
use POSIX qw(strftime);
use Time::Local qw(timegm);
use Time::Piece;
use FindBin;
use LoxBerry::System;
use LoxBerry::Log;

require "$FindBin::Bin/common.pl";

our ($lbpdatadir, $lbpurl, $lbptemplatedir, $lbplogdir);

# --------------------------
# CGI and logging setup
# --------------------------
my $q = CGI->new;
my $nopublish = ($q->param('nopublish') // '') eq '1' ? 1 : 0;

my $log = LoxBerry::Log->new(
  name      => 'compute',
  filename  => "$lbplogdir/compute.log",
  append    => 1,
  loglevel  => 6,
  addtime   => 1,
  stderr    => 0,
  nosession => 1,
);

LOGSTART("compute_costs.cgi started");

# --------------------------
# Helpers
# --------------------------
sub _tz_offset_colon {
  my $z = strftime('%z', localtime);   # e.g. +0100 or -0530
  $z =~ s/(\+|-)(\d{2})(\d{2})/$1$2:$3/;
  return $z;
}

sub _iso_to_epoch {
  my ($iso) = @_;
  return undef unless defined $iso;
  # Accept formats with Z or ±HH:MM
  if ($iso =~ /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(Z|([+\-])(\d{2}):(\d{2}))$/) {
    my ($Y,$M,$D,$h,$m,$s,$z,$sign,$oh,$om) = ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10);
    # Build a UTC epoch from local components then subtract offset to get real UTC
    my $utc_epoch = timegm($s+0, $m+0, $h+0, $D+0, $M-1, $Y-1900);
    my $offset_secs = 0;
    if (defined $z && $z ne 'Z') {
      $offset_secs = ($oh * 3600) + ($om * 60);
      $offset_secs *= ($sign eq '+') ? 1 : -1;
    }
    return $utc_epoch - $offset_secs;
  }
  return undef;
}

sub _norm_unit_name {
  my ($u) = @_;
  return 'CHF_kWh' if defined $u && lc($u) eq 'chf_kwh';
  return 'CHF_M'   if defined $u && lc($u) eq 'chf_m';
  return 'CHF_kWh' if defined $u && lc($u) =~ /^chf[_-]?kwh$/;
  return 'CHF_M'   if defined $u && lc($u) =~ /^chf[_-]?m$/;
  return $u // 'CHF_kWh';
}

sub _value_for_unit {
  my ($arr, $unit) = @_;
  $arr ||= [];
  for my $e (@$arr) {
    next unless ref($e) eq 'HASH';
    my $u = _norm_unit_name($e->{unit});
    if ($u eq $unit) {
      my $v = $e->{value};
      $v = 0 unless defined $v;
      return $v + 0;
    }
  }
  return 0;
}

# Calculate total CHF_kWh for an interval row:
# Prefer electricity+grid+regional; else integrated+regional if electricity/grid missing or both 0.
sub _interval_total_chf_kwh {
  my ($row) = @_;
  return 0 unless $row && ref($row) eq 'HASH';
  my $e_kwh = _value_for_unit($row->{electricity}, 'CHF_kWh');
  my $g_kwh = _value_for_unit($row->{grid}, 'CHF_kWh');
  my $r_kwh = _value_for_unit($row->{regional_fees}, 'CHF_kWh');
  my $i_kwh = _value_for_unit($row->{integrated}, 'CHF_kWh');

  if ($e_kwh > 0 || $g_kwh > 0) {
    return ($e_kwh + $g_kwh + $r_kwh);
  } else {
    return ($i_kwh + $r_kwh);
  }
}

# --------------------------
# Load config and latest tariffs
# --------------------------
my $cfg = load_cfg();

my $latest_path = File::Spec->catfile($lbpdatadir, 'tariffs_latest.json');
my $doc;
eval {
  open my $fh, '<', $latest_path or die "Cannot read $latest_path: $!";
  local $/ = undef;
  my $raw = <$fh>;
  close $fh;
  $doc = decode_json($raw);
  1;
} or do {
  my $err = $@ || 'Unknown error';
  LOGERR("Failed to load tariffs_latest.json: $err");
  print $q->header('application/json; charset=utf-8');
  print JSON::PP->new->canonical(1)->encode({ error => 'no_latest_tariffs', message => "Cannot read $latest_path: $err" });
  exit 0;
};

my $rows = $doc->{prices} // $doc->{rows} // [];
my $pub_ts = $doc->{publication_timestamp} // (strftime('%Y-%m-%dT%H:%M:%S', localtime) . _tz_offset_colon());

# --------------------------
# Build intervals (15-minute)
# --------------------------
my @intervals;
for my $r (@$rows) {
  next unless ref($r) eq 'HASH';
  my $start = $r->{start_timestamp};
  my $end   = $r->{end_timestamp};

  my $interval = {
    start_timestamp => $start,
    end_timestamp   => $end,
    # Keep original blocks, but ensure order and units normalized
    electricity     => [
      { unit => 'CHF_M',   value => _value_for_unit($r->{electricity}, 'CHF_M') },
      { unit => 'CHF_kWh', value => _value_for_unit($r->{electricity}, 'CHF_kWh') },
    ],
    grid            => [
      { unit => 'CHF_M',   value => _value_for_unit($r->{grid}, 'CHF_M') },
      { unit => 'CHF_kWh', value => _value_for_unit($r->{grid}, 'CHF_kWh') },
    ],
    integrated      => [
      { unit => 'CHF_M',   value => _value_for_unit($r->{integrated}, 'CHF_M') },
      { unit => 'CHF_kWh', value => _value_for_unit($r->{integrated}, 'CHF_kWh') },
    ],
    regional_fees   => [
      { unit => 'CHF_M',   value => _value_for_unit($r->{regional_fees}, 'CHF_M') },
      { unit => 'CHF_kWh', value => _value_for_unit($r->{regional_fees}, 'CHF_kWh') },
    ],
    total_chf_kwh   => _interval_total_chf_kwh($r),
  };

  push @intervals, $interval;
}

# --------------------------
# Build hourly averages
# --------------------------
# Group intervals by their hour (epoch-aligned)
my %hour_buckets; # key: hour_start_epoch, value: { hour_start => ISO, totals => [ ... ] }
for my $it (@intervals) {
  my $start_iso = $it->{start_timestamp};
  my $epoch = _iso_to_epoch($start_iso);
  next unless defined $epoch;
  my $hour_epoch = int($epoch / 3600) * 3600;

  # Build local hour_start ISO with timezone offset
  my $hs_local = strftime('%Y-%m-%dT%H:00:00', localtime($hour_epoch));
  my $off = strftime('%z', localtime($hour_epoch)); $off =~ s/^([+\-])(\d{2})(\d{2})$/$1$2:$3/;
  my $hour_iso = $hs_local . $off;

  $hour_buckets{$hour_epoch} ||= { hour_start => $hour_iso, totals => [] };
  push @{ $hour_buckets{$hour_epoch}{totals} }, ($it->{total_chf_kwh} // 0) + 0;
}

# Calculate avg/min/max per hour
my @hourly = sort {
  ($a->{hour_start} // '') cmp ($b->{hour_start} // '')
} map {
  my $hour_start = $hour_buckets{$_}{hour_start};
  my $totals     = $hour_buckets{$_}{totals} || [];
  my $count      = scalar(@$totals);
  my ($sum, $min, $max) = (0, undef, undef);
  for my $v (@$totals) {
    $sum += $v;
    $min = (!defined $min || $v < $min) ? $v : $min;
    $max = (!defined $max || $v > $max) ? $v : $max;
  }
  my $avg = $count > 0 ? ($sum / $count) : undef;
  {
    hour_start    => $hour_start,
    intervals     => $count,
    avg_total_chf => (defined $avg ? 0.0 + $avg : undef),
    min_total_chf => (defined $min ? 0.0 + $min : undef),
    max_total_chf => (defined $max ? 0.0 + $max : undef),
  }
} keys %hour_buckets;

# --------------------------
# Build 24-hour relative forecast (now + 0..23h)
# --------------------------
my %hour_epoch_map = map {
  my $e = _iso_to_epoch($_->{hour_start});
  my $he = (defined $e) ? int($e / 3600) * 3600 : undef;
  (defined $he) ? ($he => $_) : ();
} @hourly;

my @relative;
for my $off (0..23) {
  my $t = time + $off * 3600;
  my $target_hour_epoch = int($t / 3600) * 3600;
  my $entry = $hour_epoch_map{$target_hour_epoch};
  my ($hs, $val, $cnt);
  if ($entry) {
    $hs  = $entry->{hour_start};
    $val = $entry->{avg_total_chf};
    $cnt = $entry->{intervals};
  } else {
    my $hs_local = strftime('%Y-%m-%dT%H:00:00', localtime($t));
    my $offstr = strftime('%z', localtime($t)); $offstr =~ s/^([+\-])(\d{2})(\d{2})$/$1$2:$3/;
    $hs = $hs_local . $offstr;
    $val = undef;
    $cnt = 0;
  }
  push @relative, { offset => $off, hour_start => $hs, intervals => $cnt, avg_total_chf => $val };
}

# --------------------------
# MQTT publish (unless nopublish=1)
# --------------------------
my $published_intervals = 0;
my $published_hourly    = 0;
if (!$nopublish && $cfg->{mqtt_enabled}) {
  my $intervals_msg = {
    publication_timestamp => $pub_ts,
    interval_count        => scalar(@intervals),
    intervals             => \@intervals,
  };
  my $hourly_msg = {
    publication_timestamp => $pub_ts,
    hours_count           => scalar(@hourly),
    hourly                => \@hourly,
    relative              => \@relative,
  };

  eval {
    publish_mqtt($cfg, $cfg->{mqtt_topic_intervals}, $intervals_msg);
    $published_intervals = 1;
    1;
  } or do {
    LOGERR("MQTT publish intervals failed: $@");
  };

  eval {
    publish_mqtt($cfg, $cfg->{mqtt_topic_hourly}, $hourly_msg);
    $published_hourly = 1;
    1;
  } or do {
    LOGERR("MQTT publish hourly failed: $@");
  };
}

# --------------------------
# Output response
# --------------------------
print $q->header('application/json; charset=utf-8');

my $out = {
  publication_timestamp => $pub_ts,
  interval_count        => scalar(@intervals),
  hours_count           => scalar(@hourly),
  intervals             => \@intervals,
  hourly                => \@hourly,
  relative              => \@relative,
  mqtt_published        => {
    intervals => JSON::PP::bool($published_intervals ? 1 : 0),
    hourly    => JSON::PP::bool($published_hourly    ? 1 : 0),
  },
};

print JSON::PP->new->pretty(1)->encode($out);

LOGEND("compute_costs.cgi finished");

exit 0;
