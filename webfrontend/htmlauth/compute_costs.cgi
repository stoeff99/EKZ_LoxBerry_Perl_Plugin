#!/usr/bin/perl
use strict;
use warnings;

use CGI;
use CGI::Carp qw(fatalsToBrowser warningsToBrowser);  # show errors in browser
use JSON::PP;
use File::Spec;
use Time::Local;
use POSIX qw(strftime);
use FindBin;
use LoxBerry::System;
use LWP::UserAgent;  # ADDED for Influx HTTP writes
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
  my $doc = eval { JSON::PP->new->decode($raw) };   # FIX: use JSON::PP decoder
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

# Prefer E+G+R; else I (+R) to avoid double counting
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

# Prefer integrated CHF_M + regional CHF_M; else E+G+R CHF_M
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

# MQTT publish: prefer Net::MQTT::Simple when possible; otherwise use mosquitto_pub
sub mqtt_publish {
  my ($cfg, $topic, $payload_json) = @_;
  return 0 unless $cfg->{mqtt_enabled};

  my $host = $cfg->{mqtt_host} // 'localhost';
  my $port = $cfg->{mqtt_port} // 1883;
  my $user = $cfg->{mqtt_username} // '';
  my $pass = $cfg->{mqtt_password} // '';

  my $ok = 0;
  if ($user eq '') {
    eval {
      require Net::MQTT::Simple;
      Net::MQTT::Simple->import();
      my $broker = "$host:$port";
      my $mqtt = Net::MQTT::Simple->new($broker);
      $mqtt->publish($topic => $payload_json);
      $ok = 1;
      1;
    } or do {
      $ok = 0;
    };
  }

  if (!$ok) {
    my $tmp = File::Spec->catfile($lbpdatadir, 'mqtt_payload.tmp.json');
    open my $tfh, '>', $tmp or return 0;
    print $tfh $payload_json;
    close $tfh;

    my @cmd = ('mosquitto_pub', '-h', $host, '-p', $port, '-t', $topic, '-f', $tmp, '-r');
    if (defined $user && $user ne '') { push @cmd, ('-u', $user); }
    if (defined $pass && $pass ne '') { push @cmd, ('-P', $pass); }

    my $rc = system(@cmd);
    unlink $tmp;
    $ok = ($rc == 0) ? 1 : 0;
  }

  return $ok ? 1 : 0;
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

  push @intervals, {
    start_timestamp  => $start,
    end_timestamp    => $end,
    chf_per_kwh_sum  => $kwh_total,
    chf_m_per_hour   => $fixed_per_hour,
    total_chf        => $sum_total,
    month_hours_used => $hours_in_month,
  };

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

# --------------------------
# Relative 24-hour view (now +0 .. now +23) — epoch-aligned matching
# --------------------------
sub _iso_to_epoch {
  my ($iso) = @_;
  return undef unless defined $iso;
  if ($iso =~ /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(Z|([+\-])(\d{2}):(\d{2}))?$/) {
    my ($Y,$M,$D,$h,$m,$s,$z, $sign, $oh, $om) = ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10);
    $Y += 0; $M += 0; $D += 0; $h += 0; $m += 0; $s += 0;
    my $utc_epoch = timegm($s, $m, $h, $D, $M-1, $Y-1900);
    my $offset_secs = 0;
    if (defined $z && $z ne 'Z' && defined $sign && defined $oh) {
      $offset_secs = ($oh * 3600) + ($om * 60);
      $offset_secs *= ($sign eq '+') ? 1 : -1;
    }
    return $utc_epoch - $offset_secs;
  }
  return undef;
}

my %hour_epoch_map;
for my $h (@hourly) {
  next unless ref $h eq 'HASH' && defined $h->{hour_start};
  my $iso = $h->{hour_start};
  my $e = _iso_to_epoch($iso);
  next unless defined $e;
  my $hour_epoch = int($e / 3600) * 3600;
  $hour_epoch_map{$hour_epoch} = $h unless exists $hour_epoch_map{$hour_epoch};
}

