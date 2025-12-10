
#!/usr/bin/perl
use strict;
use warnings;

use LoxBerry::System;            # <-- SDK: provides $lbpdatadir, $lbpurl, etc.
use CGI;
use JSON::PP;
use File::Spec;
use File::Path qw(make_path);

my $q = CGI->new;
print $q->header('text/html; charset=utf-8');

# Use SDK-provided plugin data dir (matches plugin.cfg FOLDER)
my $LBPDATADIR = $lbpdatadir;
my $cfgfile    = File::Spec->catfile($LBPDATADIR, 'ekz_config.json');

# Ensure data folder exists
make_path($LBPDATADIR) unless -d $LBPDATADIR;

# -------- Defaults ----------
# NOTE: redirect_uri defaults to your plugin's callback CGI using $lbpurl
my %defaults = (
  auth_server_base     => 'https://login-test.ekz.ch/auth',
  realm                => 'myEKZ',
  client_id            => 'ems-bowles',
  client_secret        => '',
  redirect_uri         => "$lbpurl/callback.cgi",
  api_base             => 'https://test-api.tariffs.ekz.ch/v1',
  ems_instance_id      => 'ems-bowles',
  scope                => 'openid',           # add 'offline_access' if allowed
  response_mode        => 'query',
  timezone             => 'Europe/Zurich',
  mqtt_enabled         => JSON::PP::true,
  mqtt_topic_summary   => 'ekz/ems/tariffs/now_plus_24h',
  fallback_tariff_name => 'electricity_standard',
  retries              => 3,
  token_store_path     => ''
);

# -------- Load config (merge with defaults) ----------
my $cfg = { %defaults };
if (-f $cfgfile) {
  open my $fh, '<', $cfgfile;
  local $/ = undef;
  my $raw = <$fh>;
  close $fh;

  my $loaded = eval { decode_json($raw) };
  if ($loaded && ref $loaded eq 'HASH') {
    $cfg = { %defaults, %$loaded };
    # If redirect_uri was previously .pl, nudge to .cgi for consistency
    $cfg->{redirect_uri} =~ s/callback\.pl/callback.cgi/;
  }
}

# -------- Handle POST ----------
my $method = $q->request_method();
my $msg = '';

if ($method eq 'POST') {
  my @fields = qw/
    auth_server_base realm client_id redirect_uri api_base ems_instance_id
    scope response_mode timezone mqtt_topic_summary fallback_tariff_name
    token_store_path
  /;

  for my $f (@fields) {
    my $v = $q->param($f);
    $cfg->{$f} = defined $v ? $v : $cfg->{$f};
  }

  # mqtt_enabled checkbox
  $cfg->{mqtt_enabled} = $q->param('mqtt_enabled') ? JSON::PP::true : JSON::PP::false;

  # client_secret: only update if non-empty provided (prevent accidental blanking)
  if (defined $q->param('client_secret')) {
    my $newsec = $q->param('client_secret');
    if (defined $newsec && $newsec ne '') {
      $cfg->{client_secret} = $newsec;
    }
  }

  # Write file
  if (open my $fh, '>', $cfgfile) {
    print $fh encode_json($cfg);
    close $fh;
    chmod 0640, $cfgfile;
    $msg = "<div style='color:#080'>Settings saved.</div>";
  } else {
    $msg = "<div style='color:#b00'>Error: cannot write $cfgfile</div>";
  }
}

# -------- Render HTML ----------
# Precompute attributes for safe interpolation in heredoc
my $mqtt_checked = ($cfg->{mqtt_enabled} ? 'checked' : '');

print <<'HTML_HEAD';
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>EKZ Settings (Perl)</title>
  <style>
    body { font-family: system-ui, Arial, sans-serif; max-width: 780px; margin: 1.2rem auto; }
    fieldset { margin-bottom: 1rem; }
    label { display: block; margin: .4rem 0; }
    input[type=text], input[type=password] { width: 100%; max-width: 780px; }
    button { padding: .4rem .9rem; }
    .actions { margin-top: 1rem; }
  </style>
</head>
<body>
  <h2>EKZ Settings</h2>
HTML_HEAD

print $msg if $msg;

print <<HTML_FORM;
<form method="post">
  <fieldset><legend>EKZ / OIDC</legend>
    <label>Auth server base<br>
      <input name="auth_server_base" type="text" size="60" value="$cfg->{auth_server_base}">
    </label>
    <label>Realm<br>
      <input name="realm" type="text" value="$cfg->{realm}">
    </label>
    <label>Client ID<br>
      <input name="client_id" type="text" value="$cfg->{client_id}">
    </label>
    <label>Client secret<br>
      <input type="password" name="client_secret" placeholder="(enter to update)">
    </label>
    <label>Redirect URI<br>
      <input name="redirect_uri" type="text" size="80" value="$cfg->{redirect_uri}">
    </label>
    <label>API base<br>
      <input name="api_base" type="text" size="60" value="$cfg->{api_base}">
    </label>
    <label>EMS instance ID<br>
      <input name="ems_instance_id" type="text" value="$cfg->{ems_instance_id}">
    </label>
    <label>Scope<br>
      <input name="scope" type="text" value="$cfg->{scope}">
      <small>Use <code>openid offline_access</code> if allowed.</small>
    </label>
    <label>Response mode<br>
      <input name="response_mode" type="text" value="$cfg->{response_mode}">
    </label>
    <label>Timezone<br>
      <input name="timezone" type="text" value="$cfg->{timezone}">
    </label>
  </fieldset>

  <fieldset><legend>MQTT</legend>
    <label>
      <input type="checkbox" name="mqtt_enabled" $mqtt_checked> Enable MQTT
    </label>
    <label>Summary topic<br>
      <input name="mqtt_topic_summary" type="text" size="50" value="$cfg->{mqtt_topic_summary}">
    </label>
    <label>Fallback tariff name<br>
      <input name="fallback_tariff_name" type="text" value="$cfg->{fallback_tariff_name}">
    </label>
  </fieldset>

  <fieldset><legend>Advanced</legend>
    <label>Token store path (optional)<br>
      <input name="token_store_path" type="text" size="80" value="$cfg->{token_store_path}"><br>
      <small>Example: <code>/opt/loxberry/data/ekz/tokens.json</code></small>
    </label>
  </fieldset>

  <p class="actions">
    <button type="submit">Save</button>
    $lbpurl/index.htmlBack</a>
  </p>
</form>

</body>
</html>
HTML_FORM
