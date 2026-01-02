#!/usr/bin/perl
use strict;
use warnings;

use LoxBerry::System;
use LoxBerry::Log;
use JSON::PP;
use LWP::UserAgent;
use HTTP::Request::Common qw(POST);
use Time::Piece;
use Time::Local qw(timegm);
use File::Spec;
use File::Path qw(make_path);
use FindBin;
use URI::Escape qw(uri_escape_utf8);

# SDK globals under strict
our ($lbpdatadir, $lbpurl, $lbptemplatedir);

# Plugin data dir via SDK (no hard-coded paths)
my $LBPDATADIR = $lbpdatadir;

# Derive a base URL fallback if $lbpurl is not set
my $BASEURL = $lbpurl;
if (!$BASEURL) {
  my $path = $ENV{SCRIPT_NAME} // '';
  $path =~ s{/[^/]+$}{};
  $BASEURL = $path || '';
}

# --------------------------
# Logging helper (uses LoxBerry::Log if available; falls back to data-dir file)
# --------------------------
sub _lb_log {
  my ($level, $msg) = @_;
  $level ||= 'INF';
  $msg   ||= '';
  my $ok = eval {
    if ($level eq 'ERR') { LOGERR $msg }
    elsif ($level eq 'DEB') { LOGDEB $msg }
    else { LOGINF $msg }
    1;
  };
  return if $ok;

  # Fallback to data-dir fetch.log
  my $logfile = File::Spec->catfile($LBPDATADIR || '/opt/loxberry/data', 'fetch.log');
  if (open my $fh, '>>', $logfile) {
    print $fh scalar(localtime) . " - $msg\n";
    close $fh;
  }
}

# --------------------------
# Defaults helper
# --------------------------
sub _default_cfg {
  return {
    auth_server_base      => '',
    realm                 => '',
    client_id             => '',
    client_secret         => '',
    redirect_uri          => ($BASEURL ? "$BASEURL/callback.cgi" : ''),
    api_base              => 'https://api.tariffs.ekz.ch/v1',
    ems_instance_id       => '',
    scope                 => 'openid offline_access ems',
    response_mode         => 'query',
    timezone              => 'Europe/Zurich',
    fallback_tariff_name  => 'integrated_400D',
    retries               => 3,
    token_store_path      => File::Spec->catfile($LBPDATADIR || '/opt/loxberry/data/plugins/ekz_plugin', 'tokens.json'),

    mqtt_enabled          => JSON::PP::true,
    mqtt_host             => 'localhost',
    mqtt_port             => 1883,
    mqtt_username         => '',
    mqtt_password         => '',
    mqtt_topic_raw        => 'ekz/ems/tariffs/raw',
    mqtt_topic_summary    => 'ekz/ems/tariffs/now_plus_24h',
    mqtt_topic_intervals  => 'ekz/ems/tariffs/intervals',
    mqtt_topic_hourly     => 'ekz/ems/tariffs/hourly',
  };
}

# --------------------------
# Config loading
# --------------------------
sub _read_json_file {
  my ($path) = @_;
  my $raw;
  if (!open my $fh, '<', $path) {
    _lb_log('ERR', "Cannot read $path: $!");
    return undef;
  }
  local $/ = undef;
  $raw = <$fh>;
  close $fh;

  my $data = eval { decode_json($raw) };
  if (!$data || ref $data ne 'HASH') {
    _lb_log('ERR', "Invalid JSON in $path; using defaults");
    return undef;
  }
  return $data;
}

sub _shipped_default_cfg_path {
  # common.pl is in webfrontend/htmlauth; shipped defaults are at ../../files/config/ekz_config.json
  return File::Spec->catfile($FindBin::Bin, '../../files/config/ekz_config.json');
}