my @relative;
for my $off (0..23) {
  my $t = time + $off * 3600;
  my $target_hour_epoch = int($t / 3600) * 3600;
  my $entry = $hour_epoch_map{$target_hour_epoch};
  my ($hs, $val);
  if ($entry) {
    $hs  = $entry->{hour_start};
    $val = defined $entry->{avg_total_chf} ? ($entry->{avg_total_chf} + 0) : undef;
  } else {
    my $hs_local = strftime('%Y-%m-%dT%H:00:00', localtime($t));
    my $offstr = strftime('%z', localtime($t)); $offstr =~ s/^([+\-])(\d{2})(\d{2})$/$1$2:$3/;
    $hs = $hs_local . $offstr;
    $val = undef;
  }
  push @relative, {
    offset => $off,
    hour_start => $hs,
    avg_total_chf => $val,
  };
}

my $relative_msg = {
  publication_timestamp => $pub_ts,
  reference_time        => strftime('%Y-%m-%dT%H:%M:%S', localtime(time)),
  relative              => \@relative,
};
my $json_relative = JSON::PP->new->canonical(1)->encode($relative_msg);

# Topics (new dedicated, with backward-compatible fallback)
my $topic_intervals = $cfg->{mqtt_topic_intervals}
                   // $cfg->{mqtt_topic_raw}
                   // 'ekz/ems/tariffs/intervals';
my $topic_hourly    = $cfg->{mqtt_topic_hourly}
                   // $cfg->{mqtt_topic_summary}
                   // 'ekz/ems/tariffs/hourly';
my $topic_relative  = $cfg->{mqtt_topic_relative} // 'ekz/ems/tariffs/relative';

my ($pub_intervals_ok, $pub_hourly_ok) = (0, 0);
my $pub_relative_ok = 0;
if (!$nopublish) {
  $pub_intervals_ok = mqtt_publish($cfg, $topic_intervals, $json_intervals) ? 1 : 0;
  $pub_hourly_ok    = mqtt_publish($cfg, $topic_hourly,    $json_hourly)    ? 1 : 0;
  $pub_relative_ok  = mqtt_publish($cfg, $topic_relative,  $json_relative)  ? 1 : 0;
}

# --------------------------
# InfluxDB helpers (ADDED)
# --------------------------
sub _escape_tag {
  my ($v) = @_;
  $v //= '';
  $v =~ s/[, ]/\\$&/g;
  $v =~ s/=/\\=/g;
  return $v;
}

