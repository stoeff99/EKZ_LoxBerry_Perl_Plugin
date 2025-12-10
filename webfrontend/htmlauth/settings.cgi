#!/usr/bin/perl
use strict;
use warnings;
use CGI;
use JSON::PP;
use File::Spec;
use File::Path qw(make_path);

my $q = CGI->new;
print $q->header('text/html; charset=utf-8');

#my $LBPDATADIR = '/opt/loxberry/data/plugins/ekz_dynamic_price_perl';
my $LBPDATADIR = $lbpdatadir; 
my $cfgfile = File::Spec->catfile($LBPDATADIR, 'ekz_config.json');
make_path($LBPDATADIR) unless -d $LBPDATADIR;

my %defaults = (
  auth_server_base   => 'https://login-test.ekz.ch/auth',
  realm              => 'myEKZ',
  client_id          => 'ems-bowles',
  client_secret      => '',
  redirect_uri       => 'https://ems.bowles.ch/admin/loxberry/webfrontend/htmlauth/plugins/ekz_loxberry_perl_plugin/callback.pl',
  api_base           => 'https://test-api.tariffs.ekz.ch/v1',
  ems_instance_id    => 'ems-bowles',
  scope              => 'openid',
  response_mode      => 'query',
  timezone           => 'Europe/Zurich',
  mqtt_enabled       => JSON::PP::true,
  mqtt_topic_summary => 'ekz/ems/tariffs/now_plus_24h',
  fallback_tariff_name=> 'electricity_standard',
  retries            => 3,
  token_store_path   => ''
);

# Load current cfg or defaults
my $cfg = { %defaults };
if (-f $cfgfile) {
  open my $fh, '<', $cfgfile; local $/ = undef; my $raw = <$fh>; close $fh;
  my $loaded = eval { decode_json($raw) };
  if ($loaded && ref $loaded eq 'HASH') { $cfg = { %defaults, %$loaded }; }
}

my $method = $q->request_method();
my $msg = '';

if ($method eq 'POST') {
  my @fields = qw/auth_server_base realm client_id redirect_uri api_base ems_instance_id scope response_mode timezone mqtt_topic_summary fallback_tariff_name token_store_path/;
  for my $f (@fields) { my $v = $q->param($f); $cfg->{$f} = defined $v ? $v : $cfg->{$f}; }

  # mqtt_enabled checkbox
  $cfg->{mqtt_enabled} = $q->param('mqtt_enabled') ? JSON::PP::true : JSON::PP::false;

  # client_secret: only update if non-empty provided
  if (defined $q->param('client_secret')) {
    my $newsec = $q->param('client_secret');
    if (defined $newsec && $newsec ne '') { $cfg->{client_secret} = $newsec; }
  }

  # write file
  open my $fh, '>', $cfgfile or do { $msg = "<div style='color:#b00'>Error: cannot write $cfgfile</div>"; goto RENDER; };
  print $fh encode_json($cfg);
  close $fh;
  chmod 0640, $cfgfile;
  $msg = "<div style='color:#080'>Settings saved.</div>";
}

RENDER:
print <<'HTML';
<!doctype html>
<html><head><meta charset="utf-8"><title>EKZ Settings (Perl)</title></head>
<body style="font-family:system-ui,Arial,sans-serif;max-width:780px;margin:1.2rem auto;">
<h2>EKZ Settings</h2>
HTML
print $msg if $msg;
print <<HTML;
<form method="post">
  <fieldset><legend>EKZ / OIDC</legend>
    <label>Auth server base<br><input name="auth_server_base" size="60" value="$cfg->{auth_server_base}"></label><br>
    <label>Realm<br><input name="realm" value="$cfg->{realm}"></label><br>
    <label>Client ID<br><input name="client_id" value="$cfg->{client_id}"></label><br>
    <label>Client secret<br><input type="password" name="client_secret" placeholder="(enter to update)"></label><br>
    <label>Redirect URI<br><input name="redirect_uri" size="80" value="$cfg->{redirect_uri}"></label><br>
    <label>API base<br><input name="api_base" size="60" value="$cfg->{api_base}"></label><br>
    <label>EMS instance ID<br><input name="ems_instance_id" value="$cfg->{ems_instance_id}"></label><br>
    <label>Scope<br><input name="scope" value="$cfg->{scope}"> <small>Use <code>openid offline_access</code> if allowed.</small></label><br>
    <label>Response mode<br><input name="response_mode" value="$cfg->{response_mode}"></label><br>
    <label>Timezone<br><input name="timezone" value="$cfg->{timezone}"></label>
  </fieldset>
  <fieldset><legend>MQTT</legend>
    <label><input type="checkbox" name="mqtt_enabled" @{[$cfg->{mqtt_enabled}?'checked':'']}> Enable MQTT</label><br>
    <label>Summary topic<br><input name="mqtt_topic_summary" size="50" value="$cfg->{mqtt_topic_summary}"></label><br>
    <label>Fallback tariff name<br><input name="fallback_tariff_name" value="$cfg->{fallback_tariff_name}"></label>
  </fieldset>
  <fieldset><legend>Advanced</legend>
    <label>Token store path (optional)<br><input name="token_store_path" size="80" value="$cfg->{token_store_path}"><br>
      <small>Example: <code>/opt/loxberry/data/ekz/tokens.json</code></small>
    </label>
  </fieldset>
  <p>
    <button type="submit">Save</button>
    <a href="index.html">Back</a>
  </p>
</form>
</body></html>
HTML
