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

# ---- helpers ----

sub read_latest_json {
  my $path = File::Spec->catfile($lbpdatadir, 'tariffs_latest.json');
  unless (-f $path) {
    print JSON::PP->new->encode({ error => "not_found", message => "No tariffs_latest.json in $lbpdatadir" });
    exit 0;
  }
  open my $fh, '<', $path or die "Cannot open $path: $!";
  local $/ = undef;
  my $raw = <$fh>;
  close $fh;
  my $doc = eval { decode_json($raw) };
  if (!$doc) {
    print JSON::PP->new->encode({ error => "invalid_json", message => "Could not parse tariffs_latest.json" });
    exit 0;
  }
  return ($path, $doc);
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
  # February
  my $leap = ($y % 400 == 0) || ($y % 4 == 0 && $y % 100 != 0);
  return $leap ? 29 : 28;
}

# Parse ISO8601 like 2025-12-18T18:15:00+01:00 (return YYYY,MM,DD,HH)
sub parse_ymdh {
  my ($iso) = @_;
  my ($Y,$m,$d,$H) = $iso =~ /^(\d{4})-(\d{2})-(\d{2})T(\d{2})/;
  return ($Y+0,$m+0,$d+0,$H+0);
}

# Build hour start string "YYYY-MM-DDTHH:00:00+ZZ:zz" using offset from input timestamp
sub hour_start_from {
  my ($iso) = @_;
  my ($date,$time,$off) = $iso =~ /^([^T]+)T([^+\-Z]+)([+\-]\d{2}:\d{2}|Z)?$/;
  $off = '+00:00' if !defined $off || $off eq 'Z';
  my ($Y,$M,$D,$h) = parse_ymdh($iso);
  return sprintf('%04d-%02d-%02dT%02d:00:00%s', $Y,$M,$D,$h,$off);
}

# Choose strategy to avoid double-counting "integrated":
# - Prefer electricity + grid + regional_fees (CHF_kWh)
# - If electricity/grid missing, fall back to integrated (+ regional_fees)
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

# Monthly CHF_M total (avoid double-counting):
# - Prefer integrated CHF_M + regional_fees CHF_M
# - Else sum electricity CHF_M + grid CHF_M + regional_fees CHF_M
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

# ---- main ----

my ($src_path, $doc) = read_latest_json();

# Accept either {prices=>[]} or {rows=>[]}
my $rows = $doc->{prices};
$rows = $doc->{rows} if !defined $rows;
$rows ||= [];

# Sort by start ascending for stable output
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

  my $kwh_total      = kwh_total_for_block($b);              # 1) variable per kWh (CHF_kWh)
  my $monthly_m      = monthly_M_total_for_block($b);        # CHF per month
  my $fixed_per_hour = $hours_in_month ? ($monthly_m / $hours_in_month) : 0;  # 2) CHF_M per hour for this month
  my $sum_total      = $kwh_total + $fixed_per_hour;         # 3) sum

  my $hour_key = hour_start_from($start);

  my $row = {
    start_timestamp  => $start,
    end_timestamp    => $end,
    chf_per_kwh_sum  => $kwh_total,       # 1)
    chf_m_per_hour   => $fixed_per_hour,  # 2)
    total_chf        => $sum_total,       # 3)
    month_hours_used => $hours_in_month,  # e.g., 744 in Dec
  };
  push @intervals, $row;

  # Group for hourly averages
  $hour_groups{$hour_key} ||= { n => 0, kwh_sum => 0, fixed_sum => 0, total_sum => 0 };
  $hour_groups{$hour_key}{n}         += 1;
  $hour_groups{$hour_key}{kwh_sum}   += $kwh_total;
  $hour_groups{$hour_key}{fixed_sum} += $fixed_per_hour;
  $hour_groups{$hour_key}{total_sum} += $sum_total;
}

# Build hourly averages (mean of the 4 intervals in each hour)
my @hourly;
for my $hk (sort keys %hour_groups) {
  my $g = $hour_groups{$hk};
  my $n = $g->{n} || 1;
  push @hourly, {
    hour_start          => $hk,
    avg_total_chf       => $g->{total_sum} / $n,
    avg_chf_per_kwh_sum => $g->{kwh_sum}   / $n,
    avg_chf_m_per_hour  => $g->{fixed_sum} / $n,  # usually equal across the four intervals
    intervals_count     => $n,
  };
}

my $out = {
  source_file             => $src_path,
  publication_timestamp   => $doc->{publication_timestamp} // '',
  interval_count_input    => scalar(@sorted),
  interval_count_output   => scalar(@intervals),
  intervals               => \@intervals,
  hourly                  => \@hourly,
  notes => [
    "chf_per_kwh_sum is the sum of electricity+grid+regional_fees CHF_kWh in each 15-min interval (integrated ignored to avoid double counting).",
    "chf_m_per_hour is the monthly fixed CHF_M divided by the number of hours in the interval's month (e.g., 31*24=744 for December).",
    "total_chf = chf_per_kwh_sum + chf_m_per_hour.",
    "Hourly averages are the mean of the four 15-min intervals within each hour.",
  ],
};

print JSON::PP->new->pretty(1)->encode($out);
exit 0;
