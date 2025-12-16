#!/usr/bin/perl
use strict;
use warnings;

use LoxBerry::System;
use LoxBerry::Log;
use JSON::PP;
use LWP::UserAgent;
use HTTP::Request::Common qw(POST);
use Time::Piece;
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
# Config loading
# --------------------------
sub _read_json_file {
  my ($path) = @_;
  open my $fh, '<', $path or die "Cannot read $path: $!";
  local $/ = undef;
  my $raw = <$fh>;
  close $fh;
  my $data = decode_json($raw);
  die "Invalid JSON in $path" unless ref $data eq 'HASH';
  return $data;
}

sub _shipped_default_cfg_path {
  # common.pl is in webfrontend/htmlauth; shipped defaults are at ../../config/ekz_config.json
  return File::Spec->catfile($FindBin::Bin, '../../config/ekz_config.json');
}

sub load_cfg {
  my $runtime = File::Spec->catfile($LBPDATADIR, 'ekz_config.json');
  my $cfg;

  if (-f $runtime) {
    $cfg = _read_json_file($runtime);
  } else {
    my $shipped = _shipped_default_cfg_path();
    die "Default config not found: $shipped" unless -f $shipped;
    $cfg = _read_json_file($shipped);
  }

  # Minimal runtime fallback: compute redirect_uri if missing
  if (!defined $cfg->{redirect_uri} || $cfg->{redirect_uri} eq '') {
    $cfg->{redirect_uri} = ($BASEURL ? "$BASEURL/callback.cgi" : '');
  }

  # Validate required keys exist in JSON
  for my $k (qw/auth_server_base realm client_id api_base ems_instance_id scope response_mode timezone/) {
    die "Missing cfg key: $k" unless defined $cfg->{$k};
  }

  return $cfg;
}

