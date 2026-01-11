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
my $debug_dump = ($q->param('debug_dump') // '') ne '' ? 1 : 0;

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
  my $metering_kwh = get_unit_value($b->{metering}, 'CHF_kWh');
  if ($e_kwh || $g_kwh) {
    return $e_kwh + $g_kwh + $r_kwh + $metering_kwh;
  } else {
    my $i_kwh = get_unit_value($b->{integrated}, 'CHF_kWh');
    return $i_kwh + $r_kwh + $metering_kwh;
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
    
    if (! $has_start || !$has_end || !$has_elec || !$has_grid || !$has_integ || ! $has_regional || ! $has_metering) {
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

# --------------------------
# Canonical tariff data builder
# --------------------------
sub build_canonical_tariff_data {
  my ($merged_rows, $publication_timestamp) = @_;
  
  # Sort input rows by start timestamp
  my @sorted = sort {
    ($a->{start_timestamp} // '') cmp ($b->{start_timestamp} // '')
  } grep { ref $_ eq 'HASH' && $_->{start_timestamp} } @$merged_rows;
  
  eval { LOGDEB("build_canonical_tariff_data: Processing " . scalar(@sorted) . " intervals"); 1; };
  
  # Build intervals_raw with numeric epochs
  my @intervals_raw;
  my %quarter_hour_map;  # Keyed by quarter-hour epoch for lookup
  
  for my $b (@sorted) {
    my $start_iso = $b->{start_timestamp} // next;
    my $end_iso = $b->{end_timestamp} // '';
    
    my $start_epoch = _iso_to_epoch($start_iso);
    my $end_epoch = _iso_to_epoch($end_iso);
    next unless defined $start_epoch;
    
    # Normalize to quarter-hour boundary
    my $q_epoch = int($start_epoch / 900) * 900;
    
    my ($Y, $M) = (parse_ymdh($start_iso))[0, 1];
    my $hours_in_month = days_in_month($Y, $M) * 24;
    
    my $kwh_total = kwh_total_for_block($b);
    my $monthly_m = monthly_M_total_for_block($b);
    my $fixed_per_hour = $hours_in_month ? ($monthly_m / $hours_in_month) : 0;
    my $sum_total = $kwh_total + $fixed_per_hour;
    
    my $entry = {
      start_epoch => $start_epoch + 0,
      end_epoch => (defined $end_epoch ? $end_epoch + 0 : $start_epoch + 900),
      start_timestamp => $start_iso,
      end_timestamp => $end_iso,
      chf_per_kwh_sum => $kwh_total + 0,
      chf_m_per_hour => $fixed_per_hour + 0,
      total_chf => $sum_total + 0,
      month_hours_used => $hours_in_month + 0,
    };
    
    push @intervals_raw, $entry;
    
    # Store in quarter-hour map (first occurrence wins)
    $quarter_hour_map{$q_epoch} = $entry unless exists $quarter_hour_map{$q_epoch};
  }
  
  # Build hourly_map by aggregating quarter-hour intervals
  my %hourly_map;
  
  for my $b (@sorted) {
    my $start_iso = $b->{start_timestamp};
    next unless defined $start_iso;
    
    my $start_epoch = _iso_to_epoch($start_iso);
    next unless defined $start_epoch;
    
    # Normalize to hour boundary
    my $hour_epoch = int($start_epoch / 3600) * 3600;
    
    # Generate hour_start ISO string from normalized epoch
    my @lt = localtime($hour_epoch);
    my $hour_iso = strftime('%Y-%m-%dT%H:00:00', @lt);
    my $tz_offset = strftime('%z', @lt);
    $tz_offset =~ s/^([+\-])(\d{2})(\d{2})$/$1$2:$3/;
    my $hour_start_iso = $hour_iso . $tz_offset;
    
    my ($Y, $M) = (parse_ymdh($start_iso))[0, 1];
    my $hours_in_month = days_in_month($Y, $M) * 24;
    
    my $kwh_total = kwh_total_for_block($b);
    my $monthly_m = monthly_M_total_for_block($b);
    my $fixed_per_hour = $hours_in_month ? ($monthly_m / $hours_in_month) : 0;
    my $sum_total = $kwh_total + $fixed_per_hour;
    
    unless (exists $hourly_map{$hour_epoch}) {
      $hourly_map{$hour_epoch} = {
        hour_start => $hour_start_iso,
        n => 0,
        kwh_sum => 0,
        fixed_sum => 0,
        total_sum => 0,
      };
    }
    
    $hourly_map{$hour_epoch}{n} += 1;
    $hourly_map{$hour_epoch}{kwh_sum} += $kwh_total;
    $hourly_map{$hour_epoch}{fixed_sum} += $fixed_per_hour;
    $hourly_map{$hour_epoch}{total_sum} += $sum_total;
  }
  
  # Compute averages for hourly_map
  for my $epoch (keys %hourly_map) {
    my $entry = $hourly_map{$epoch};
    my $n = $entry->{n} || 1;
    $entry->{avg_total_chf} = ($entry->{total_sum} / $n) + 0;
    $entry->{avg_chf_per_kwh_sum} = ($entry->{kwh_sum} / $n) + 0;
    $entry->{avg_chf_m_per_hour} = ($entry->{fixed_sum} / $n) + 0;
    $entry->{intervals_count} = $n + 0;
    # Clean up temporary fields
    delete $entry->{n};
    delete $entry->{kwh_sum};
    delete $entry->{fixed_sum};
    delete $entry->{total_sum};
  }
  
  # Build hourly_list sorted by epoch
  my @hourly_list;
  for my $epoch (sort { $a <=> $b } keys %hourly_map) {
    push @hourly_list, {
      hour_start => $hourly_map{$epoch}{hour_start},
      avg_total_chf => $hourly_map{$epoch}{avg_total_chf},
      avg_chf_per_kwh_sum => $hourly_map{$epoch}{avg_chf_per_kwh_sum},
      avg_chf_m_per_hour => $hourly_map{$epoch}{avg_chf_m_per_hour},
      intervals_count => $hourly_map{$epoch}{intervals_count},
    };
  }
  
  # Build intervals_filled with forward-fill logic for 48 hours
  my @lt_now = localtime(time);
  my $midnight_local_epoch = timelocal(0, 0, 0, $lt_now[3], $lt_now[4], $lt_now[5]);
  
  my @quarter_epochs_sorted = sort { $a <=> $b } keys %quarter_hour_map;
  my @hour_epochs_sorted = sort { $a <=> $b } keys %hourly_map;
  
  # Helper to get latest quarter-hour entry before or at epoch
  my $get_latest_q = sub {
    my ($epoch) = @_;
    return undef unless defined $epoch;
    return undef unless @quarter_epochs_sorted;
    
    my $k = int($epoch / 900) * 900;
    return $quarter_hour_map{$k} if exists $quarter_hour_map{$k};
    
    # Forward-fill: use last available if we're past the end
    return $quarter_hour_map{$quarter_epochs_sorted[-1]} if $epoch >= $quarter_epochs_sorted[-1];
    
    # Find the latest entry before this epoch
    for (my $i = $#quarter_epochs_sorted; $i >= 0; $i--) {
      my $e = $quarter_epochs_sorted[$i];
      return $quarter_hour_map{$e} if $e <= $epoch;
    }
    
    # Fallback to first entry
    return $quarter_hour_map{$quarter_epochs_sorted[0]};
  };
  
  # Helper to format local ISO with timezone
  my $format_q_iso = sub {
    my ($epoch) = @_;
    my @lt = localtime($epoch);
    my $iso = strftime('%Y-%m-%dT%H:%M:00', @lt);
    my $tz = strftime('%z', @lt);
    $tz =~ s/^([+\-])(\d{2})(\d{2})$/$1$2:$3/;
    return $iso . $tz;
  };
  
  my @intervals_filled;
  for my $q_off (0..191) {  # 192 quarter-hours = 48 hours
    my $t = $midnight_local_epoch + $q_off * 900;
    my $start_iso = $format_q_iso->($t);
    my $end_iso = $format_q_iso->($t + 900);
    
    my $src = $get_latest_q->($t);
    
    push @intervals_filled, {
      start_timestamp => $start_iso,
      end_timestamp => $end_iso,
      chf_per_kwh_sum => ($src ? $src->{chf_per_kwh_sum} + 0 : 0),
      chf_m_per_hour => ($src ? $src->{chf_m_per_hour} + 0 : 0),
      total_chf => ($src ? $src->{total_chf} + 0 : 0),
      month_hours_used => ($src ? $src->{month_hours_used} + 0 : 0),
    };
  }
  
  eval { LOGDEB("build_canonical_tariff_data: Built " . 
    scalar(@intervals_raw) . " raw intervals, " .
    scalar(keys %hourly_map) . " hourly entries, " .
    scalar(@intervals_filled) . " filled intervals"); 1; };
  
  return {
    intervals_raw => \@intervals_raw,
    hourly_map => \%hourly_map,
    hourly_list => \@hourly_list,
    intervals_filled => \@intervals_filled,
    quarter_hour_map => \%quarter_hour_map,
    publication_timestamp => $publication_timestamp,
  };
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
# Build canonical tariff data
# --------------------------
my $canonical = build_canonical_tariff_data(\@sorted, $pub_ts);

# Extract data for use in rest of script
my $intervals_raw = $canonical->{intervals_raw};
my $hourly_map = $canonical->{hourly_map};
my $hourly_list = $canonical->{hourly_list};
my $intervals_filled = $canonical->{intervals_filled};

# For backwards compatibility with existing code sections
my @intervals_raw = @$intervals_raw;
my @hourly_raw = @$hourly_list;
my @intervals_filled = @$intervals_filled;
my @hourly_filled;

# Optional debug mode
if ($debug_dump) {
  my @epochs = sort { $a <=> $b } keys %$hourly_map;
  my $first_epoch = @epochs ? $epochs[0] : 0;
  my $last_epoch = @epochs ? $epochs[-1] : 0;
  eval { LOGDEB("Canonical map debug: " . 
    scalar(@$intervals_raw) . " raw intervals, " .
    scalar(keys %$hourly_map) . " hourly entries, " .
    "first epoch=" . $first_epoch . " (" . strftime('%Y-%m-%d %H:%M:%S', localtime($first_epoch)) . "), " .
    "last epoch=" . $last_epoch . " (" . strftime('%Y-%m-%d %H:%M:%S', localtime($last_epoch)) . ")"); 1; };
}

# Build hourly_filled for 48 hours using canonical hourly_map
my @lt_now = localtime(time);
my $midnight_local_epoch = timelocal(0, 0, 0, $lt_now[3], $lt_now[4], $lt_now[5]);

my @hour_epochs_sorted = sort { $a <=> $b } keys %$hourly_map;

if (@hour_epochs_sorted == 0) {
  eval { LOGWARN("Midnight transition: No hourly data available"); 1; };
}

# Helper to get hourly entry at or before epoch
my $get_hourly_at = sub {
  my ($epoch) = @_;
  return undef unless defined $epoch;
  return undef unless @hour_epochs_sorted;
  
  my $k = int($epoch / 3600) * 3600;
  return $hourly_map->{$k} if exists $hourly_map->{$k};
  
  # Forward-fill: use last available if past the end
  return $hourly_map->{$hour_epochs_sorted[-1]} if $epoch >= $hour_epochs_sorted[-1];
  
  # Find latest entry before this epoch
  for (my $i = $#hour_epochs_sorted; $i >= 0; $i--) {
    my $e = $hour_epochs_sorted[$i];
    return $hourly_map->{$e} if $e <= $epoch;
  }
  
  # Fallback to first entry
  return $hourly_map->{$hour_epochs_sorted[0]};
};

# Build hourly_filled for 48 hours
for my $h_off (0..47) {
  my $t = $midnight_local_epoch + $h_off * 3600;
  
  my @lt = localtime($t);
  my $iso = strftime('%Y-%m-%dT%H:00:00', @lt);
  my $tz = strftime('%z', @lt);
  $tz =~ s/^([+\-])(\d{2})(\d{2})$/$1$2:$3/;
  my $hour_start_iso = $iso . $tz;
  
  my $src = $get_hourly_at->($t);
  
  push @hourly_filled, {
    hour_start => $hour_start_iso,
    avg_total_chf => ($src ? $src->{avg_total_chf} + 0 : 0),
    avg_chf_per_kwh_sum => ($src ? $src->{avg_chf_per_kwh_sum} + 0 : 0),
    avg_chf_m_per_hour => ($src ? $src->{avg_chf_m_per_hour} + 0 : 0),
    intervals_count => ($src ? $src->{intervals_count} + 0 : 0),
  };
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

# Use canonical hourly_map for relative values
my %hour_epoch_map_rel = %$hourly_map;
my @hour_epochs_sorted_rel = sort { $a <=> $b } keys %hour_epoch_map_rel;

if (@hour_epochs_sorted_rel == 0) {
  eval { LOGERR("CRITICAL: hour_epoch_map_rel is EMPTY! Cannot calculate relative values!"); 1; };
} else {
  eval { LOGDEB("Relative map: " . scalar(@hour_epochs_sorted_rel) . " hours from canonical data"); 1; };
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
  
  my $target_epoch = $t;  # Use the normalized epoch directly instead of re-parsing
  
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
