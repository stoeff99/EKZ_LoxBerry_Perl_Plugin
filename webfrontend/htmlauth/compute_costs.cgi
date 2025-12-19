#!/usr/bin/perl
use strict;
use warnings;

use CGI;
use JSON::PP;
use File::Spec;
use Time::Local;
use FindBin;
use LoxBerry::System;
require "$FindBin::Bin/common.pl";

# SDK globals
our ($lbpdatadir, $lbpurl, $lbptemplatedir);

my $q = CGI->new;
print $q->header('application/json; charset=utf-8');

# Allow UI to compute without publishing (to avoid duplicate MQTT)
my $nopublish = ($q->param('nopublish') // '') ne '' ? 1 : 0;

# ---- helpers ----

sub read_latest_json {
  my $path = File::Spec->catfile($lbpdatadir, 'tariffs_latest.json');
  unless (-f $path) {
    return ($path, undef, { error => "not_found", message => "No tariffs_latest.json in $lbpdatadir" });
  }
  open my $fh, '<', $path or return ($path, undef, { error => "open_failed", message => "Cannot open $path: $!" });
  local $/ = undef;
  my $raw = <$fh>;
  close $fh;
  my $doc = eval { decode_json($raw) };
  if (!$doc) {
    return ($path, undef, { error => "invalid_json", message => "Could not parse tariffs_latest.json" });
  }
  return ($path, $doc, undef);
}

sub get_unit_value {
  my ($arr, $wanted) = @_;
  return 0 unless $arr && ref($arr) eq 'ARRAY';
  for my $e (@$arr) {
    next unless ref($e) eq 'HASH';
    my $u = $e->{unit};
    next unless defined $u;
    if (lc($u) eq lc($wanted)) {
      my $v = $e->{value};
      return 0 + ($v // 0);
    }
  }
  return 0;
}

sub days_in_month {
  my ($y, $m) = @_; # y=YYYY, m=1..12
  return 31 if $m =~ /^(1|3|5|7|8|10|12)$/;
  return 30 if $m =~ /^(4|6|9|11)$/;
  my $leap = ($y % 400 == 0) || ($y % 4 == 0 && $y % 100 != 0);
  return $leap ? 29 : 28;
}

sub parse_ymdh {
  my ($iso) = @_;
  my ($Y,$m,$d,$H) = $iso =~ /^(\d{4})-(\d{2})-(\d{2})T(\d{2})/;
  return ($Y+0,$m+0,$d+0,$H+0);
}

sub hour_start_from {
  my ($iso) = @_;
  my ($date,$time,$off) = $iso =~ /^([^T]+)T([^+\-Z]+)([+\-]\d{2}:\d{2}|Z)?$/;
  $off = '+00:00' if !defined $off || $off eq 'Z';
  my ($Y,$M,$D,$h) = parse_ymdh($iso);
  return sprintf('%04d-%02d-%02dT%02d:00:00%s', $Y,$M,$D,$h,$off);
}

sub kwh_total_for_block {
  my ($b) = @_;
  my $e_kwh = get_unit_value($b->{electricity},    'CHF_kWh');
  my $g_kwh = get_unit_value($b->{grid},           'CHF_kWh');
  my $r_kwh = get_unit_value($b->{regional_fees},  'CHF_kWh');
  if ($e_kwh || $g_kwh) {
    return $e_kwh + $g_kwh + $r_kwh;
  } else {
    my $i_kwh = get_unit_value($b->{integrated}, 'CHF_kWh');
    return $i_kwh + $r_kwh;
  }
}

sub monthly_M_total_for_block {
  my ($b) = @_;
  my $i_m = get_unit_value($b->{integrated},     'CHF_M');
  my $r_m = get_unit_value($b->{regional_fees},  'CHF_M');
  if ($i_m) {
    return $i_m + $r_m;
  } else {
    my $e_m = get_unit_value($b->{electricity},  'CHF_M');
    my $g_m = get_unit_value($b->{grid},         'CHF_M');
    return $e_m + $g_m + $r_m;
  }
}

# MQTT publish with mosquitto_pub fallback
sub mqtt_publish {
  my ($cfg, $topic, $payload_json) = @_;
  return 0 unless $cfg->{mqtt_enabled};

  my $host = $cfg->{mqtt_host} // 'localhost';
  my $port = $cfg->{mqtt_port} // 1883;
  my $user = $cfg->{mqtt_username} // '';
  my $pass = $cfg->{mqtt_password} // '';

  my $tmp = File::Spec->catfile($lbpdatadir, 'mqtt_payload.tmp.json');
  open my $tf, '>', $tmp or return 0;
  print $tf $payload_json;
  close $tf;

  my @cmd = ('mosquitto_pub', '-h', $host, '-p', $port, '-t', $topic, '-f', $tmp, '-r');
  if (defined $user && $user ne '') { push @cmd, ('-u', $user); }
  if (defined $pass && $pass ne '') { push @cmd, ('-P', $pass); }

  my $rc = system(@cmd);
  unlink $tmp;
  return $rc == 0 ? 1 : 0;
}

# ---- main ----

my $cfg = eval { load_cfg() } // {};

my ($src_path, $doc, $err) = read_latest_json();
if ($err) {
  print JSON::PP->new->encode($err);
  exit 0;
}

my $rows = $doc->{prices};
$rows = $doc->{rows} if !defined $rows;
$rows ||= [];

my @sorted = sort {
  ($a->{start_timestamp} // '') cmp ($b->{start_timestamp} // '')
} grep { ref $_ eq 'HASH' && $_->{start_timestamp} } @$rows;

my @intervals;
my %hour_groups;

for my $b (@sorted) {
  my $start = $b->{start_timestamp} // next;
  my $end   = $b->{end_timestamp}   // '';

  my ($Y,$M) = (parse_ymdh($start))[0,1];
  my $hours_in_month = days_in_month($Y,$M) * 24;

  my $kwh_total      = kwh_total_for_block($b);
  my $monthly_m      = monthly_M_total_for_block($b);
  my $fixed_per_hour = $hours_in_month ? ($monthly_m / $hours_in_month) : 0;
  my $sum_total      = $kwh_total + $fixed_per_hour;

  my $hour_key = hour_start_from($start);

  my $row = {
    start_timestamp  => $start,
    end_timestamp    => $end,
    chf_per_kwh_sum  => $kwh_total,
    chf_m_per_hour   => $fixed_per_hour,
    total_chf        => $sum_total,
    month_hours_used => $hours_in_month,
  };
  push @intervals, $row;

  $hour_groups{$hour_key} ||= { n => 0, kwh_sum => 0, fixed_sum => 0, total_sum => 0 };
  $hour_groups{$hour_key}{n}         += 1;
  $hour_groups{$hour_key}{kwh_sum}   += $kwh_total;
  $hour_groups{$hour_key}{fixed_sum} += $fixed_per_hour;
  $hour_groups{$hour_key}{total_sum} += $sum_total;
}

my @hourly;
for my $hk (sort keys %hour_groups) {
  my $g = $hour_groups{$hk};
  my $n = $g->{n} || 1;
  push @hourly, {
    hour_start          => $hk,
    avg_total_chf       => $g->{total_sum} / $n,
    avg_chf_per_kwh_sum => $g->{kwh_sum}   / $n,
    avg_chf_m_per_hour  => $g->{fixed_sum} / $n,
    intervals_count     => $n,
  };
}

my $pub_ts = $doc->{publication_timestamp} // '';

my $intervals_msg = {
  publication_timestamp => $pub_ts,
  interval_count        => scalar(@intervals),
  intervals             => \@intervals,
};
my $hourly_msg = {
  publication_timestamp => $pub_ts,
  hour_count            => scalar(@hourly),
  hourly                => \@hourly,
};

my $json_intervals = JSON::PP->new->canonical(1)->encode($intervals_msg);
my $json_hourly    = JSON::PP->new->canonical(1)->encode($hourly_msg);

# FIX: use dedicated topics, with backward-compatible fallback
my $topic_intervals = $cfg->{mqtt_topic_intervals}
                   // $cfg->{mqtt_topic_raw}
                   // 'ekz/ems/tariffs/intervals';
my $topic_hourly    = $cfg->{mqtt_topic_hourly}
                   // $cfg->{mqtt_topic_summary}
                   // 'ekz/ems/tariffs/hourly';

my ($pub_intervals_ok, $pub_hourly_ok) = (0, 0);
if (!$nopublish) {
  $pub_intervals_ok = mqtt_publish($cfg, $topic_intervals, $json_intervals) ? 1 : 0;
  $pub_hourly_ok    = mqtt_publish($cfg, $topic_hourly,    $json_hourly)    ? 1 : 0;

my $out = {
  source_file             => $src_path,
  publication_timestamp   => $pub_ts,
  interval_count_input    => scalar(@sorted),
  interval_count_output   => scalar(@intervals),
  hour_count_output       => scalar(@hourly),
  intervals               => \@intervals,
  hourly                  => \@hourly,
  mqtt => {
    enabled               => !!($cfg->{mqtt_enabled}),
    intervals_topic       => $topic_intervals,
    hourly_topic          => $topic_hourly,
    publish_intervals_ok  => $pub_intervals_ok ? JSON::PP::true : JSON::PP::false,
    publish_hourly_ok     => $pub_hourly_ok    ? JSON::PP::true : JSON::PP::false,
  },
};

print JSON::PP->new->pretty(1)->encode($out);
exit 0;
