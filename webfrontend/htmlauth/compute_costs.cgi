#!/usr/bin/perl
use strict;
use warnings;

use CGI;
use CGI::Carp qw(fatalsToBrowser warningsToBrowser);  # show errors in browser
use JSON::PP;
use File::Spec;
use Time::Local qw(timegm timelocal);
use POSIX qw(strftime);
use FindBin;
use LoxBerry::System;
use LWP::UserAgent;  # for Influx HTTP writes
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
  my $doc = eval { JSON::PP->new->decode($raw) };
  if (!$doc) {
    return ($path, undef, { error => "invalid_json", message => "Could not parse tariffs_latest.json" });
  }
  return ($path, $doc, undef);
}

sub read_today_and_tomorrow_json {
  my $today_path = File::Spec->catfile($lbpdatadir, 'tariffs_today.json');
  my $tomorrow_path = File::Spec->catfile($lbpdatadir, 'tariffs_tomorrow.json');
  
  my ($today_doc, $tomorrow_doc);
  
  # Read today's data
  if (-f $today_path) {
    if (open my $fh, '<', $today_path) {
      local $/ = undef;
      my $raw = <$fh>;
      close $fh;
      $today_doc = eval { JSON::PP->new->decode($raw) };
      eval { LOGWARN("Failed to parse $today_path: $@"); 1; } if $@;
    } else {
      eval { LOGWARN("Failed to open $today_path: $!"); 1; };
    }
  }
  
  # Read tomorrow's data
  if (-f $tomorrow_path) {
    if (open my $fh, '<', $tomorrow_path) {
      local $/ = undef;
      my $raw = <$fh>;
      close $fh;
      $tomorrow_doc = eval { JSON::PP->new->decode($raw) };
      eval { LOGWARN("Failed to parse $tomorrow_path: $@"); 1; } if $@;
    } else {
      eval { LOGWARN("Failed to open $tomorrow_path: $!"); 1; };
    }
  }
  
  return ($today_doc, $tomorrow_doc);
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
  return 0 unless ref($b) eq 'HASH';
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
  return 0 unless ref($b) eq 'HASH';
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

# Parse ISO timestamp with offset into a UTC epoch (seconds since epoch).
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

# --------------------------
# Build raw intervals and hourly aggregates
# --------------------------
my @intervals_raw;
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

  push @intervals_raw, {
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

my @hourly_raw;
for my $hk (sort keys %hour_groups) {
  my $g = $hour_groups{$hk};
  my $n = $g->{n} || 1;
  push @hourly_raw, {
    hour_start          => $hk,
    avg_total_chf       => $g->{total_sum} / $n,
    avg_chf_per_kwh_sum => $g->{kwh_sum}   / $n,
    avg_chf_m_per_hour  => $g->{fixed_sum} / $n,
    intervals_count     => $n,
  };
}

my $pub_ts = $doc->{publication_timestamp} // '';

# --------------------------
# Forward-fill absolute outputs (never-null) for MQTT
# - hourly_filled: 24 entries (local hours)
# - intervals_filled: 96 entries (local 15m)
# --------------------------

# Build epoch maps from RAW arrays
my %hour_epoch_map_ff;
for my $h (@hourly_raw) {
  next unless ref $h eq 'HASH' && defined $h->{hour_start};
  my $e = _iso_to_epoch($h->{hour_start});
  next unless defined $e;
  $hour_epoch_map_ff{ int($e/3600)*3600 } = $h unless exists $hour_epoch_map_ff{ int($e/3600)*3600 };
}
my @hour_epochs_sorted_ff = sort { $a <=> $b } keys %hour_epoch_map_ff;

my %q_epoch_map_ff;
for my $it (@intervals_raw) {
  next unless ref $it eq 'HASH' && defined $it->{start_timestamp};
  my $e = _iso_to_epoch($it->{start_timestamp});
  next unless defined $e;
  $q_epoch_map_ff{ int($e/900)*900 } = $it unless exists $q_epoch_map_ff{ int($e/900)*900 };
}
my @q_epochs_sorted_ff = sort { $a <=> $b } keys %q_epoch_map_ff;

# Local midnight for "today"
my @lt_now = localtime(time);
my $midnight_local_epoch = timelocal(0, 0, 0, $lt_now[3], $lt_now[4], $lt_now[5]);

# Detect midnight transition: today's midnight is after all available data
# This happens between 00:00 and ~19:00 when building today's view but only having yesterday's data
# Log this condition for troubleshooting
if (@hour_epochs_sorted_ff == 0) {
  # No data at all
  eval { LOGWARN("Midnight transition: No hourly data available in tariffs_latest.json"); 1; };
} elsif ($midnight_local_epoch > $hour_epochs_sorted_ff[-1]) {
  # Today's midnight is after the last available data timestamp
  my $last_data_time = strftime('%Y-%m-%d %H:%M:%S', localtime($hour_epochs_sorted_ff[-1]));
  my $today_midnight = strftime('%Y-%m-%d %H:%M:%S', localtime($midnight_local_epoch));
  eval { 
    LOGINF("Midnight transition detected: Building view for $today_midnight but last data is from $last_data_time. Using last available values as carry-forward."); 
    1; 
  };
}

sub _latest_hour_before_or_at {
  my ($epoch) = @_;
  return undef unless defined $epoch;
  
  # If no data available, return undef
  return undef unless @hour_epochs_sorted_ff;
  
  # If requested epoch is at or after the last available data, return the last available
  # This handles the midnight transition case where we're building today's view with yesterday's data
  if ($epoch >= $hour_epochs_sorted_ff[-1]) {
    return $hour_epoch_map_ff{ $hour_epochs_sorted_ff[-1] };
  }
  
  # Find the latest data point before or at the requested epoch
  for (my $i = $#hour_epochs_sorted_ff; $i >= 0; $i--) {
    my $e = $hour_epochs_sorted_ff[$i];
    return $hour_epoch_map_ff{$e} if $e <= $epoch;
  }
  
  # If requested epoch is before all available data, return the first available
  # (This shouldn't normally happen, but provides a fallback)
  return $hour_epoch_map_ff{ $hour_epochs_sorted_ff[0] };
}

sub _latest_q_before_or_at {
  my ($epoch) = @_;
  return undef unless defined $epoch;
  
  # If no data available, return undef
  return undef unless @q_epochs_sorted_ff;
  
  # If requested epoch is at or after the last available data, return the last available
  # This handles the midnight transition case where we're building today's view with yesterday's data
  if ($epoch >= $q_epochs_sorted_ff[-1]) {
    return $q_epoch_map_ff{ $q_epochs_sorted_ff[-1] };
  }
  
  # Find the latest data point before or at the requested epoch
  for (my $i = $#q_epochs_sorted_ff; $i >= 0; $i--) {
    my $e = $q_epochs_sorted_ff[$i];
    return $q_epoch_map_ff{$e} if $e <= $epoch;
  }
  
  # If requested epoch is before all available data, return the first available
  # (This shouldn't normally happen, but provides a fallback)
  return $q_epoch_map_ff{ $q_epochs_sorted_ff[0] };
}

# Helpers to build local ISO with timezone offset
sub _local_hour_iso { my ($t)=@_; my $d=strftime('%Y-%m-%dT%H:00:00', localtime($t)); my $z=strftime('%z', localtime($t)); $z =~ s/^([+\-])(\d{2})(\d{2})$/$1$2:$3/; return $d.$z; }
sub _local_q_iso   { my ($t)=@_; my $d=strftime('%Y-%m-%dT%H:%M:00', localtime($t)); my $z=strftime('%z', localtime($t)); $z =~ s/^([+\-])(\d{2})(\d{2})$/$1$2:$3/; return $d.$z; }
sub _local_q_end_iso { my ($t)=@_; my $d=strftime('%Y-%m-%dT%H:%M:00', localtime($t+900)); my $z=strftime('%z', localtime($t+900)); $z =~ s/^([+\-])(\d{2})(\d{2})$/$1$2:$3/; return $d.$z; }

# Build filled hourly (24) and intervals (96) arrays for MQTT
my @hourly_filled;
for my $h_off (0..23) {
  my $t  = $midnight_local_epoch + $h_off * 3600;
  my $is = _local_hour_iso($t);
  my $ke = _iso_to_epoch($is);
  my $k  = defined $ke ? int($ke/3600)*3600 : undef;

  my $src = (defined $k && exists $hour_epoch_map_ff{$k}) ? $hour_epoch_map_ff{$k} : _latest_hour_before_or_at($ke);
  my $avg_total = (defined $src && defined $src->{avg_total_chf}) ? 0 + $src->{avg_total_chf} : 0;
  my $avg_kwh   = (defined $src && defined $src->{avg_chf_per_kwh_sum}) ? 0 + $src->{avg_chf_per_kwh_sum} : 0;
  my $avg_m     = (defined $src && defined $src->{avg_chf_m_per_hour}) ? 0 + $src->{avg_chf_m_per_hour} : 0;
  my $n_int     = (defined $src && defined $src->{intervals_count}) ? 0 + $src->{intervals_count} : 0;

  push @hourly_filled, {
    hour_start          => $is,        # local hour with offset
    avg_total_chf       => $avg_total, # never null
    avg_chf_per_kwh_sum => $avg_kwh,
    avg_chf_m_per_hour  => $avg_m,
    intervals_count     => $n_int,
  };
}

my @intervals_filled;
for my $q_off (0..95) {
  my $t  = $midnight_local_epoch + $q_off * 900;
  my $is = _local_q_iso($t);
  my $ie = _local_q_end_iso($t);
  my $ke = _iso_to_epoch($is);
  my $k  = defined $ke ? int($ke/900)*900 : undef;

  my $src = (defined $k && exists $q_epoch_map_ff{$k}) ? $q_epoch_map_ff{$k} : _latest_q_before_or_at($ke);

  my $tot      = (defined $src && defined $src->{total_chf}) ? 0 + $src->{total_chf} : 0;
  my $sum_kwh  = (defined $src && defined $src->{chf_per_kwh_sum}) ? 0 + $src->{chf_per_kwh_sum} : 0;
  my $m_per_h  = (defined $src && defined $src->{chf_m_per_hour}) ? 0 + $src->{chf_m_per_hour} : 0;
  my $mh_used  = (defined $src && defined $src->{month_hours_used}) ? 0 + $src->{month_hours_used} : 0;

  push @intervals_filled, {
    start_timestamp  => $is,    # local 15m start with offset
    end_timestamp    => $ie,    # local 15m end with offset
    chf_per_kwh_sum  => $sum_kwh,
    chf_m_per_hour   => $m_per_h,
    total_chf        => $tot,   # never null
    month_hours_used => $mh_used,
  };
}

# --------------------------
# Build MQTT messages from filled arrays (topics unchanged)
# --------------------------
my $intervals_msg = {
  publication_timestamp => $pub_ts,
  interval_count        => scalar(@intervals_filled),
  intervals             => \@intervals_filled,
};
my $hourly_msg = {
  publication_timestamp => $pub_ts,
  hour_count            => scalar(@hourly_filled),
  hourly                => \@hourly_filled,
};

my $json_intervals = JSON::PP->new->canonical(1)->encode($intervals_msg);
my $json_hourly    = JSON::PP->new->canonical(1)->encode($hourly_msg);

# --------------------------
# Relative 24-hour view — load both today and tomorrow data
# --------------------------
my ($today_doc, $tomorrow_doc) = read_today_and_tomorrow_json();

# Helper function to add blocks to hour epoch map
# Accumulates all 15-minute intervals per hour, then calculates averages
sub _add_blocks_to_map {
  my ($doc, $map_ref) = @_;
  return unless $doc && ref($doc->{rows}) eq 'ARRAY';
  
  my $rows = $doc->{rows};
  for my $b (@$rows) {
    my $start = $b->{start_timestamp} // next;
    my $hour_key = hour_start_from($start);
    my $e = _iso_to_epoch($hour_key);
    next unless defined $e;
    my $k = int($e/3600)*3600;
    
    # Initialize accumulator if not exists
    unless (exists $map_ref->{$k}) {
      $map_ref->{$k} = {
        hour_start => $hour_key,
        n => 0,
        total_sum => 0,
      };
    }
    
    # Accumulate all intervals for this hour (same logic as main aggregation)
    my ($Y,$M) = (parse_ymdh($start))[0,1];
    my $hours_in_month = days_in_month($Y,$M) * 24;
    my $kwh_total = kwh_total_for_block($b);
    my $monthly_m = monthly_M_total_for_block($b);
    my $fixed_per_hour = $hours_in_month ? ($monthly_m / $hours_in_month) : 0;
    my $sum_total = $kwh_total + $fixed_per_hour;
    
    $map_ref->{$k}{n} += 1;
    $map_ref->{$k}{total_sum} += $sum_total;
  }
  
  # Calculate averages after accumulating all intervals
  for my $k (keys %$map_ref) {
    my $entry = $map_ref->{$k};
    my $n = $entry->{n} || 1;  # Fallback to 1 matches main aggregation logic (line 263)
    $entry->{avg_total_chf} = $entry->{total_sum} / $n;
    # Clean up intermediate accumulation fields (not needed in final output)
    delete $entry->{n};
    delete $entry->{total_sum};
  }
}

# Build combined hour map for relative view
my %hour_epoch_map_rel;
my @hour_epochs_sorted_rel;

# Add today's data
_add_blocks_to_map($today_doc, \%hour_epoch_map_rel);

# Add tomorrow's data
_add_blocks_to_map($tomorrow_doc, \%hour_epoch_map_rel);

@hour_epochs_sorted_rel = sort { $a <=> $b } keys %hour_epoch_map_rel;

sub _latest_value_before_or_at_rel {
  my ($epoch) = @_;
  return (undef, undef) unless defined $epoch;
  return (0 + ($hour_epoch_map_rel{$hour_epochs_sorted_rel[-1]}{avg_total_chf} // 0),
          $hour_epoch_map_rel{$hour_epochs_sorted_rel[-1]}{hour_start})
    if @hour_epochs_sorted_rel && $epoch >= $hour_epochs_sorted_rel[-1];
  for (my $i = $#hour_epochs_sorted_rel; $i >= 0; $i--) {
    my $e = $hour_epochs_sorted_rel[$i];
    if ($e <= $epoch) {
      my $h = $hour_epoch_map_rel{$e};
      my $v = defined $h->{avg_total_chf} ? 0 + $h->{avg_total_chf} : 0;
      return ($v, $h->{hour_start});
    }
  }
  return (undef, undef);
}

my @relative;
for my $off (0..23) {
  # Get current time rounded down to the hour, THEN add offset
  my $current_hour_epoch = int(time / 3600) * 3600;  # Round current time to hour
  my $t = $current_hour_epoch + $off * 3600;

  # Local ISO for this hour (+ offset like +01:00 or +02:00)
  my $hs_local = strftime('%Y-%m-%dT%H:00:00', localtime($t));
  my $offstr = strftime('%z', localtime($t)); $offstr =~ s/^([+\-])(\d{2})(\d{2})$/$1$2:$3/;
  my $hs_iso_local = $hs_local . $offstr;
  
  my $target_epoch = _iso_to_epoch($hs_iso_local);
  my $k = defined $target_epoch ? int($target_epoch/3600)*3600 : undef;

  my ($val, $val_hour_start) = _latest_value_before_or_at_rel($target_epoch);
  $val //= 0;

  push @relative, {
    offset        => $off,
    hour_start    => $hs_iso_local,   # local hour with offset
    avg_total_chf => $val,
  };
}

my $relative_msg = {
  publication_timestamp => $pub_ts,
  reference_time        => strftime('%Y-%m-%dT%H:%M:%S', localtime(time)),
  relative              => \@relative,
};
my $json_relative = JSON::PP->new->canonical(1)->encode($relative_msg);

# --------------------------
# MQTT publish (topics unchanged)
# --------------------------
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
  # Add time check to prevent overwriting today's data with tomorrow's data
  # Only publish absolute values (intervals/hourly) at 23:59 or during hour 0 (00:00-00:59)
  # This prevents publishing when tomorrow's data is in tariffs_latest.json (18:00-23:58)
  my ($hour, $min) = (localtime(time))[2,1];
  my $allow_absolute_publish = ($hour == 23 && $min >= 59) || ($hour == 0);
  
  if ($allow_absolute_publish) {
    $pub_intervals_ok = mqtt_publish($cfg, $topic_intervals, $json_intervals) ? 1 : 0;
    $pub_hourly_ok    = mqtt_publish($cfg, $topic_hourly,    $json_hourly)    ? 1 : 0;
  }
  # Relative can always be published for rolling 24h view
  $pub_relative_ok  = mqtt_publish($cfg, $topic_relative,  $json_relative)  ? 1 : 0;
}

# --------------------------
# InfluxDB helpers
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
    unless ($base && $org && $bucket && $token) {
      eval { LOGERR("Influx v2 missing config: url/org/bucket/token required"); 1; };
      return 0;
    }
    $url = "$base/api/v2/write?org=$org&bucket=$bucket&precision=" . ($precision // 'ns');
    $headers{'Authorization'} = "Token $token";
  } else {
    my $db   = $cfg->{influx_db} // '';
    unless ($base && $db) {
      eval { LOGERR("Influx v1 missing config: url/db required"); 1; };
      return 0;
    }
    my $u = $cfg->{influx_user}     // '';
    my $p = $cfg->{influx_password} // '';
    my $auth = ($u ne '') ? "&u=$u&p=$p" : '';
    $url = "$base/write?db=$db$auth&precision=" . ($precision // 'ns');
  }

  my $body = join("\n", @{$lines_ref // []});
  my $res = $ua->post($url, %headers, Content => $body);
  if ($res->is_success) {
    return 1;
  } else {
    eval { LOGERR("Influx write failed: HTTP ".$res->code." - ".$res->decoded_content); 1; };
    return 0;
  }
}

# --------------------------
# Build Influx payload from RAW arrays and write
# --------------------------
my $influx_ok = 1;
if ($cfg->{influx_enabled}) {
  my @influx_lines;
  my $source = $doc->{source} // 'unknown';
  my $ems    = $cfg->{ems_instance_id} // '';

  # Intervals (RAW, not forward-filled)
  for my $it (@intervals_raw) {
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

  # Hourly (RAW, not forward-filled)
  for my $h (@hourly_raw) {
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

# --------------------------
# Final output (debug/info)
# --------------------------
my $out = {
  source_file             => $src_path,
  publication_timestamp   => $pub_ts,
  interval_count_input    => scalar(@sorted),
  interval_count_output   => scalar(@intervals_filled),
  hour_count_output       => scalar(@hourly_filled),
  intervals               => \@intervals_filled, # filled view mirrors what MQTT publishes
  hourly                  => \@hourly_filled,    # filled view mirrors what MQTT publishes
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
  influx_written          => ($influx_ok ? JSON::PP::true : JSON::PP::false),
};

print JSON::PP->new->pretty(1)->encode($out);
exit 0;