sub load_cfg {
  my $runtime = File::Spec->catfile($LBPDATADIR, 'ekz_config.json');
  my $cfg;

  if (-f $runtime) {
    $cfg = _read_json_file($runtime);
  }

  if (!defined $cfg) {
    my $shipped = _shipped_default_cfg_path();
    if (-f $shipped) {
      $cfg = _read_json_file($shipped);
    } else {
      _lb_log('ERR', "Default config not found at $shipped; using built-in defaults");
      $cfg = _default_cfg();
    }
  }

  # Minimal runtime fallback: compute redirect_uri if missing
  if (!defined $cfg->{redirect_uri} || $cfg->{redirect_uri} eq '') {
    $cfg->{redirect_uri} = ($BASEURL ? "$BASEURL/callback.cgi" : '');
  }

  # Soft defaults to avoid CGI 500 if keys are missing
  $cfg->{auth_server_base}  ||= '';
  $cfg->{realm}             ||= '';
  $cfg->{client_id}         ||= '';
  $cfg->{client_secret}     ||= '';
  $cfg->{api_base}          ||= 'https://api.tariffs.ekz.ch/v1';
  $cfg->{ems_instance_id}   ||= '';
  $cfg->{scope}             ||= 'openid offline_access ems';
  $cfg->{response_mode}     ||= 'query';
  $cfg->{timezone}          ||= 'Europe/Zurich';
  $cfg->{fallback_tariff_name} ||= 'integrated_400D';
  $cfg->{retries}           = int($cfg->{retries} // 3);
  $cfg->{token_store_path}  ||= File::Spec->catfile($LBPDATADIR || '/opt/loxberry/data/plugins/ekz_plugin', 'tokens.json');

  # MQTT topic defaults (must NOT be removed)
  $cfg->{mqtt_enabled}          = !!($cfg->{mqtt_enabled});
  $cfg->{mqtt_host}             ||= 'localhost';
  $cfg->{mqtt_port}             = int($cfg->{mqtt_port} // 1883);
  $cfg->{mqtt_username}         ||= '';
  $cfg->{mqtt_password}         ||= '';
  $cfg->{mqtt_topic_raw}        ||= 'ekz/ems/tariffs/raw';
  $cfg->{mqtt_topic_summary}    ||= 'ekz/ems/tariffs/now_plus_24h';
  $cfg->{mqtt_topic_intervals}  ||= 'ekz/ems/tariffs/intervals';
  $cfg->{mqtt_topic_hourly}     ||= 'ekz/ems/tariffs/hourly';

  return $cfg;
}

# --------------------------
# MQTT publish helper
# --------------------------
sub publish_mqtt {
  my ($cfg, $topic, $payload) = @_;

  return 1 unless $cfg && $cfg->{mqtt_enabled};
  return 1 unless $topic;

  my $host = $cfg->{mqtt_host} // 'localhost';
  my $port = int($cfg->{mqtt_port} // 1883);
  my $user = $cfg->{mqtt_username} // '';
  my $pass = $cfg->{mqtt_password} // '';
  my $msg  = ref($payload) ? encode_json($payload) : $payload;

  my $ok = 1;
  my $used_cli_fallback = 0;

  my $try_net_mqtt_simple = sub {
    eval {
      require Net::MQTT::Simple;
      Net::MQTT::Simple->import();
      my $mqtt = Net::MQTT::Simple->new("$host:$port");

      # Only call login if credentials provided
      if (defined $user && $user ne '') {
        $mqtt->login($user, $pass // '');
      }

      $mqtt->publish($topic => $msg);
      1;
    };
  };

  my $net_ok = $try_net_mqtt_simple->();

  if (!$net_ok) {
    my $err = $@ || 'unknown';
    if (defined $user && $user ne '') {
      $used_cli_fallback = 1;

      my $tmpfile = File::Spec->catfile($LBPDATADIR || '/tmp', "mqtt_payload_$$.json");
      eval {
        open my $tfh, '>', $tmpfile or die "Cannot write $tmpfile: $!";
        print $tfh $msg; close $tfh; 1;
      } or do {
        $ok = 0;
        my $logfile = File::Spec->catfile($LBPDATADIR, 'fetch.log');
        if (open my $lf, '>>', $logfile) {
          print $lf scalar(localtime) . " - MQTT publish fallback failed: cannot write temp file ($@)\n";
          close $lf;
        }
        return $ok;
      };

      my @cmd = ('mosquitto_pub', '-h', $host, '-p', $port, '-t', $topic, '-f', $tmpfile);
      push @cmd, ('-u', $user, '-P', $pass // '') if $user ne '';

      my $rc = system(@cmd);
      my $exit = ($rc == -1) ? -1 : ($rc >> 8);
      unlink $tmpfile;

      if ($exit != 0) {
        $ok = 0;
        my $logfile = File::Spec->catfile($LBPDATADIR, 'fetch.log');
        if (open my $lf, '>>', $logfile) {
          print $lf scalar(localtime) . " - MQTT publish via mosquitto_pub failed (exit=$exit) cmd=[", join(' ', @cmd), "]\n";
          close $lf;
        }
      }
    } else {
      $ok = 0;
      my $logfile = File::Spec->catfile($LBPDATADIR, 'fetch.log');
      if (open my $lf, '>>', $logfile) {
        print $lf scalar(localtime) . " - MQTT publish failed via Net::MQTT::Simple: $err\n";
        close $lf;
      }
    }
  }

  if ($used_cli_fallback) {
    my $logfile = File::Spec->catfile($LBPDATADIR, 'fetch.log');
    if (open my $lf, '>>', $logfile) {
      print $lf scalar(localtime) . " - MQTT publish used mosquitto_pub fallback\n";
      close $lf;
    }
  }

  return $ok;
}

# --------------------------
# Completeness validators
# --------------------------
sub expected_intervals_between {
  my ($start_iso, $end_iso) = @_;
  my $s = Time::Piece->strptime(substr($start_iso,0,19), '%Y-%m-%dT%H:%M:%S')->epoch;
  my $e = Time::Piece->strptime(substr($end_iso,0,19),   '%Y-%m-%dT%H:%M:%S')->epoch;
  my $delta = $e - $s;
  my $count = int($delta / 900);
  return $count > 0 ? $count : 0;
}

sub _norm_unit_name {
  my ($u) = @_;
  return 'CHF_kWh' if defined $u && lc($u) =~ /^chf[_-]?kwh$/;
  return 'CHF_M'   if defined $u && lc($u) =~ /^chf[_-]?m$/;
  return $u // 'CHF_kWh';
}

sub _block_chf_kwh_total {
  my ($b) = @_;
  return 0 unless defined $b && ref($b) eq 'HASH';
  my ($e_kwh, $g_kwh, $r_kwh, $i_kwh) = (0,0,0,0);
  for my $arr_name (qw/electricity grid regional_fees integrated/) {
    my $arr = $b->{$arr_name};
    next unless $arr && ref($arr) eq 'ARRAY';
    for my $e (@$arr) {
      next unless ref($e) eq 'HASH';
      my $unit = _norm_unit_name($e->{unit});
      next unless $unit eq 'CHF_kWh';
      my $val = $e->{value};
      $val = 0 unless defined $val;
      if ($arr_name eq 'electricity') { $e_kwh = $val + 0; }
      elsif ($arr_name eq 'grid')     { $g_kwh = $val + 0; }
      elsif ($arr_name eq 'regional_fees') { $r_kwh = $val + 0; }
      elsif ($arr_name eq 'integrated')    { $i_kwh = $val + 0; }
    }
  }
  return ($e_kwh || $g_kwh) ? ($e_kwh + $g_kwh + $r_kwh) : ($i_kwh + $r_kwh);
}

sub rows_nonzero_share {
  my ($rows) = @_;
  return 0 unless $rows && ref($rows) eq 'ARRAY' && @$rows;
  my $n = scalar(@$rows);
  my $nz = 0;
  for my $r (@$rows) {
    $nz++ if _block_chf_kwh_total($r) > 0;
  }
  return $nz / $n;
}

sub payload_is_complete {
  my ($payload, $start_iso, $end_iso) = @_;
  return 0 unless $payload && ref($payload) eq 'HASH';
  my $rows = $payload->{rows} // $payload->{prices} // [];
  my $count = ref($rows) eq 'ARRAY' ? scalar(@$rows) : 0;
  my $exp = expected_intervals_between($start_iso, $end_iso);
  return 0 if $exp <= 0;
  return 0 unless $count == $exp;
  my $share = rows_nonzero_share($rows);
  return $share >= 0.90 ? 1 : 0;
}

# --------------------------
# HTTP GET with retries and URL-encoded query string
# --------------------------
sub get_json_with_retry {
  my ($url, $headers, $params, $attempts) = @_;
  $attempts = ($attempts && $attempts > 0) ? $attempts : 3;
  my $ua = LWP::UserAgent->new(timeout => 30);

  my $qs = '';
  if ($params && ref $params eq 'HASH' && keys %$params) {
    my @pairs = map {
      my $k = $_;
      my $v = defined $params->{$k} ? $params->{$k} : '';
      uri_escape_utf8($k) . '=' . uri_escape_utf8($v)
    } sort keys %$params;
    $qs = join '&', @pairs;
  }
  my $full_url = $qs ne '' ? "$url?$qs" : $url;

  my ($last_code, $last_body) = (undef, undef);

  for my $i (0..$attempts-1) {
    my $req = HTTP::Request->new(GET => $full_url);
    for my $k (keys %{$headers // {}}) {
      $req->header($k => $headers->{$k});
    }
    my $res = $ua->request($req);
    if ($res->is_success) {
      return decode_json($res->decoded_content);
    }
    $last_code = $res->code;
    $last_body = eval { $res->decoded_content } // '';
    sleep($i == 0 ? 1 : (2**$i));
  }
  die "GET $full_url failed after $attempts attempts; last HTTP $last_code: $last_body";
}

# --------------------------
# Payload normalization and public fallback helper
# --------------------------
sub _normalize_payload {
  my ($p) = @_;
  return {} unless defined $p && ref($p) eq 'HASH';
  if (exists $p->{prices} && ref($p->{prices}) eq 'ARRAY' && (!exists $p->{rows} || ref($p->{rows}) ne 'ARRAY')) {
    $p->{rows} = $p->{prices};
  }
  unless (defined $p->{interval_count}) {
    if (exists $p->{rows} && ref($p->{rows}) eq 'ARRAY') {
      $p->{interval_count} = scalar @{ $p->{rows} };
    } elsif (exists $p->{prices} && ref($p->{prices}) eq 'ARRAY') {
      $p->{interval_count} = scalar @{ $p->{prices} };
    } else {
      $p->{interval_count} = 0;
    }
  }
  return $p;
}

sub fetch_public_tariffs_by_name {
  my ($cfg, $start_iso, $end_iso, $tariff_name) = @_;
  my %hdr = ( accept => "application/json" );
  my $base = $cfg->{api_base};
  return undef unless $tariff_name;
  my $payload;
  eval {
    $payload = get_json_with_retry("$base/tariffs", \%hdr, {
      tariff_name     => $tariff_name,
      start_timestamp => $start_iso,
      end_timestamp   => $end_iso,
    }, int($cfg->{retries} || 3));
    1;
  } or do {
    my $err = $@ || 'unknown error';
    _lb_log('ERR', "public /tariffs (tariff_name=$tariff_name) failed: $err");
    return undef;
  };
  return _normalize_payload($payload);
}

# --------------------------
# Window fetch with layered fallbacks
# --------------------------
sub fetch_window {
  my ($cfg, $access, $start_iso, $end_iso) = @_;

  my %hdr = ( Authorization => "Bearer $access", accept => "application/json" );
  my $base    = $cfg->{api_base};
  my $logfile = File::Spec->catfile($LBPDATADIR, 'fetch.log');
  my $attempts = int($cfg->{retries} || 3);

  my $log = sub {
    my ($m) = @_;
    eval {
      if (open my $fh, '>>', $logfile) {
        print $fh scalar(localtime) . " - $m\n";
        close $fh;
      }
      1;
    };
  };

  my ($payload, $source);

  # 1) Customer tariffs
  my $cust_params = {
    ems_instance_id => $cfg->{ems_instance_id},
    start_timestamp => $start_iso,
    end_timestamp   => $end_iso,
  };

  eval {
    $payload = get_json_with_retry("$base/customerTariffs", \%hdr, $cust_params, $attempts);
    $source  = 'customer';
    1;
  } or do {
    $log->("customerTariffs failed: " . ($@ || 'unknown'));
    $payload = undef; $source = undef;
  };

  if (defined $payload && ref($payload) eq 'HASH') {
    $payload = _normalize_payload($payload);
    my $rows = $payload->{rows} // [];
    my $count = scalar(@$rows);
    my $exp   = expected_intervals_between($start_iso, $end_iso);
    my $share = rows_nonzero_share($rows);
    $log->("customerTariffs: rows=$count expected=$exp nonzero_share=$share");
    if ($count == $exp && $share >= 0.90) {
      eval { publish_mqtt($cfg, $cfg->{mqtt_topic_summary}, { source => 'customer', from => $start_iso, to => $end_iso }); 1 };
      return ($payload, 'customer');
    }
    $log->("customerTariffs incomplete; trying public fallbacks");
  }

  # 2) Public tariffs by fallback_tariff_name
  my $tariff_name = $cfg->{fallback_tariff_name} // '';
  my $pub_payload;

  if ($tariff_name ne '') {
    $pub_payload = fetch_public_tariffs_by_name($cfg, $start_iso, $end_iso, $tariff_name);
    if (defined $pub_payload) {
      my $rows = $pub_payload->{rows} // [];
      my $count = scalar(@$rows);
      my $exp   = expected_intervals_between($start_iso, $end_iso);
      my $share = rows_nonzero_share($rows);
      $log->("public /tariffs (tariff_name=$tariff_name): rows=$count expected=$exp nonzero_share=$share");
      if ($count == $exp && $share >= 0.90) {
        eval { publish_mqtt($cfg, $cfg->{mqtt_topic_summary}, { source => 'public', from => $start_iso, to => $end_iso }); 1 };
        return ($pub_payload, 'public');
      }
      $log->("public /tariffs (tariff_name=$tariff_name) incomplete; trying public no-name");
    } else {
      $log->("public /tariffs (tariff_name=$tariff_name) failed; trying public no-name");
    }
  } else {
    $log->("No fallback_tariff_name configured; skipping tariff_name-based public request");
  }

  # 3) Public tariffs without tariff_name
  eval {
    $pub_payload = get_json_with_retry("$base/tariffs", \%hdr, { start_timestamp => $start_iso, end_timestamp => $end_iso }, $attempts);
    1;
  } or do {
    $log->("public /tariffs (no tariff_name) failed: " . ($@ || 'unknown'));
    $pub_payload = undef;
  };

  if (defined $pub_payload && ref($pub_payload) eq 'HASH') {
    $pub_payload = _normalize_payload($pub_payload);
    my $rows2 = $pub_payload->{rows} // [];
    my $count2 = scalar(@$rows2);
    my $exp2   = expected_intervals_between($start_iso, $end_iso);
    my $share2 = rows_nonzero_share($rows2);
    $log->("public /tariffs (no tariff_name): rows=$count2 expected=$exp2 nonzero_share=$share2");
    if ($count2 == $exp2 && $share2 >= 0.90) {
      eval { publish_mqtt($cfg, $cfg->{mqtt_topic_summary}, { source => 'public', from => $start_iso, to => $end_iso }); 1 };
      return ($pub_payload, 'public');
    }
    $log->("public /tariffs (no tariff_name) incomplete.");
  }

  # Nothing complete; return normalized empty payload
  $log->("No complete tariff rows found; returning empty payload.");
  my $empty = _normalize_payload( {} );
  return ($empty, 'public');
}

# --------------------------
# Saves tariffs to JSON (atomic)
# --------------------------
sub save_tariffs_json {
  my ($cfg, $payload, $source, $start_iso, $end_iso) = @_;
  return 1 unless $cfg && $payload && ref($payload) eq 'HASH';

  my $dir = $LBPDATADIR || '/opt/loxberry/data';
  eval { File::Path::make_path($dir) unless -d $dir; 1 } or return 0;

  (my $start_safe = $start_iso) =~ s/[:+]/_/g;
  (my $end_safe   = $end_iso)   =~ s/[:+]/_/g;
  my $window_file = File::Spec->catfile($dir, "tariffs_${source}_${start_safe}_${end_safe}.json");
  my $latest_file = File::Spec->catfile($dir, "tariffs_latest.json");

  my $doc = {
    source         => $source || 'unknown',
    from           => $start_iso,
    to             => $end_iso,
    interval_count => $payload->{interval_count} // 0,
    rows           => $payload->{rows} // [],
  };

  eval {
    # atomic write via temp
    my $tmp = "$latest_file.tmp.$$";
    open my $fl, '>', $tmp or die "Cannot write $tmp: $!";
    print $fl encode_json($doc); close $fl; chmod 0640, $tmp;
    rename $tmp, $latest_file or die "Cannot rename $tmp to $latest_file: $!";

    open my $fh, '>', $window_file or die "Cannot write $window_file: $!";
    print $fh encode_json($doc); close $fh; chmod 0640, $window_file;
    1;
  } or do {
    my $logfile = File::Spec->catfile($dir, 'fetch.log');
    if (open my $lf, '>>', $logfile) {
      print $lf scalar(localtime) . " - save_tariffs_json failed: $@\n";
      close $lf;
    }
    return 0;
  };

  return 1;
}

# Publish full tariffs payload to MQTT (raw topic)
sub publish_tariffs_to_mqtt {
  my ($cfg, $payload, $source, $start_iso, $end_iso) = @_;
  return 1 unless $cfg && $cfg->{mqtt_enabled};
  return 1 unless $cfg->{mqtt_topic_raw};
  return 1 unless $payload && ref($payload) eq 'HASH';

  my $doc = {
    source         => $source || 'unknown',
    from           => $start_iso,
    to             => $end_iso,
    interval_count => $payload->{interval_count} // 0,
    rows           => $payload->{rows} // [],
  };

  my $ok = 1;
  eval { publish_mqtt($cfg, $cfg->{mqtt_topic_raw}, $doc); 1 } or do {
    $ok = 0;
    my $err = $@ || 'unknown';
    my $logfile = File::Spec->catfile($LBPDATADIR, 'fetch.log');
    if (open my $fh, '>>', $logfile) {
      print $fh scalar(localtime) . " - MQTT raw publish failed: $err\n";
      close $fh;
    }
  };
  return $ok;
}

# --------------------------
# EMS linking & helpers
# --------------------------
sub ems_link_status {
  my ($cfg, $access, $redirect_uri) = @_;
  my %hdr = ( Authorization => "Bearer $access", accept => "application/json" );
  my $base = $cfg->{api_base};

  my $params = {
    ems_instance_id => $cfg->{ems_instance_id},
    redirect_uri    => $redirect_uri,
  };

  return get_json_with_retry("$base/emsLinkStatus", \%hdr, $params, int($cfg->{retries}));
}

sub ensure_linked {
  my ($cfg) = @_;
  my $access = ensure_access_token($cfg);

  my $oauth_cb = ($cfg->{redirect_uri} && $cfg->{redirect_uri} ne '')
    ? $cfg->{redirect_uri}
    : ($BASEURL ? "$BASEURL/callback.cgi" : '');

  my $return_uri = $oauth_cb;
  $return_uri =~ s{/callback\.cgi$}{/link_return.cgi};

  my $st = ems_link_status($cfg, $access, $return_uri);

  if ($st && $st->{link_status} && $st->{link_status} eq 'link_required') {
    my $redir = $st->{linking_process_redirect_uri} || '';
    return ('link_required', $redir);
  }
  return ('linked', undef);
}

sub fetch_customer_tariffs_window {
  my ($cfg, $start_iso, $end_iso) = @_;
  my $access = ensure_access_token($cfg);
  my %hdr = ( Authorization => "Bearer $access", accept => "application/json" );
  my $base = $cfg->{api_base};

  return get_json_with_retry(
    "$base/customerTariffs", \%hdr,
    { ems_instance_id => $cfg->{ems_instance_id}, start_timestamp => $start_iso, end_timestamp => $end_iso },
    int($cfg->{retries})
  );
}

sub has_tokens {
  my ($cfg) = @_;
  my $tok = load_tokens($cfg) || {};
  return 1 if ($tok->{refresh_token});
  return 1 if ($tok->{access_token} && $tok->{expires_at} && time() < ($tok->{expires_at} - 30));
  return 0;
}

sub try_ensure_linked {
  my ($cfg) = @_;
  return ('not_signed_in', undef, undef) unless has_tokens($cfg);

  my ($status, $link_url);
  my $err;
  eval {
    ($status, $link_url) = ensure_linked($cfg);
    1;
  } or do {
    $err = $@ || 'unknown error';
    $status = 'error';
  };
  return ($status, $link_url, $err);
}

sub build_scheduled_window {
  # Build window for the "following day" (next-day 00:00 local -> +24h)
  my $now = localtime;
  my $tomorrow = $now + 24*3600;
  my $start = Time::Piece->strptime($tomorrow->strftime('%Y-%m-%d').' 00:00:00', '%Y-%m-%d %H:%M:%S');
  my $end = $start + 24*3600;

  my $off = $now->strftime('%z');            # e.g. +0100
  $off =~ s/^([+-])(\d{2})(\d{2})$/$1$2:$3/; # +0100 -> +01:00

  my $start_iso = $start->strftime('%Y-%m-%dT%H:%M:%S') . $off;
  my $end_iso   = $end->strftime('%Y-%m-%dT%H:%M:%S') . $off;
  return ($start_iso, $end_iso);
}

1;
