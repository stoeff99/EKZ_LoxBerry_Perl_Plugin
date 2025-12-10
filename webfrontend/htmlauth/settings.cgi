#!/usr/bin/perl
use strict;
use warnings;

# LoxBerry SDK
use LoxBerry::System;            # SDK for paths/urls/etc.
use CGI;
use JSON::PP;
use File::Spec;
use File::Path qw(make_path);

# --- CGI header ---
my $q = CGI->new;
print $q->header('text/html; charset=utf-8');

# --- Resolve plugin paths using SDK (functions are safer than bare globals) ---
# lbpdatadir(): /opt/loxberry/data/plugins/<folder>
# lbpurl():     /admin/loxberry/webfrontend/htmlauth/plugins/<folder>   (for auth)
my $LBPDATADIR = LoxBerry::System::lbpdatadir();
my $LBPURL     = LoxBerry::System::lbpurl();      # base URL for your plugin (auth)

# --- Ensure data dir exists ---
eval { make_path($LBPDATADIR) unless -d $LBPDATADIR; 1 } or do {
    print "<p style='color:#b00'>Failed to create data dir $LBPDATADIR: $@</p>";
    exit;
};

# --- Config file path ---
my $cfgfile = File::Spec->catfile($LBPDATADIR, 'ekz_config.json');

# --- Defaults ---
# NOTE: default redirect_uri uses your plugin's auth URL and points to callback.cgi
my %defaults = (
  auth_server_base     => 'https://login-test.ekz.ch/auth',
  realm                => 'myEKZ',
  client_id            => 'ems-bowles',
  client_secret        => '',
  redirect_uri         => "$LBPURL/callback.cgi",
  api_base             => 'https://test-api.tariffs.ekz.ch/v1',
  ems_instance_id      => 'ems-bowles',
  scope                => 'openid',         # add 'offline_access' if allowed
  response_mode        => 'query',
  timezone             => 'Europe/Zurich',
  mqtt_enabled         => JSON::PP::true,
  mqtt_topic_summary   => 'ekz/ems/tariffs/now_plus_24h',
  fallback_tariff_name => 'electricity_standard',
  retries              => 3,
  token_store_path     => ''
);

# --- Load config (merge with defaults) ---
my $cfg = { %defaults };
if (-f $cfgfile) {
    if (open my $fh, '<', $cfgfile) {
        local $/ = undef;
        my $raw = <$fh>;
        close $fh;
        my $loaded = eval { decode_json($raw) };
        if ($@) {
            print "<p style='color:#b00'>Invalid JSON in $cfgfile: $@</p>";
        } elsif ($loaded && ref $loaded eq 'HASH') {
            $cfg = { %defaults, %$loaded };
            # Normalize any old .pl redirect to .cgi
            $cfg->{redirect_uri} =~ s/callback\.pl/callback.cgi/;
        }
    } else {
        print "<p style='color:#b00'>Cannot read $cfgfile: $!</p>";
    }
}

# --- Handle POST ---
my $msg = '';
if ($q->request_method eq 'POST') {
    my @fields = qw/
      auth_server_base realm client_id redirect_uri api_base ems_instance_id
      scope response_mode timezone mqtt_topic_summary fallback_tariff_name
      token_store_path
    /;

    for my $f (@fields) {
        my $v = $q->param($f);
        $cfg->{$f} = defined $v ? $v : $cfg->{$f};
    }

    # Checkbox
    $cfg->{mqtt_enabled} = $q->param('mqtt_enabled') ? JSON::PP::true : JSON::PP::false;

    # Only set client_secret if non-empty provided
    if (defined $q->param('client_secret')) {
        my $newsec = $q->param('client_secret');
        if (defined $newsec && $newsec ne '') {
            $cfg->{client_secret} = $newsec;
        }
    }

    # Write JSON (with error reporting)
    if (open my $fh, '>', $cfgfile) {
        print $fh encode_json($cfg);
        close $fh;
        chmod 0640, $cfgfile;
        $msg = "<div style='color:#080'>Settings saved.</div>";
    } else {
        $msg = "<div style='color:#b00'>Error: cannot write $cfgfile: $!</div>";
    }
}

# --- Precompute attributes to avoid complex interpolation in heredocs ---
my $mqtt_checked = ($cfg->{mqtt_enabled} ? 'checked' : '');

# --- HTML ---
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

# Use sprintf to safely inject variables
printf <<'HTML_FORM', 
$cfg->{auth_server_base}, $cfg->{realm}, $cfg->{client_id},
$cfg->{redirect_uri}, $cfg->{api_base}, $cfg->{ems_instance_id},
$cfg->{scope}, $cfg->{response_mode}, $cfg->{timezone},
$mqtt_checked, $cfg->{mqtt_topic_summary}, $cfg->{fallback_tariff_name},
$cfg->{token_store_path}, $LBPURL;
<form method="post">

  <fieldset><legend>EKZ / OIDC</legend>
    <label>Auth server base<br>
      <input name="auth_server_base" type="text" size="60" value="%s">
    </label>
    <label>Realm<br>
      <input name="realm" type="text" value="%s">
    </label>
    <label>Client ID<br>
      <input name="client_id" type="text" value="%s">
    </label>
    <label>Client secret<br>
      <input type="password" name="client_secret" placeholder="(enter to update)">
    </label>
    <label>Redirect URI<br>
      <input name="redirect_uri" type="text" size="80" value="%s">
    </label>
    <label>API base<br>
      <input name="api_base" type="text" size="60" value="%s">
    </label>
    <label>EMS instance ID<br>
      <input name="ems_instance_id" type="text" value="%s">
    </label>
    <label>Scope<br>
      <input name="scope" type="text" value="%s">
      <small>Use <code>openid offline_access</code> if allowed.</small>
    </label>
    <label>Response mode<br>
      <input name="response_mode" type="text" value="%s">
    </label>
    <label>Timezone<br>
      <input name="timezone" type="text" value="%s">
    </label>
  </fieldset>

  <fieldset><legend>MQTT</legend>
    <label>
      <input type="checkbox" name="mqtt_enabled" %s> Enable MQTT
    </label>
    <label>Summary topic<br>
      <input name="mqtt_topic_summary" type="text" size="50" value="%s">
    </label>
    <label>Fallback tariff name<br>
      <input name="fallback_tariff_name" type="text" value="%s">
    </label>
  </fieldset>

  <fieldset><legend>Advanced</legend>
    <label>Token store path (optional)<br>
      <input name="token_store_path" type="text" size="80" value="%s"><br>
      <small>Example: <code>/opt/loxberry/data/ekz/tokens.json</code></small>
    </label>
  </fieldset>

  <p class="actions">
    <button type="submit">Save</button>
    %s/index.htmlBack</a>
  </p>
</form>

</body>
</html>
HTML_FORM

# End
