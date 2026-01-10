#!/usr/bin/perl
use strict;
use warnings;
use Time::Piece;
use CGI::Carp qw(fatalsToBrowser);
use CGI;
use JSON::PP;
use File::Spec;
use POSIX qw(strftime);
use Tie::IxHash;           # preserve object key order
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

# --------------------------
# Helpers: normalization
# --------------------------
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

  tie my %doc, 'Tie::IxHash';
  %doc = (
    publication_timestamp => $pub,
    prices                => \@out,   # keep original key
    rows                  => \@out,   # add rows for compute_costs.cgi compatibility
  );
  return \%doc;
}

# --------------------------
# Helpers: values and fallback checks
# --------------------------
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

sub _set_value_for_unit {
  my ($arr, $unit, $new_value) = @_;
  $arr ||= [];
  my $found = 0;
  for my $e (@$arr) {
    next unless ref($e) eq 'HASH';
    my $u = _norm_unit_name($e->{unit});
    if ($u eq $unit) {
      $e->{value} = ($new_value + 0);
      $found = 1;
      last;
    }
  }
  if (!$found) {
    push @$arr, { unit => $unit, value => ($new_value + 0) };
  }
  return $arr;
}

sub integrated_nonzero_share {
  my ($rows) = @_;
  return 0 unless $rows && ref($rows) eq 'ARRAY' && @$rows;
  my $n = scalar(@$rows);
  my $nz = 0;
  for my $r (@$rows) {
    my $ikwh = _value_for_unit($r->{integrated}, 'CHF_kWh');
    $nz++ if ($ikwh // 0) > 0;
  }
  return $nz / $n;
}

sub apply_fixed_regional_fee_to_integrated {
  my ($payload, $fee_kwh, $also_zero_regional) = @_;
  my $rows = $payload->{rows} // $payload->{prices} // [];
  for my $r (@$rows) {
    my $ikwh = _value_for_unit($r->{integrated}, 'CHF_kWh');
    $ikwh += ($fee_kwh + 0);
    $r->{integrated} = _set_value_for_unit($r->{integrated}, 'CHF_kWh', $ikwh);

    # Ensure integrated CHF_M key exists (keep as-is or 0)
    my $im = _value_for_unit($r->{integrated}, 'CHF_M');
    $r->{integrated} = _set_value_for_unit($r->{integrated}, 'CHF_M', $im);

    # Optionally zero-out regional_fees CHF_kWh to avoid double counting
    if ($also_zero_regional) {
      my $rm = _value_for_unit($r->{regional_fees}, 'CHF_M');
      $r->{regional_fees} = _set_value_for_unit($r->{regional_fees}, 'CHF_M', $rm); # keep CHF_M as-is
      $r->{regional_fees} = _set_value_for_unit($r->{regional_fees}, 'CHF_kWh', 0.0);
    } else {
      # Ensure regional CHF_kWh exists (even if fee is fixed, leave as-is)
      my $rkwh = _value_for_unit($r->{regional_fees}, 'CHF_kWh'); # no change
      $r->{regional_fees} = _set_value_for_unit($r->{regional_fees}, 'CHF_kWh', $rkwh);
    }
  }

  # Reflect adjusted rows into both keys if only one exists
  if (exists $payload->{rows} && ref($payload->{rows}) eq 'ARRAY') {
    $payload->{prices} = $payload->{rows};
  } elsif (exists $payload->{prices} && ref($payload->{prices}) eq 'ARRAY') {
    $payload->{rows} = $payload->{prices};
  }
  return $payload;
}

# --------------------------
# Helpers: event/raw logging (added)
# --------------------------
sub _iso_now {
  return strftime('%Y-%m-%dT%H:%M:%S', localtime) . _tz_offset_colon();
}

sub _ensure_dir {
  my ($dir) = @_;
  return if -d $dir;
  require File::Path;
  File::Path::make_path($dir);
}

sub _cleanup_old_files {
  my ($dir, $prefix, $retain_days) = @_;
  return unless -d $dir && $retain_days && $retain_days > 0;
  opendir(my $dh, $dir) or return;
  my $cutoff = time() - ($retain_days * 24 * 3600);
  while (my $f = readdir($dh)) {
    next unless $f =~ /^\Q$prefix\E/;
    my $p = File::Spec->catfile($dir, $f);
    my @st = stat($p);
    next unless @st;
    unlink $p if $st[9] < $cutoff;
  }
  closedir($dh);
}

sub _event_log_path {
  my ($date_str) = @_;
  $date_str ||= strftime('%Y%m%d', localtime);
  return File::Spec->catfile($lbplogdir, "fetch_events-$date_str.log");
}

sub log_event {
  my ($cfg, $rid, $type, $data) = @_;
  return unless $cfg->{fetch_event_logging} // 1;
  my $date_str = strftime('%Y%m%d', localtime);
  my $path = _event_log_path($date_str);
  my $obj = {
    ts         => _iso_now(),
    request_id => $rid,
    type       => $type,
    %{$data // {}},
  };
  my $line = JSON::PP->new->canonical(1)->encode($obj) . "\n";
  if (open my $fh, '>>', $path) {
    print $fh $line;
    close $fh;
    chmod 0640, $path;
  }
  # Retention
  my $days = int($cfg->{fetch_event_retain_days} // EVENT_LOG_RETENTION_DAYS);
  _cleanup_old_files($lbplogdir, 'fetch_events-', $days);
}

sub save_raw_payload {
  my ($cfg, $rid, $label, $payload) = @_;
  return unless $cfg->{fetch_raw_archiving} // 1;
  my $raw_dir = File::Spec->catdir($lbplogdir, 'raw');
  _ensure_dir($raw_dir);
  my $ts = strftime('%Y%m%d-%H%M%S', localtime);
  my $fn = sprintf('%s_%s_%s.json', $label, $ts, $rid);
  my $path = File::Spec->catfile($raw_dir, $fn);
  if (open my $fh, '>', $path) {
    print $fh JSON::PP->new->pretty(1)->encode($payload);
    close $fh;
    chmod 0640, $path;
  }
  my $days = int($cfg->{fetch_raw_retain_days} // RAW_LOG_RETENTION_DAYS);
  _cleanup_old_files($raw_dir, '', $days);
  return $path;
}

# --------------------------
# Ring buffer storage for fetch records
# --------------------------
sub save_fetch_record {
  my ($record) = @_;
  
  # Wrap entire operation in eval to ensure it never breaks the main fetch
  eval {
    # Create fetch_records directory if it doesn't exist
    my $records_dir = File::Spec->catdir($lbpdatadir, 'fetch_records');
    unless (-d $records_dir) {
      _ensure_dir($records_dir);
      chmod 0750, $records_dir;
    }
    
    # Rotate existing files: 09→delete, 08→09, ..., 01→02, 00→01
    # Start from the oldest (09) and work backwards
    my $oldest_file = File::Spec->catfile($records_dir, 'fetch_record_09.json');
    unlink $oldest_file if -f $oldest_file;
    
    # Rotate files 08→09, 07→08, ..., 00→01
    for (my $i = 8; $i >= 0; $i--) {
      my $old_num = sprintf('%02d', $i);
      my $new_num = sprintf('%02d', $i + 1);
      my $old_path = File::Spec->catfile($records_dir, "fetch_record_${old_num}.json");
      my $new_path = File::Spec->catfile($records_dir, "fetch_record_${new_num}.json");
      
      if (-f $old_path) {
        rename $old_path, $new_path;
      }
    }
    
    # Write the new record as fetch_record_00.json
    my $new_record_path = File::Spec->catfile($records_dir, 'fetch_record_00.json');
    if (open my $fh, '>', $new_record_path) {
      print $fh JSON::PP->new->pretty(1)->encode($record);
      close $fh;
      chmod 0640, $new_record_path;
      LOGINF("Saved fetch record to ring buffer: fetch_record_00.json");
    } else {
      LOGWARN("Failed to write fetch record to ring buffer: $!");
    }
    
    1;
  } or do {
    my $err = $@ // 'unknown error';
    LOGWARN("Ring buffer operation failed (non-fatal): $err");
  };
}

# --------------------------
# Main
# --------------------------

# Set timezone globally at startup
my $STARTUP_CFG = eval { load_cfg() };
if ($STARTUP_CFG && $STARTUP_CFG->{timezone}) {
  $ENV{TZ} = $STARTUP_CFG->{timezone};
  POSIX::tzset();
}

my $ok = eval {
  my $cfg = load_cfg();

  # Add a request correlation id for tracing
  my $rid = sprintf('%d-%d', time, $$);

  my $force      = ($q->param('force') // '') eq '1' ? 1 : 0;
  my $want_today = ($q->param('today') // '') eq '1' ? 1 : 0;
  my $schedule   = $cfg->{fetch_schedule} // '1';

  # Configurable grace minutes (default: 5)
  my $grace = int($cfg->{publish_grace_minutes} // GRACE_PERIOD_DEFAULT_MIN);

  log_event($cfg, $rid, 'start', {
    schedule    => $schedule,
    force       => ($force ? JSON::PP::true : JSON::PP::false),
    param_today => ($want_today ? JSON::PP::true : JSON::PP::false),
    grace_minutes => $grace,
  });

  my $last_file = File::Spec->catfile($lbpdatadir, 'last_fetch.json');

  if (!$force) {
    if (($schedule // '') eq '1') {
      if (-f $last_file) {
        if (open my $lf, '<', $last_file) {
          local $/ = undef;
          my $raw = <$lf>;
          close $lf;
          my $j = eval { decode_json($raw) } || {};
          my $last_ts = $j->{last_success_epoch};
          if (defined $last_ts && same_calendar_day(time, $last_ts)) {
            log_event($cfg, $rid, 'skip', { reason => 'already_fetched_today', last_success_epoch => $last_ts });
            LOGINF("Skipped fetch: already fetched today (once-per-day schedule)");
            print JSON::PP->new->encode({ skipped => JSON::PP::true, reason => 'already_fetched_today' });
            return 1;
          }
        }
      }
    }

    my $hour = (localtime(time))[2];
    my $allowed = 0;
    if    ($schedule eq '1')  { $allowed = ($hour >= 18) ? 1 : 0; }   # any time after 18:00
    elsif ($schedule eq '2')  { $allowed = ($hour == 6 || $hour == 18) ? 1 : 0; }
    elsif ($schedule eq '12') { $allowed = ($hour % 2 == 0) ? 1 : 0; }
    elsif ($schedule eq '24') { $allowed = 1; }
    else                      { $allowed = 0; }

    unless ($allowed) {
      log_event($cfg, $rid, 'skip', { reason => 'not_scheduled_now', current_hour => $hour, schedule => $schedule });
      LOGINF("Skipped fetch: not scheduled now (hour=$hour schedule=$schedule)");
      print JSON::PP->new->encode({ skipped => JSON::PP::true, reason => 'not_scheduled_now', hour => $hour, schedule => $schedule });
      return 1;
    }
  } else {
    LOGINF("Force fetch requested via ?force=1");
  }

  # Decide TODAY (<18:00) vs NEXT-DAY (>=18:00), with grace period at 18:00
  my $now_epoch = time();
  my @lt = localtime($now_epoch);
  my $now_hour = $lt[2];   # 0..23
  my $now_min  = $lt[1];   # 0..59

  # Rotate files at midnight if needed
  if ($now_hour == 0) {
    my $today_file = File::Spec->catfile($lbpdatadir, 'tariffs_today.json');
    my $tomorrow_file = File::Spec->catfile($lbpdatadir, 'tariffs_tomorrow.json');
    
    if (-f $tomorrow_file) {
      # Copy tomorrow to today (overwrite if exists)
      if (open my $src, '<', $tomorrow_file) {
        local $/ = undef;
        my $content = <$src>;
        close $src;
        if (open my $dst, '>', $today_file) {
          print $dst $content;
          close $dst;
          chmod 0640, $today_file;
          LOGINF("Rotated tariffs_tomorrow.json -> tariffs_today.json at midnight");
        } else {
          LOGWARN("Failed to write to $today_file during rotation: $!");
        }
      } else {
        LOGWARN("Failed to read $tomorrow_file during rotation: $!");
      }
      unlink $tomorrow_file or LOGWARN("Failed to delete $tomorrow_file after rotation: $!");
    }
  }

  if (!$want_today) {
    if ($force && $now_hour < 18) {
      $want_today = 1;
    } elsif ($now_hour < 18) {
      $want_today = 1;
    }
  }

  my ($start_iso, $end_iso);

  if ($want_today) {
    LOGINF("Building TODAY window (00:00..24:00 local)");
    ($start_iso, $end_iso) = build_today_window();
    log_event($cfg, $rid, 'window', { kind => 'today', from => $start_iso, to => $end_iso });
  } else {
    if ($now_hour == 18 && $now_min < $grace) {
      log_event($cfg, $rid, 'skip', { reason => 'within_grace_period', minute => $now_min, grace_minutes => $grace });
      LOGINF("Skipping next-day fetch: within grace period (minute=%d grace=%d)", $now_min, $grace);
      print JSON::PP->new->encode({
        skipped        => JSON::PP::true,
        reason         => 'within_grace_period',
        minute         => $now_min,
        grace_minutes  => $grace
      });
      return 1;
    }
    LOGINF("Building NEXT-DAY window (tomorrow 00:00..24:00 local)");
    ($start_iso, $end_iso) = build_tomorrow_window();

    if (!$force && $now_hour < 18) {
      log_event($cfg, $rid, 'skip', { reason => 'not_published_yet', current_hour => $now_hour });
      LOGINF("Skipping next-day fetch: not published yet (local hour=%d)", $now_hour);
      print JSON::PP->new->encode({ skipped => JSON::PP::true, reason => 'not_published_yet', hour => $now_hour });
      return 1;
    }
    log_event($cfg, $rid, 'window', { kind => 'nextday', from => $start_iso, to => $end_iso });
  }

  # ---- link / auth checks ----
  my ($link_status, $link_url) = try_ensure_linked($cfg);
  if ($link_status eq 'not_signed_in') {
    log_event($cfg, $rid, 'error', { reason => 'not_signed_in' });
    print encode_json({ error => 'not_signed_in', message => 'User not signed in. Please sign in via the plugin UI.' });
    return 1;
  }
  if ($link_status eq 'link_required') {
    log_event($cfg, $rid, 'error', { reason => 'link_required', link => $link_url });
    print encode_json({
      error => 'link_required',
      message => 'EMS is not linked to customer account. Redirect customer to linking flow.',
      linking_process_redirect_uri => $link_url,
    });
    return 1;
  }
  if ($link_status eq 'error') {
    my (undef, undef, $err) = try_ensure_linked($cfg);
    log_event($cfg, $rid, 'error', { reason => 'link_check_failed', detail => $err // 'Unknown error' });
    print encode_json({ error => 'link_check_failed', message => $err // 'Unknown error checking link status' });
    return 1;
  }

  # Fetch window and potentially apply fallback
  my $access = ensure_access_token($cfg);
  my ($payload, $source) = fetch_window($cfg, $access, $start_iso, $end_iso);
  
  # Track metadata for ring buffer
  my $initial_source = $source // 'unknown';
  my $fallback_applied = 0;
  my $window_kind = $want_today ? 'today' : 'nextday';

  # Next-day fallback to public tariffs if integrated CHF_kWh mostly zero after 18:00
  my $is_nextday = !$want_today;
  if (defined $payload && ref($payload) eq 'HASH' && $is_nextday && $now_hour >= 18) {
    my $rows_for_check = $payload->{rows} // $payload->{prices} // [];
    my $share = integrated_nonzero_share($rows_for_check);
    log_event($cfg, $rid, 'fetched', {
      initial_source => $source // 'unknown',
      intervals      => (ref($rows_for_check) eq 'ARRAY' ? scalar(@$rows_for_check) : 0),
      integrated_nonzero_share => sprintf('%.4f', $share),
    });
    save_raw_payload($cfg, $rid, 'raw_customer', $payload);

    if ($share < INTEGRATED_NONZERO_THRESHOLD) {
      my $base = $cfg->{api_base};
      my $tariff_name = $cfg->{fallback_tariff_name} // 'integrated_400D';
      LOGINF(sprintf("customerTariffs integrated CHF_kWh mostly zero (share=%.2f). Falling back to public tariff_name=%s", $share, $tariff_name));
      log_event($cfg, $rid, 'fallback_start', { reason => 'low_integrated_share', share => $share+0, tariff_name => $tariff_name });
      my $pub_payload;
      my $ok_pub = eval {
        $pub_payload = get_json_with_retry(
          "$base/tariffs",
          { accept => "application/json" },
          { tariff_name => $tariff_name, start_timestamp => $start_iso, end_timestamp => $end_iso },
          int($cfg->{retries} || 3)
        );
        1;
      };
      if ($ok_pub && defined $pub_payload && ref($pub_payload) eq 'HASH') {
        # Apply fixed regional fee to integrated for fallback result
        my $fee_kwh = ($cfg->{fallback_regional_fee_kwh} // REGIONAL_FEE_FALLBACK_KWH) + 0;
        my $zero_reg = !!($cfg->{fallback_zero_regional_when_applied} // JSON::PP::true);
        $pub_payload = apply_fixed_regional_fee_to_integrated($pub_payload, $fee_kwh, $zero_reg);

        $payload = $pub_payload;
        $source  = 'public';
        $fallback_applied = 1;
        LOGINF("Applied public fallback tariffs and folded regional fee (%.4f CHF/kWh) into integrated%s.",
          $fee_kwh, ($zero_reg ? " (regional CHF_kWh zeroed)" : ""));
        save_raw_payload($cfg, $rid, 'raw_public_fallback', $payload);
        log_event($cfg, $rid, 'fallback_applied', {
          fee_kwh      => $fee_kwh+0,
          zero_regional => ($zero_reg ? JSON::PP::true : JSON::PP::false),
        });
      } else {
        LOGERR("Public fallback tariffs failed; keeping customerTariffs payload. Error: " . ($@ // 'unknown'));
        log_event($cfg, $rid, 'fallback_failed', { error => ($@ // 'unknown') });
      }
    } else {
      save_raw_payload($cfg, $rid, 'raw_customer', $payload);
    }
  } else {
    # Non-nextday or before 18:00: still record the fetch
    if (defined $payload && ref($payload) eq 'HASH') {
      my $rows_for_check = $payload->{rows} // $payload->{prices} // [];
      log_event($cfg, $rid, 'fetched', {
        initial_source => $source // 'unknown',
        intervals      => (ref($rows_for_check) eq 'ARRAY' ? scalar(@$rows_for_check) : 0),
      });
      save_raw_payload($cfg, $rid, 'raw_customer', $payload);
    }
  }

  if (!defined $payload || ref($payload) ne 'HASH') {
    my $msg = "Unexpected response from fetch_window";
    LOGERR($msg);
    log_event($cfg, $rid, 'error', { reason => 'invalid_fetch_response' });
    print encode_json({ error => 'invalid_fetch_response', message => $msg });
    return 1;
  }

  my $norm = normalize_prices_doc($payload);

  # Save metadata (source, from, to) into the file so it's visible later
  $norm->{source} = $source // 'unknown';
  $norm->{from}   = $start_iso;
  $norm->{to}     = $end_iso;

  # Write to appropriate file based on window kind
  my $target_file;
  if ($want_today) {
    $target_file = File::Spec->catfile($lbpdatadir, 'tariffs_today.json');
    LOGINF("Saving TODAY data to tariffs_today.json");
  } else {
    $target_file = File::Spec->catfile($lbpdatadir, 'tariffs_tomorrow.json');
    LOGINF("Saving NEXT-DAY data to tariffs_tomorrow.json");
  }
  write_json_file($target_file, $norm);
  save_raw_payload($cfg, $rid, 'normalized', $norm);

  # Also maintain tariffs_latest.json for backward compatibility (always write today's data if available)
  my $latest = File::Spec->catfile($lbpdatadir, 'tariffs_latest.json');
  if ($want_today) {
    write_json_file($latest, $norm);
  }

  # Record last successful fetch
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

  # Per-day file
  if (@{ $norm->{prices} // [] }) {
    my $day = substr($norm->{prices}[0]{start_timestamp} // '', 0, 10);
    if ($day && $day =~ /^\d{4}-\d{2}-\d{2}$/) {
      my $byday = File::Spec->catfile($lbpdatadir, "tariffs_${day}.json");
      write_json_file($byday, $norm);
    }
  }

  # Publish raw payload
  my $mqtt_ok = publish_tariffs_to_mqtt($cfg, $payload, $source, $start_iso, $end_iso);
  log_event($cfg, $rid, 'publish_raw', { mqtt_ok => ($mqtt_ok ? JSON::PP::true : JSON::PP::false) });

  # Trigger compute publishes
  my $compute = File::Spec->catfile($FindBin::Bin, 'compute_costs.cgi');
  my $compute_rc = system('/usr/bin/perl', $compute);
  my $compute_ok = ($compute_rc == 0);
  log_event($cfg, $rid, 'compute', { ok => ($compute_ok ? JSON::PP::true : JSON::PP::false), rc => ($compute_rc >> 8) });
  LOGINF("compute_costs.cgi invoked to publish intervals/hourly.");

  # Response
  my $prices = $norm->{prices} // [];
  my $out = {
    publication_timestamp => $norm->{publication_timestamp},
    source                => $norm->{source},
    from                  => $norm->{from},
    to                    => $norm->{to},
    prices                => $prices,
    rows                  => $prices,
    interval_count        => scalar(@$prices),
    request_id            => $rid,
  };
  print JSON::PP->new->pretty(1)->encode($out);
  log_event($cfg, $rid, 'done', { intervals => scalar(@$prices), final_source => $norm->{source} });

  # Save fetch record to ring buffer
  my $rows_for_check = $payload->{rows} // $payload->{prices} // [];
  my $intervals_count = ref($rows_for_check) eq 'ARRAY' ? scalar(@$rows_for_check) : 0;
  my $integrated_share = $intervals_count > 0 ? integrated_nonzero_share($rows_for_check) : 0;
  
  my $fetch_record = {
    timestamp    => _iso_now(),
    request_id   => $rid,
    fetch_metadata => {
      schedule       => $schedule,
      force          => ($force ? JSON::PP::true : JSON::PP::false),
      param_today    => (($q->param('today') // '') eq '1' ? JSON::PP::true : JSON::PP::false),
      grace_minutes  => $grace,
    },
    window => {
      kind => $window_kind,
      from => $start_iso,
      to   => $end_iso,
    },
    api_response => {
      source                   => $initial_source,
      intervals                => $intervals_count,
      integrated_nonzero_share => sprintf('%.4f', $integrated_share),
    },
    raw_payload        => $payload,
    normalized_payload => $norm,
    fallback_applied   => ($fallback_applied ? JSON::PP::true : JSON::PP::false),
    mqtt_published     => ($mqtt_ok ? JSON::PP::true : JSON::PP::false),
    compute_completed  => ($compute_ok ? JSON::PP::true : JSON::PP::false),
  };
  
  save_fetch_record($fetch_record);

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