# --------------------------
# MQTT publish helper with fallback for insecure password login
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

    # If credentials are set and Net::MQTT::Simple blocks insecure login,
    # or any other failure happened with creds, fallback to mosquitto_pub.
    if (defined $user && $user ne '') {
      $used_cli_fallback = 1;

      # Write payload to a temp file and use mosquitto_pub -f to avoid quoting issues
      my $tmpfile = File::Spec->catfile($LBPDATADIR || '/tmp', "mqtt_payload_$$.json");
      eval {
        open my $tfh, '>', $tmpfile or die "Cannot write $tmpfile: $!";
        print $tfh $msg;
        close $tfh;
        1;
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
      # Anonymous publish failed; log and return
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
# Misc helpers
# --------------------------
sub _randhex {
  my ($len) = @_;
  my @hex = ('0'..'9', 'a'..'f');
  my $out = '';
  for (1..($len||16)) { $out .= $hex[int(rand(@hex))]; }
  return $out;
}

# --------------------------
# Tokens storage helpers
# --------------------------
sub tokens_path {
  my ($cfg) = @_;
  if ($cfg && $cfg->{token_store_path}) {
    my ($vol, $dir, undef) = File::Spec->splitpath($cfg->{token_store_path});
    make_path($dir) unless -d $dir;
    return $cfg->{token_store_path};
  }
  return File::Spec->catfile($LBPDATADIR, 'tokens.json');
}

sub load_tokens {
  my ($cfg) = @_;
  my $path = tokens_path($cfg);
  return {} unless -f $path;
  open my $fh, '<', $path or return {};
  local $/ = undef;
  my $raw = <$fh>; close $fh;
  my $tok = eval { decode_json($raw) } // {};
  return $tok;
}

sub save_tokens {
  my ($tok, $cfg) = @_;
  my $path = tokens_path($cfg);
  my ($vol, $dir, undef) = File::Spec->splitpath($path);
  make_path($dir) unless -d $dir;
  open my $fh, '>', $path or die "Cannot write $path: $!";
  print $fh encode_json($tok);
  close $fh;
  chmod 0640, $path;
}

# --------------------------
# Access token handling (refresh)
# Uses HTTP Basic auth for token endpoint
# --------------------------
sub ensure_access_token {
  my ($cfg) = @_;
  my $tok = load_tokens($cfg);

  if ($tok->{access_token} && $tok->{expires_at} && time() < ($tok->{expires_at} - 30)) {
    return $tok->{access_token};
  }
  unless ($tok->{refresh_token}) {
    die "No refresh_token; sign in via UI once (or include offline_access in scope).";
  }

  my $ua = LWP::UserAgent->new(timeout => 30);
  my $endpoint = $cfg->{auth_server_base} . "/realms/$cfg->{realm}/protocol/openid-connect/token";

  my $req = POST $endpoint, [
    grant_type    => 'refresh_token',
    refresh_token => $tok->{refresh_token},
  ];
  $req->authorization_basic($cfg->{client_id}, $cfg->{client_secret});

  my $res = $ua->request($req);
  die "Token refresh HTTP ".$res->code.": ".$res->decoded_content unless $res->is_success;

  my $j = decode_json($res->decoded_content);
  $tok->{access_token}  = $j->{access_token} // '';
  $tok->{refresh_token} = $j->{refresh_token} // $tok->{refresh_token};
  $tok->{expires_at}    = time() + int($j->{expires_in} // 300);
  save_tokens($tok, $cfg);
  return $tok->{access_token};
}

# --------------------------
# HTTP GET with retries and URL-encoded query string
# --------------------------
sub get_json_with_retry {
  my ($url, $headers, $params, $attempts) = @_;
  $attempts = ($attempts && $attempts > 0) ? $attempts : 3;
  my $ua = LWP::UserAgent->new(timeout => 30);

  # URL-encode query parameters
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
# Fetch Data Window
# --------------------------
sub _normalize_payload {
  my ($p) = @_;
  return {} unless defined $p && ref($p) eq 'HASH';

  # If API returned 'prices' (public format), map to 'rows'
  if (exists $p->{prices} && ref($p->{prices}) eq 'ARRAY' && (!exists $p->{rows} || ref($p->{rows}) ne 'ARRAY')) {
    $p->{rows} = $p->{prices};
  }

  # Ensure interval_count is present
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

sub fetch_window {
  my ($cfg, $access, $start_iso, $end_iso) = @_;

  my %hdr = ( Authorization => "Bearer $access", accept => "application/json" );
  my $base    = $cfg->{api_base};
  my $logfile = File::Spec->catfile($LBPDATADIR, 'fetch.log');

  my $attempts = int($cfg->{retries} || 3);

  # Helper to log a message to fetch.log (best-effort)
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

  # 1) Try customerTariffs
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
    my $err = $@ || 'unknown error';
    $log->("customerTariffs failed: $err");
    $payload = undef;
    $source  = undef;
  };

  # Normalize if needed and return if rows present
  if (defined $payload && ref($payload) eq 'HASH') {
    $payload = _normalize_payload($payload);
    if ($payload->{rows} && ref($payload->{rows}) eq 'ARRAY' && @{ $payload->{rows} }) {
      $log->("customerTariffs: returned " . scalar(@{$payload->{rows}}) . " rows");
      eval { publish_mqtt($cfg, $cfg->{mqtt_topic_summary}, { source => 'customer', from => $start_iso, to => $end_iso }); 1 } or warn "MQTT publish failed";
      return ($payload, 'customer');
    }
    $log->("customerTariffs returned empty rows (count=" . ($payload->{interval_count}//0) . "), falling back to public tariffs");
  }

  # 2) Try public /tariffs with fallback_tariff_name (if set)
  my $pub_payload;
  my $tariff_name = $cfg->{fallback_tariff_name} // '';

  if ($tariff_name ne '') {
    my $pub_params = {
      tariff_name     => $tariff_name,
      start_timestamp => $start_iso,
      end_timestamp   => $end_iso,
    };
    eval {
      $pub_payload = get_json_with_retry("$base/tariffs", \%hdr, $pub_params, $attempts);
      1;
    } or do {
      my $err = $@ || 'unknown error';
      $log->("public /tariffs (tariff_name=$tariff_name) failed: $err");
      $pub_payload = undef;
    };

    if (defined $pub_payload && ref($pub_payload) eq 'HASH') {
      $pub_payload = _normalize_payload($pub_payload);
      if ($pub_payload->{rows} && ref($pub_payload->{rows}) eq 'ARRAY' && @{ $pub_payload->{rows} }) {
        $log->("public /tariffs (tariff_name=$tariff_name): returned " . scalar(@{$pub_payload->{rows}}) . " rows");
        eval { publish_mqtt($cfg, $cfg->{mqtt_topic_summary}, { source => 'public', from => $start_iso, to => $end_iso }); 1 } or warn "MQTT publish failed";
        return ($pub_payload, 'public');
      }
      $log->("public /tariffs (tariff_name=$tariff_name) returned empty rows (count=" . ($pub_payload->{interval_count}//0) . ")");
    }
  } else {
    $log->("No fallback_tariff_name configured; skipping tariff_name-based public request");
  }

  # 3) Try public /tariffs without tariff_name (defaults)
  eval {
    $pub_payload = get_json_with_retry("$base/tariffs", \%hdr, { start_timestamp => $start_iso, end_timestamp => $end_iso }, $attempts);
    1;
  } or do {
    my $err = $@ || 'unknown error';
    $log->("public /tariffs (no tariff_name) failed: $err");
    $pub_payload = undef;
  };

  if (defined $pub_payload && ref($pub_payload) eq 'HASH') {
    $pub_payload = _normalize_payload($pub_payload);
    if ($pub_payload->{rows} && ref($pub_payload->{rows}) eq 'ARRAY' && @{ $pub_payload->{rows} }) {
      $log->("public /tariffs (no tariff_name): returned " . scalar(@{$pub_payload->{rows}}) . " rows");
      eval { publish_mqtt($cfg, $cfg->{mqtt_topic_summary}, { source => 'public', from => $start_iso, to => $end_iso }); 1 } or warn "MQTT publish failed";
      return ($pub_payload, 'public');
    }
    $log->("public /tariffs (no tariff_name) returned empty rows (count=" . ($pub_payload->{interval_count}//0) . ")");
  }

  # Nothing returned rows; log and return empty payload with 'public' source
  $log->("No tariff rows found from customerTariffs or public /tariffs endpoints; returning empty payload.");
  $pub_payload = {} unless defined $pub_payload && ref($pub_payload) eq 'HASH';
  $pub_payload = _normalize_payload($pub_payload);
  return ($pub_payload, 'public');
}


# --------------------------
# Saves tariffs to JSON
# --------------------------

sub save_tariffs_json {
  my ($cfg, $payload, $source, $start_iso, $end_iso) = @_;
  return 1 unless $cfg && $payload && ref($payload) eq 'HASH';

  my $dir = $LBPDATADIR || '/opt/loxberry/data';
  eval { File::Path::make_path($dir) unless -d $dir; 1 } or return 0;

  # Filenames: latest.json and a windowed file
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
    open my $fh, '>', $window_file or die "Cannot write $window_file: $!";
    print $fh encode_json($doc);
    close $fh;
    chmod 0640, $window_file;

    open my $fl, '>', $latest_file or die "Cannot write $latest_file: $!";
    print $fl encode_json($doc);
    close $fl;
    chmod 0640, $latest_file;
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
# EMS linking & helpers (must appear AFTER get_json_with_retry and fetch_window)
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
  # today 18:00 local → +24h, include timezone offset like +01:00
  my $now = localtime;
  my $start = Time::Piece->strptime($now->strftime('%Y-%m-%d').' 18:00:00', '%Y-%m-%d %H:%M:%S');
  my $end = $start + 24*60*60;

  my $off = $now->strftime('%z');            # e.g. +0100
  $off =~ s/^([+-])(\d{2})(\d{2})$/$1$2:$3/; # +0100 -> +01:00

  my $start_iso = $start->strftime('%Y-%m-%dT%H:%M:%S') . $off;
  my $end_iso   = $end->strftime('%Y-%m-%dT%H:%M:%S') . $off;
  return ($start_iso, $end_iso);
}

1;
