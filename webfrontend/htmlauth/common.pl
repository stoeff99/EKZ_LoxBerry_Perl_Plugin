#!/usr/bin/perl
use strict;
use warnings;

use LoxBerry::System;
use JSON::PP;
use LWP::UserAgent;
use HTTP::Request::Common qw(POST);
use Time::Piece;
use File::Spec;
use File::Path qw(make_path);
use FindBin;


# SDK globals under strict
our ($lbpdatadir, $lbpurl, $lbptemplatedir);

# Plugin data dir via SDK (no hard-coded paths)
my $LBPDATADIR = $lbpdatadir;

sub load_cfg {
  my $path = File::Spec->catfile($LBPDATADIR, 'ekz_config.json');
  open my $fh, '<', $path or die "Config not found: $path";
  local $/ = undef;
  my $raw = <$fh>; close $fh;
  my $cfg = decode_json($raw);

  # defaults
  $cfg->{realm}               //= 'myEKZ';
  $cfg->{response_mode}       //= 'query';
  $cfg->{timezone}            //= 'Europe/Zurich';
  $cfg->{retries}             //= 3;
  $cfg->{mqtt_enabled}        //= JSON::PP::true;
  $cfg->{mqtt_host}           //= 'localhost';
  $cfg->{mqtt_port}           //= 1883;
  $cfg->{mqtt_username}       //= '';
  $cfg->{mqtt_password}       //= '';
  $cfg->{mqtt_topic_raw}      //= 'ekz/ems/tariffs/raw';
  $cfg->{mqtt_topic_summary}  //= 'ekz/ems/tariffs/now_plus_24h';
  $cfg->{fallback_tariff_name}//= 'electricity_standard';
  $cfg->{output_base}         //= 'ekz_customer_tariffs_now_plus_24h';
  $cfg->{token_store_path}    //= '';

  for my $k (qw/auth_server_base client_id client_secret redirect_uri api_base ems_instance_id scope/) {
    die "Missing cfg key: $k" unless $cfg->{$k};
  }
  return $cfg;
}

sub publish_mqtt {
  my ($cfg, $topic, $payload) = @_;

  return 1 unless $cfg->{mqtt_enabled};
  return 1 unless $topic;

  eval { require Net::MQTT::Simple; Net::MQTT::Simple->import(); 1 } or die "Net::MQTT::Simple not available";
  my $server = $cfg->{mqtt_host} . ':' . int($cfg->{mqtt_port} || 1883);
  my $mqtt   = Net::MQTT::Simple->new($server);

  if ($cfg->{mqtt_username}) {
    $mqtt->login($cfg->{mqtt_username}, $cfg->{mqtt_password} // '');
  }

  my $msg = ref($payload) ? encode_json($payload) : $payload;
  $mqtt->publish($topic => $msg);
  return 1;
}

sub _randhex {
  my ($len) = @_;
  my @hex = ('0'..'9', 'a'..'f');
  my $out = '';
  for (1..($len||16)) { $out .= $hex[int(rand(@hex))]; }
  return $out;
}

sub tokens_path {
  my ($cfg) = @_;
  if ($cfg->{token_store_path}) {
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
  my $res = $ua->request(POST $endpoint, [
    grant_type    => 'refresh_token',
    client_id     => $cfg->{client_id},
    client_secret => $cfg->{client_secret},
    refresh_token => $tok->{refresh_token},
  ]);
  die "Token refresh HTTP ".$res->code.": ".$res->decoded_content unless $res->is_success;

  my $j = decode_json($res->decoded_content);
  $tok->{access_token}  = $j->{access_token} // '';
  $tok->{refresh_token} = $j->{refresh_token} // $tok->{refresh_token};
  $tok->{expires_at}    = time() + int($j->{expires_in} // 300);
  save_tokens($tok, $cfg);
  return $tok->{access_token};
}

sub get_json_with_retry {
  my ($url, $headers, $params, $attempts) = @_;
  $attempts = ($attempts && $attempts > 0) ? $attempts : 3;
  my $ua = LWP::UserAgent->new(timeout => 30);
  my $qs = join '&', map { $_.'='.$params->{$_} } keys %$params;

  for my $i (0..$attempts-1) {
    my $req = HTTP::Request->new(GET => "$url?$qs");
    while (my ($k,$v) = each %$headers) { $req->header($k => $v) }
    my $res = $ua->request($req);
    if ($res->is_success) {
      return decode_json($res->decoded_content);
    }
    sleep($i == 0 ? 1 : (2**$i));
  }
  die "GET failed after $attempts attempts";
}

sub fetch_window {
  my ($cfg, $access, $start_iso, $end_iso) = @_;
  my %hdr = ( Authorization => "Bearer $access", accept => "application/json" );
  my $base = $cfg->{api_base};

  eval {
    my $payload = get_json_with_retry(
      "$base/customerTariffs", \%hdr,
      { ems_instance_id => $cfg->{ems_instance_id}, start_timestamp => $start_iso, end_timestamp => $end_iso },
      int($cfg->{retries})
    );
    return ($payload, 'customer');
  } or do {
    my $payload = get_json_with_retry(
      "$base/tariffs", \%hdr,
      { tariff_name => $cfg->{fallback_tariff_name} },
      int($cfg->{retries})
    );
    return ($payload, 'public');
  };
}

sub build_scheduled_window {
  # today 18:00 → +24h, local time
  my $now = localtime;
  my $start = Time::Piece->strptime($now->strftime('%Y-%m-%d').' 18:00:00', '%Y-%m-%d %H:%M:%S');
  my $end = $start + 24*60*60;
  return ($start->strftime('%Y-%m-%dT%H:%M:%S'), $end->strftime('%Y-%m-%dT%H:%M:%S'));
}

1;