sub _line {
  my ($measurement, $tags_hashref, $fields_hashref, $epoch_s) = @_;
  my $m = $measurement; $m =~ s/[, ]/\\$&/g;
  my @tags;
  for my $k (sort keys %{$tags_hashref // {}}) {
    my $kk = $k; $kk =~ s/[, =]/\\$&/g;
    push @tags, "$kk="._escape_tag($tags_hashref->{$k});
  }
  my @fields;
  for my $k (sort keys %{$fields_hashref // {}}) {
    my $kk = $k; $kk =~ s/[, =]/\\$&/g;
    my $v = $fields_hashref->{$k};
    push @fields, "$kk=$v";
  }
  return join(',', $m, (scalar(@tags) ? join(',', @tags) :())) . ' ' . join(',', @fields) . ' ' . int($epoch_s) . '000000000';
}

sub influx_write_lines {
  my ($cfg, $lines_ref, $precision) = @_;
  return 1 unless $cfg->{influx_enabled};

  my $version = ($cfg->{influx_version} // '2') . '';
  my $base    = $cfg->{influx_url} // '';
  my $ua      = LWP::UserAgent->new(timeout => 20);

  my $url;
  my %headers = ( 'Content-Type' => 'text/plain' );

  if ($version eq '2') {
    my $org    = $cfg->{influx_org}    // '';
    my $bucket = $cfg->{influx_bucket} // '';
    my $token  = $cfg->{influx_token}  // '';
    die "influx_url/org/bucket/token required for InfluxDB v2" unless $base && $org && $bucket && $token;
    $url = "$base/api/v2/write?org=$org&bucket=$bucket&precision=" . ($precision // 'ns');
    $headers{'Authorization'} = "Token $token";
  } else {
    my $db   = $cfg->{influx_db} // '';
    die "influx_url/db required for InfluxDB v1" unless $base && $db;
    my $u = $cfg->{influx_user}     // '';
    my $p = $cfg->{influx_password} // '';
    my $auth = ($u ne '') ? "&u=$u&p=$p" : '';
    $url = "$base/write?db=$db$auth&precision=" . ($precision // 'ns');
  }

  my $body = join("\n", @{$lines_ref // []});
  my $res = $ua->post($url, %headers, Content => $body);
  return 1 if $res->is_success;

  eval { LOGERR("Influx write failed: HTTP ".$res->code." - ".$res->decoded_content); 1; };
  return 0;
}

# --------------------------
# Build Influx payload and write (ADDED)
# --------------------------
my $influx_ok = 1;
if ($cfg->{influx_enabled}) {
  my @influx_lines;
  my $source = $doc->{source} // 'unknown';
  my $ems    = $cfg->{ems_instance_id} // '';

  for my $it (@intervals) {
    my $t = _iso_to_epoch($it->{start_timestamp}); next unless defined $t;
    my %tags = ( source => $source, ems => $ems );
    my %fields = (
      total_chf        => ($it->{total_chf}        // 0) + 0,
      chf_per_kwh_sum  => ($it->{chf_per_kwh_sum}  // 0) + 0,
      chf_m_per_hour   => ($it->{chf_m_per_hour}   // 0) + 0,
      month_hours_used => ($it->{month_hours_used} // 0) + 0,
    );
    push @influx_lines, _line('ekz_tariff_intervals', \%tags, \%fields, $t);
  }

  for my $h (@hourly) {
    next unless ref $h eq 'HASH' && defined $h->{hour_start};
    my $t = _iso_to_epoch($h->{hour_start}); next unless defined $t;
    my %tags = ( source => $source, ems => $ems );
    my %fields = (
      avg_total_chf       => ($h->{avg_total_chf}       // 0) + 0,
      avg_chf_per_kwh_sum => ($h->{avg_chf_per_kwh_sum} // 0) + 0,
      avg_chf_m_per_hour  => ($h->{avg_chf_m_per_hour}  // 0) + 0,
      intervals_count     => ($h->{intervals_count}     // 0) + 0,
    );
    push @influx_lines, _line('ekz_tariff_hourly', \%tags, \%fields, $t);
  }

  $influx_ok = influx_write_lines($cfg, \@influx_lines, 'ns');
}

my $out = {
  source_file             => $src_path,
  publication_timestamp   => $pub_ts,
  interval_count_input    => scalar(@sorted),
  interval_count_output   => scalar(@intervals),
  hour_count_output       => scalar(@hourly),
  intervals               => \@intervals,
  hourly                  => \@hourly,
  mqtt => {
    enabled                  => !!($cfg->{mqtt_enabled}),
    intervals_topic          => $topic_intervals,
    hourly_topic             => $topic_hourly,
    publish_intervals_ok     => $pub_intervals_ok ? JSON::PP::true : JSON::PP::false,
    publish_hourly_ok        => $pub_hourly_ok    ? JSON::PP::true : JSON::PP::false,
    skipped_due_to_nopublish => $nopublish ? JSON::PP::true : JSON::PP::false,
    relative_topic           => $topic_relative,
    publish_relative_ok      => $pub_relative_ok ? JSON::PP::true : JSON::PP::false,
  },
  influx_written          => ($influx_ok ? JSON::PP::true : JSON::PP::false),  # FIX: use true/false
};

print JSON::PP->new->pretty(1)->encode($out);
exit 0;
