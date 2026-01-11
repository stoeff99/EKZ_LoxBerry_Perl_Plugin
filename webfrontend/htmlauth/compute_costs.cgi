#!/usr/bin/perl
use strict;
use warnings;

use CGI;
use CGI::Carp qw(fatalsToBrowser warningsToBrowser);
use JSON::PP;
use File::Spec;
use Time::Local qw(timegm timelocal);
use POSIX qw(strftime);
use FindBin;
use LoxBerry::System;
use LWP::UserAgent;
require "$FindBin::Bin/common.pl";

our (
  $WINDOW_END_OFFSET_SEC,
  $HOUR_ROUNDING_THRESHOLD_MIN,
);

our ($lbpdatadir, $lbpurl, $lbptemplatedir);

my $q = CGI->new;
print $q->header('application/json; charset=utf-8');

my $nopublish = ($q->param('nopublish') // '') ne '' ? 1 : 0;

# ---- helpers ----

sub read_today_and_tomorrow_json {
  my $today_path = File::Spec->catfile($lbpdatadir, 'tariffs_today.json');
  my $tomorrow_path = File::Spec->catfile($lbpdatadir, 'tariffs_tomorrow.json');
  my $latest_path = File::Spec->catfile($lbpdatadir, 'tariffs_latest.json');
  
  my $today_doc = read_json_file($today_path, { silent => 1 });
  my $tomorrow_doc = read_json_file($tomorrow_path, { silent => 1 });
  
  if (!$today_doc && -f $latest_path) {
    eval { LOGWARN("tariffs_today.json missing, using tariffs_latest.json as fallback"); 1; };
    $today_doc = read_json_file($latest_path, { silent => 1 });
    if ($today_doc) {
      eval { LOGINF("Successfully loaded today's data from tariffs_latest.json"); 1; };
    }
  }
  
  if (!$today_doc) {
    eval { LOGERR("CRITICAL: No valid today data found for relative calculations!"); 1; };
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
  my ($y, $m) = @_;
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
  return 0 unless ref($b) eq 'HASH';
  my $e_kwh = get_unit_value($b->{electricity}, 'CHF_kWh');
  my $g_kwh = get_unit_value($b->{grid}, 'CHF_kWh');
  my $r_kwh = get_unit_value($b->{regional_fees}, 'CHF_kWh');
  if ($e_kwh || $g_kwh) {
    return $e_kwh + $g_kwh + $r_kwh;
  } else {
    my $i_kwh = get_unit_value($b->{integrated}, 'CHF_kWh');
    return $i_kwh + $r_kwh;
  }
}

sub monthly_M_total_for_block {
  my ($b) = @_;
  return 0 unless ref($b) eq 'HASH';
  
  my $metering_m = get_unit_value($b->{metering}, 'CHF_M');
  my $i_m = get_unit_value($b->{integrated}, 'CHF_M');
  my $r_m = get_unit_value($b->{regional_fees}, 'CHF_M');
  
  if ($i_m) {
    return $i_m + $r_m + $metering_m;
  } else {
    my $e_m = get_unit_value($b->{electricity}, 'CHF_M');
    my $g_m = get_unit_value($b->{grid}, 'CHF_M');
    return $e_m + $g_m + $r_m + $metering_m;
  }
}

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

sub ensure_daily_rotation {
  my ($cfg) = @_;
  
  my $hour = (localtime(time))[2];
  return unless $hour == 0;
  
  my $marker_file = File::Spec->catfile($lbpdatadir, '.rotated_today');
  my $today_ymd = strftime('%Y-%m-%d', localtime);
  
  if (-f $marker_file) {
    if (open my $fh, '<', $marker_file) {
      my $marker_date = <$fh>;
      close $fh;
      chomp $marker_date if defined $marker_date;
      return if defined $marker_date && $marker_date eq $today_ymd;
    }
  }
  
  my $today_file = File::Spec->catfile($lbpdatadir, 'tariffs_today.json');
  my $tomorrow_file = File::Spec->catfile($lbpdatadir, 'tariffs_tomorrow.json');
  
  if (-f $tomorrow_file) {
    my $tomorrow_doc = read_json_file($tomorrow_file, { silent => 1 });
    if ($tomorrow_doc) {
      write_json_file($today_file, $tomorrow_doc);
      
      my $latest_file = File::Spec->catfile($lbpdatadir, 'tariffs_latest.json');
      write_json_file($latest_file, $tomorrow_doc);
      
      unlink $tomorrow_file;
      
      eval { LOGINF("Daily rotation completed: tomorrow → today at midnight"); 1; };
    }
  }
  
  if (open my $mf, '>', $marker_file) {
    print $mf $today_ymd;
    close $mf;
    chmod 0640, $marker_file;
  }
}

sub verify_and_reformat_prices {
  my ($doc) = @_;
  my $prices = $doc->{prices} || $doc->{rows} || [];
  
  my @reformatted;
  my $missing_count = 0;
  
  for my $i (0..$#{$prices}) {
    my $p = $prices->[$i];
    
    # Check if all required fields exist
    my $has_start = exists $p->{start_timestamp};
    my $has_end = exists $p->{end_timestamp};
    my $has_elec = exists $p->{electricity};
    my $has_grid = exists $p->{grid};
    my $has_integ = exists $p->{integrated};
    my $has_regional = exists $p->{regional_fees};
    my $has_metering = exists $p->{metering};
    
    if (! $has_start || !$has_end || !$has_elec || !$has_grid || !$has_integ || ! $has_regional || ! $metering) {
      $missing_count++;
      eval { LOGWARN("Interval $i missing fields: " . 
        "start=$has_start end=$has_end elec=$has_elec grid=$has_grid integ=$has_integ regional=$has_regional metering=$has_metering"); 1; };
    }
    
    # Reformat in preferred order
    push @reformatted, {
      start_timestamp => $p->{start_timestamp},
      end_timestamp => $p->{end_timestamp},
      electricity => $p->{electricity},
      grid => $p->{grid},
      integrated => $p->{integrated},
      regional_fees => $p->{regional_fees},
      metering => $p->{metering},
    };
  }
  
  eval { LOGINF("Verified " . scalar(@$prices) . " intervals, $missing_count had missing fields"); 1; };
  
  return \@reformatted;
}

# ---- main ----

my $cfg = eval { load_cfg() } // {};

# Read BOTH today and tomorrow data for absolute values
my ($today_doc, $tomorrow_doc) = read_today_and_tomorrow_json();

# Verify and reformat the data for troubleshooting
if ($today_doc) {
  $today_doc->{prices} = verify_and_reformat_prices($today_doc);
  eval { LOGINF("Today's data verified and reformatted"); 1; };
}
if ($tomorrow_doc) {
  $tomorrow_doc->{prices} = verify_and_reformat_prices($tomorrow_doc);
  eval { LOGINF("Tomorrow's data verified and reformatted"); 1; };
}

# Use today as primary source
my $doc = $today_doc;
if (!$doc) {
  print JSON::PP->new->encode({ error => 'No today tariff data available' });
  exit 0;
}

# Get today's rows
my $rows = $doc->{prices};
$rows = $doc->{rows} if ! defined $rows;
$rows ||= [];

my @sorted = sort {
  ($a->{start_timestamp} // '') cmp ($b->{start_timestamp} // '')
} grep { ref $_ eq 'HASH' && $_->{start_timestamp} } @$rows;

# IMPORTANT: Add tomorrow's data if available
if ($tomorrow_doc) {
  my $tomorrow_rows = $tomorrow_doc->{prices};
  $tomorrow_rows = $tomorrow_doc->{rows} if ! defined $tomorrow_rows;
  
  if ($tomorrow_rows && ref($tomorrow_rows) eq 'ARRAY') {
    my @tomorrow_sorted = sort {
      ($a->{start_timestamp} // '') cmp ($b->{start_timestamp} // '')
    } grep { ref $_ eq 'HASH' && $_->{start_timestamp} } @$tomorrow_rows;
    
    # Merge tomorrow's data into sorted array
    push @sorted, @tomorrow_sorted;
    
    eval { LOGINF("Loaded " . scalar(@tomorrow_sorted) . " intervals from tomorrow's data"); 1; };
  }
}

# Use today's publication timestamp
my $pub_ts = $doc->{publication_timestamp} // '';

eval { LOGINF("Total sorted intervals: " . scalar(@sorted)); 1; };

# --------------------------
# Build raw intervals and hourly aggregates
# --------------------------
my @intervals_raw;
my %hour_groups;

for my $b (@sorted) {
  my $start = $b->{start_timestamp} // next;
  my $end = $b->{end_timestamp} // '';

  my ($Y,$M) = (parse_ymdh($start))[0,1];
  my $hours_in_month = days_in_month($Y,$M) * 24;

  my $kwh_total = kwh_total_for_block($b);
  my $monthly_m = monthly_M_total_for_block($b);
  my $fixed_per_hour = $hours_in_month ? ($monthly_m / $hours_in_month) : 0;
  my $sum_total = $kwh_total + $fixed_per_hour;

  my $hour_key = hour_start_from($start);

  push @intervals_raw, {
    start_timestamp => $start,
    end_timestamp => $end,
    chf_per_kwh_sum => $kwh_total,
    chf_m_per_hour => $fixed_per_hour,
    total_chf => $sum_total,
    month_hours_used => $hours_in_month,
  };

  $hour_groups{$hour_key} ||= { n => 0, kwh_sum => 0, fixed_sum => 0, total_sum => 0 };
  $hour_groups{$hour_key}{n} += 1;
  $hour_groups{$hour_key}{kwh_sum} += $kwh_total;
  $hour_groups{$hour_key}{fixed_sum} += $fixed_per_hour;
  $hour_groups{$hour_key}{total_sum} += $sum_total;
}

my @hourly_raw;
for my $hk (sort keys %hour_groups) {
  my $g = $hour_groups{$hk};
  my $n = $g->{n} || 1;
  push @hourly_raw, {
    hour_start => $hk,
    avg_total_chf => $g->{total_sum} / $n,
    avg_chf_per_kwh_sum => $g->{kwh_sum} / $n,
    avg_chf_m_per_hour => $g->{fixed_sum} / $n,
    intervals_count => $n,
  };
}

# --------------------------
# Forward-fill absolute outputs
# --------------------------

my %hour_epoch_map_ff;
for my $h (@hourly_raw) {
  next unless ref $h eq 'HASH' && defined $h->{hour_start};
  my $e = _iso_to_epoch($h->{hour_start});
  next unless defined $e;
  my $k = int($e/3600)*3600;
  $hour_epoch_map_ff{$k} = $h unless exists $hour_epoch_map_ff{$k};
}
my @hour_epochs_sorted_ff = sort { $a <=> $b } keys %hour_epoch_map_ff;

my %q_epoch_map_ff;
for my $it (@intervals_raw) {
  next unless ref $it eq 'HASH' && defined $it->{start_timestamp};
  my $e = _iso_to_epoch($it->{start_timestamp});
  next unless defined $e;
  my $k = int($e/900)*900;
  $q_epoch_map_ff{$k} = $it unless exists $q_epoch_map_ff{$k};
}
my @q_epochs_sorted_ff = sort { $a <=> $b } keys %q_epoch_map_ff;

my @lt_now = localtime(time);
my $midnight_local_epoch = timelocal(0, 0, 0, $lt_now[3], $lt_now[4], $lt_now[5]);

if (@hour_epochs_sorted_ff == 0) {
  eval { LOGWARN("Midnight transition: No hourly data available"); 1; };
} elsif ($midnight_local_epoch > $hour_epochs_sorted_ff[-1]) {
  eval { LOGINF("Midnight transition detected: Using last available values"); 1; };
}

sub _latest_hour_before_or_at {
  my ($epoch) = @_;
  return undef unless defined $epoch;
  return undef unless @hour_epochs_sorted_ff;
  
  if ($epoch >= $hour_epochs_sorted_ff[-1]) {
    return $hour_epoch_map_ff{$hour_epochs_sorted_ff[-1]};
  }
  
  for (my $i = $#hour_epochs_sorted_ff; $i >= 0; $i--) {
    my $e = $hour_epochs_sorted_ff[$i];
    return $hour_epoch_map_ff{$e} if $e <= $epoch;
  }
  
  return $hour_epoch_map_ff{$hour_epochs_sorted_ff[0]};
}

sub _latest_q_before_or_at {
  my ($epoch) = @_;
  return undef unless defined $epoch;
  return undef unless @q_epochs_sorted_ff;
  
  if ($epoch >= $q_epochs_sorted_ff[-1]) {
    return $q_epoch_map_ff{$q_epochs_sorted_ff[-1]};
  }
  
  for (my $i = $#q_epochs_sorted_ff; $i >= 0; $i--) {
    my $e = $q_epochs_sorted_ff[$i];
    return $q_epoch_map_ff{$e} if $e <= $epoch;
  }
  
  return $q_epoch_map_ff{$q_epochs_sorted_ff[0]};
}

sub _local_hour_iso { my ($t)=@_; my $d=strftime('%Y-%m-%dT%H:00:00', localtime($t)); my $z=strftime('%z', localtime($t)); $z =~ s/^([+\-])(\d{2})(\d{2})$/$1$2:$3/; return $d.$z; }
sub _local_q_iso { my ($t)=@_; my $d=strftime('%Y-%m-%dT%H:%M:00', localtime($t)); my $z=strftime('%z', localtime($t)); $z =~ s/^([+\-])(\d{2})(\d{2})$/$1$2:$3/; return $d.$z; }
sub _local_q_end_iso { my ($t)=@_; my $d=strftime('%Y-%m-%dT%H:%M:00', localtime($t+900)); my $z=strftime('%z', localtime($t+900)); $z =~ s/^([+\-])(\d{2})(\d{2})$/$1$2:$3/; return $d.$z; }

# --------------------------
# Build filled hourly and intervals for 48 hours
# --------------------------
my @hourly_filled;
for my $day_offset (0..0) {
  my $day_midnight_epoch = $midnight_local_epoch + ($day_offset * 86400);
  
  for my $h_off (0..47) {  # 48 hours = today + tomorrow
    my $t = $day_midnight_epoch + $h_off * 3600;
    my $is = _local_hour_iso($t);
    my $ke = _iso_to_epoch($is);
    my $k = defined $ke ? int($ke/3600)*3600 : undef;

    my $src = (defined $k && exists $hour_epoch_map_ff{$k}) ? $hour_epoch_map_ff{$k} : _latest_hour_before_or_at($ke);
    my $avg_total = (defined $src && defined $src->{avg_total_chf}) ? 0 + $src->{avg_total_chf} : 0;
    my $avg_kwh = (defined $src && defined $src->{avg_chf_per_kwh_sum}) ? 0 + $src->{avg_chf_per_kwh_sum} : 0;
    my $avg_m = (defined $src && defined $src->{avg_chf_m_per_hour}) ? 0 + $src->{avg_chf_m_per_hour} : 0;
    my $n_int = (defined $src && defined $src->{intervals_count}) ? 0 + $src->{intervals_count} : 0;

    push @hourly_filled, {
      hour_start => $is,
      avg_total_chf => $avg_total,
      avg_chf_per_kwh_sum => $avg_kwh,
      avg_chf_m_per_hour => $avg_m,
      intervals_count => $n_int,
    };
  }
}

my @intervals_filled;
for my $day_offset (0..0) {
  my $day_midnight_epoch = $midnight_local_epoch + ($day_offset * 86400);
  
  for my $q_off (0..191) {  # 192 = 48 hours * 4 intervals/hour
    my $t = $day_midnight_epoch + $q_off * 900;
    my $is = _local_q_iso($t);
    my $ie = _local_q_end_iso($t);
    my $ke = _iso_to_epoch($is);
    my $k = defined $ke ? int($ke/900)*900 : undef;

    my $src = (defined $k && exists $q_epoch_map_ff{$k}) ? $q_epoch_map_ff{$k} : _latest_q_before_or_at($ke);

    my $tot = (defined $src && defined $src->{total_chf}) ? 0 + $src->{total_chf} : 0;
    my $sum_kwh = (defined $src && defined $src->{chf_per_kwh_sum}) ? 0 + $src->{chf_per_kwh_sum} : 0;
    my $m_per_h = (defined $src && defined $src->{chf_m_per_hour}) ? 0 + $src->{chf_m_per_hour} : 0;
    my $mh_used = (defined $src && defined $src->{month_hours_used}) ? 0 + $src->{month_hours_used} : 0;

    push @intervals_filled, {
      start_timestamp => $is,
      end_timestamp => $ie,
      chf_per_kwh_sum => $sum_kwh,
      chf_m_per_hour => $m_per_h,
      total_chf => $tot,
      month_hours_used => $mh_used,
    };
  }
}

# --------------------------
# Build MQTT messages
# --------------------------
my $intervals_msg = {
  publication_timestamp => $pub_ts,
  interval_count => scalar(@intervals_filled),
  intervals => \@intervals_filled,
};
my $hourly_msg = {
  publication_timestamp => $pub_ts,
  hour_count => scalar(@hourly_filled),
  hourly => \@hourly_filled,
};

my $json_intervals = JSON::PP->new->canonical(1)->encode($intervals_msg);
my $json_hourly = JSON::PP->new->canonical(1)->encode($hourly_msg);

# --------------------------
# Relative 24-hour view
# --------------------------

sub _add_blocks_to_map {
  my ($doc, $map_ref) = @_;
  return unless $doc;
  
  my $rows = $doc->{prices};
  $rows = $doc->{rows} if !defined $rows;
  
  unless ($rows && ref($rows) eq 'ARRAY' && @$rows > 0) {
    eval { LOGWARN("_add_blocks_to_map: No valid rows/prices array found"); 1; };
    return;
  }
  
  eval { LOGDEB("_add_blocks_to_map: Processing " . scalar(@$rows) . " blocks"); 1; };
  
  for my $b (@$rows) {
    next unless ref($b) eq 'HASH';
    my $start = $b->{start_timestamp};
    next unless defined $start;
    
    my $hour_key = hour_start_from($start);
    my $e = _iso_to_epoch($hour_key);
    unless (defined $e) {
      eval { LOGWARN("Could not parse epoch from hour_key: $hour_key"); 1; };
      next;
    }
    
    my $k = int($e/3600)*3600;
    
    unless (exists $map_ref->{$k}) {
      $map_ref->{$k} = {
        hour_start => $hour_key,
        n => 0,
        total_sum => 0,
      };
    }
    
    my ($Y,$M) = (parse_ymdh($start))[0,1];
    my $hours_in_month = days_in_month($Y,$M) * 24;
    my $kwh_total = kwh_total_for_block($b);
    my $monthly_m = monthly_M_total_for_block($b);
    my $fixed_per_hour = $hours_in_month ? ($monthly_m / $hours_in_month) : 0;
    my $sum_total = $kwh_total + $fixed_per_hour;
    
    $map_ref->{$k}{n} += 1;
    $map_ref->{$k}{total_sum} += $sum_total;
  }
  
  for my $k (keys %$map_ref) {
    my $entry = $map_ref->{$k};
    my $n = $entry->{n} || 1;
    $entry->{avg_total_chf} = $entry->{total_sum} / $n;
    delete $entry->{n};
    delete $entry->{total_sum};
  }
  
  eval { LOGDEB("_add_blocks_to_map: Added " . scalar(keys %$map_ref) . " hours to map"); 1; };
}

my %hour_epoch_map_rel;

if (! $today_doc) {
  eval { LOGERR("Cannot build relative values: today's tariff data is missing"); 1; };
} else {
  eval { LOGINF("Loading today's data for relative view"); 1; };
  _add_blocks_to_map($today_doc, \%hour_epoch_map_rel);
  eval { LOGINF("Added " . scalar(keys %hour_epoch_map_rel) . " hours from today's data"); 1; };
}

if ($tomorrow_doc) {
  eval { LOGINF("Loading tomorrow's data for relative view"); 1; };
  _add_blocks_to_map($tomorrow_doc, \%hour_epoch_map_rel);
  eval { LOGINF("Total " . scalar(keys %hour_epoch_map_rel) . " hours in relative map"); 1; };
} else {
  eval { LOGWARN("Tomorrow's tariff data not available yet"); 1; };
}

my @hour_epochs_sorted_rel = sort { $a <=> $b } keys %hour_epoch_map_rel;

if (@hour_epochs_sorted_rel == 0) {
  eval { LOGERR("CRITICAL: hour_epoch_map_rel is EMPTY! Cannot calculate relative values!"); 1; };
}

sub _latest_value_before_or_at_rel {
  my ($epoch) = @_;
  return (undef, undef) unless defined $epoch;
  
  my $k = int($epoch/3600)*3600;
  if (exists $hour_epoch_map_rel{$k}) {
    my $h = $hour_epoch_map_rel{$k};
    my $v = defined $h->{avg_total_chf} ? 0 + $h->{avg_total_chf} : 0;
    return ($v, $h->{hour_start});
  }
  
  return (0 + ($hour_epoch_map_rel{$hour_epochs_sorted_rel[-1]}{avg_total_chf} // 0),
          $hour_epoch_map_rel{$hour_epochs_sorted_rel[-1]}{hour_start})
    if @hour_epochs_sorted_rel && $epoch >= $hour_epochs_sorted_rel[-1];
  
  for (my $i = $#hour_epochs_sorted_rel; $i >= 0; $i--) {
    my $e = $hour_epochs_sorted_rel[$i];
    if ($e < $epoch) {
      my $h = $hour_epoch_map_rel{$e};
      my $v = defined $h->{avg_total_chf} ? 0 + $h->{avg_total_chf} : 0;
      return ($v, $h->{hour_start});
    }
  }
  return (undef, undef);
}

my @relative;
for my $off (0..23) {
  my $now = time;
  my $current_hour_epoch = int($now / 3600) * 3600;
  my $minutes_into_hour = int(($now - $current_hour_epoch) / 60);
  
  if ($minutes_into_hour >= $HOUR_ROUNDING_THRESHOLD_MIN) {
    $current_hour_epoch += 3600;
  }
  
  my $t = $current_hour_epoch + $off * 3600;

  my $hs_local = strftime('%Y-%m-%dT%H:00:00', localtime($t));
  my $offstr = strftime('%z', localtime($t)); 
  $offstr =~ s/^([+\-])(\d{2})(\d{2})$/$1$2:$3/;
  my $hs_iso_local = $hs_local . $offstr;
  
  my $target_epoch = _iso_to_epoch($hs_iso_local);
  
  my ($val, $val_hour_start) = _latest_value_before_or_at_rel($target_epoch);
  $val //= 0;

  push @relative, {
    offset => $off,
    hour_start => $hs_iso_local,
    avg_total_chf => $val,
  };
}

my $relative_msg = {
  publication_timestamp => $pub_ts,
  reference_time => strftime('%Y-%m-%dT%H:%M:%S', localtime(time)),
  relative => \@relative,
};
my $json_relative = JSON::PP->new->canonical(1)->encode($relative_msg);

# --------------------------
# MQTT publish
# --------------------------
my $topic_intervals = $cfg->{mqtt_topic_intervals} // $cfg->{mqtt_topic_raw} // 'ekz/ems/tariffs/intervals';
my $topic_hourly = $cfg->{mqtt_topic_hourly} // $cfg->{mqtt_topic_summary} // 'ekz/ems/tariffs/hourly';
my $topic_relative = $cfg->{mqtt_topic_relative} // 'ekz/ems/tariffs/relative';

my ($pub_intervals_ok, $pub_hourly_ok, $pub_relative_ok) = (0, 0, 0);

if (! $nopublish) {
  my @lt_now = localtime(time);
  my $today_start = timelocal(0, 0, 0, $lt_now[3], $lt_now[4], $lt_now[5]);
  my $today_end = $today_start + $WINDOW_END_OFFSET_SEC;
  
  my $allow_absolute_publish = 0;
  
  if (@sorted > 0) {
    my $first_start = _iso_to_epoch($sorted[0]{start_timestamp});
    my $last_end = _iso_to_epoch($sorted[-1]{end_timestamp});
    
    if (defined $first_start && defined $last_end) {
      my $data_is_for_today = ($first_start < $today_end) && ($last_end >= $today_start);
      $allow_absolute_publish = $data_is_for_today;
    }
  }
  
  if ($allow_absolute_publish) {
    $pub_intervals_ok = publish_mqtt($cfg, $topic_intervals, $json_intervals, { retain => 1, pre_encoded => 1 }) ? 1 : 0;
    $pub_hourly_ok = publish_mqtt($cfg, $topic_hourly, $json_hourly, { retain => 1, pre_encoded => 1 }) ? 1 : 0;
  }
  $pub_relative_ok = publish_mqtt($cfg, $topic_relative, $json_relative, { retain => 1, pre_encoded => 1 }) ? 1 : 0;
}

# --------------------------
# InfluxDB
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
    return join(',', $m, (scalar(@tags) ? join(',', @tags) : ())) . ' ' . join(',', @fields) . ' ' . int($epoch_s) . '000000000';
}

sub influx_write_lines {
  my ($cfg, $lines_ref, $precision) = @_;
  return 1 unless $cfg->{influx_enabled};

  my $version = ($cfg->{influx_version} // '2') . '';
  my $base = $cfg->{influx_url} // '';
  my $ua = LWP::UserAgent->new(timeout => 20);

  my $url;
  my %headers = ( 'Content-Type' => 'text/plain' );

  if ($version eq '2') {
    my $org = $cfg->{influx_org} // '';
    my $bucket = $cfg->{influx_bucket} // '';
    my $token = $cfg->{influx_token} // '';
    unless ($base && $org && $bucket && $token) {
      eval { LOGERR("Influx v2 missing config: url/org/bucket/token required"); 1; };
      return 0;
    }
    $url = "$base/api/v2/write?org=$org&bucket=$bucket&precision=" . ($precision // 'ns');
    $headers{'Authorization'} = "Token $token";
  } else {
    my $db = $cfg->{influx_db} // '';
    unless ($base && $db) {
      eval { LOGERR("Influx v1 missing config: url/db required"); 1; };
      return 0;
    }
    my $u = $cfg->{influx_user} // '';
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

my $influx_ok = 1;
if ($cfg->{influx_enabled}) {
  my @influx_lines;
  my $source = $doc->{source} // 'unknown';
  my $ems = $cfg->{ems_instance_id} // '';

  for my $it (@intervals_raw) {
    my $t = _iso_to_epoch($it->{start_timestamp}); next unless defined $t;
    my %tags = ( source => $source, ems => $ems );
    my %fields = (
      total_chf => ($it->{total_chf} // 0) + 0,
      chf_per_kwh_sum => ($it->{chf_per_kwh_sum} // 0) + 0,
      chf_m_per_hour => ($it->{chf_m_per_hour} // 0) + 0,
      month_hours_used => ($it->{month_hours_used} // 0) + 0,
    );
    push @influx_lines, _line('ekz_tariff_intervals', \%tags, \%fields, $t);
  }

  for my $h (@hourly_raw) {
    next unless ref $h eq 'HASH' && defined $h->{hour_start};
    my $t = _iso_to_epoch($h->{hour_start}); next unless defined $t;
    my %tags = ( source => $source, ems => $ems );
    my %fields = (
      avg_total_chf => ($h->{avg_total_chf} // 0) + 0,
      avg_chf_per_kwh_sum => ($h->{avg_chf_per_kwh_sum} // 0) + 0,
      avg_chf_m_per_hour => ($h->{avg_chf_m_per_hour} // 0) + 0,
      intervals_count => ($h->{intervals_count} // 0) + 0,
    );
    push @influx_lines, _line('ekz_tariff_hourly', \%tags, \%fields, $t);
  }

  $influx_ok = influx_write_lines($cfg, \@influx_lines, 'ns');
}
# --------------------------
# Final output
# --------------------------
my $src_path = File::Spec->catfile($lbpdatadir, 'tariffs_today.json');
my $out = {
  source_file             => $src_path,
  publication_timestamp   => $pub_ts,
  interval_count_input    => scalar(@sorted),
  interval_count_output   => scalar(@intervals_filled),
  hour_count_output       => scalar(@hourly_filled),
  intervals               => \@intervals_filled,
  hourly                  => \@hourly_filled,
  mqtt => {
    enabled                  => !!($cfg->{mqtt_enabled}),
    intervals_topic          => $topic_intervals,
    hourly_topic             => $topic_hourly,
    publish_intervals_ok     => $pub_intervals_ok ? JSON::PP::true : JSON::PP::false,
    publish_hourly_ok        => $pub_hourly_ok ? JSON::PP::true : JSON::PP::false,
    skipped_due_to_nopublish => $nopublish ? JSON::PP::true : JSON::PP::false,
    relative_topic           => $topic_relative,
    publish_relative_ok      => $pub_relative_ok ? JSON::PP::true : JSON::PP::false,
  },
  influx_written          => ($influx_ok ? JSON::PP::true : JSON::PP::false),
};

print JSON::PP->new->pretty(1)->encode($out);
exit 0;
