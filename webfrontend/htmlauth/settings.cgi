#!/usr/bin/perl
use strict;
use warnings;

use LoxBerry::System;            # provides $lbpdatadir, $lbpurl, etc.
use CGI;
use JSON::PP;
use File::Spec;
use File::Path qw(make_path);

my $q = CGI->new;
print $q->header('text/html; charset=utf-8');

# --- SDK globals (from LoxBerry::System) ---
my $LBPDATADIR = $lbpdatadir;    # e.g. /opt/loxberry/data/plugins/<folder>
my $LBPURL     = $lbpurl;        # e.g. /admin/loxberry/webfrontend/htmlauth/plugins/<folder>

# --- Ensure data dir exists ---
eval { make_path($LBPDATADIR) unless -d $LBPDATADIR; 1 } or do {
    print "<p style='color:#b00'>Failed to create data dir $LBPDATADIR: $@</p>";
    exit;
};

# --- Config file path ---
my $cfgfile = File::Spec->catfile($LBPDATADIR, 'ekz_config.json');

# --- Defaults ---
# NOTE: If you haven't renamed callback.pl -> callback.cgi yet, either rename it
# or temporarily change the default below back to .../callback.pl.
my %defaults = (
  auth_server_base     => 'https://login-test.ekz.ch/auth',
  realm                => 'myEKZ',
  client_id            => 'ems-bowles',
  client_secret        => '',
  redirect_uri         => "$LBPURL/callback.cgi",
  api_base             => 'https://test-api.tariffs.ekz.ch/v1',
  ems_instance_id      => 'ems-bowles',
  scope                => 'openid',            # add 'offline_access' if allowed
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

    # mqtt_enabled checkbox
    $cfg->{mqtt_enabled} = $q->param('mqtt_enabled') ? JSON::PP::true : JSON::PP::false;

    # client_secret: only update if non-empty provided
    if (defined $q->param('client_secret')) {
      my $newsec = $q->param('client_secret');
      if (defined $newsec && $newsec ne '') {
        $cfg->{client_secret} = $newsec;
      }
    }

    # write file
    if (open my $fh, '>', $cfgfile) {
        print $fh encode_json($cfg);
        close $fh;
        chmod 0640, $cfgfile;
        $msg = "<div style='color:#080'>Settings saved.</div>";
    } else {
        $msg = "<div style='color:#b00'>Error: cannot write $cfgfile: $!</div>";
    }
}

# --- Render HTML (no heredocs) ---
print '<!doctype html><html><head><meta charset="utf-8"><title>EKZ Settings</title>';
print '<style>body{font-family:system-ui,Arial,sans-serif;max-width:780px;margin:1.2rem auto}';
print 'fieldset{margin-bottom:1rem}label{display:block;margin:.4rem 0}';
print 'input[type=text],input[type=password]{width:100%;max-width:780px}';
print 'button{padding:.4rem .9rem}.actions{margin-top:1rem}</style></head><body>';
print '<h2>EKZ Settings</h2>';
print $msg if $msg;

print '<form method="post">';

print '<fieldset><legend>EKZ / OIDC</legend>';
print '<label>Auth server base<br><input name="auth_server_base" type="text" size="60" value="' . $cfg->{auth_server_base} . '"></label>';
print '<label>Realm<br><input name="realm" type="text" value="' . $cfg->{realm} . '"></label>';
print '<label>Client ID<br><input name="client_id" type="text" value="' . $cfg->{client_id} . '"></label>';
print '<label>Client secret<br><input type="password" name="client_secret" placeholder="(enter to update)"></label>';
print '<label>Redirect URI<br><input name="redirect_uri" type="text" size="80" value="' . $cfg->{redirect_uri} . '"></label>';
print '<label>API base<br><input name="api_base" type="text" size="60" value="' . $cfg->{api_base} . '"></label>';
print '<label>EMS instance ID<br><input name="ems_instance_id" type="text" value="' . $cfg->{ems_instance_id} . '"></label>';
print '<label>Scope<br><input name="scope" type="text" value="' . $cfg->{scope} . '"> <small>Use <code>openid offline_access</code> if allowed.</small></label>';
print '<label>Response mode<br><input name="response_mode" type="text" value="' . $cfg->{response_mode} . '"></label>';
print '<label>Timezone<br><input name="timezone" type="text" value="' . $cfg->{timezone} . '"></label>';
print '</fieldset>';

print '<fieldset><legend>MQTT</legend>';
my $mqtt_checked = $cfg->{mqtt_enabled} ? ' checked' : '';
print '<label><input type="checkbox" name="mqtt_enabled"' . $mqtt_checked . '> Enable MQTT</label>';
print '<label>Summary topic<br><input name="mqtt_topic_summary" type="text" size="50" value="' . $cfg->{mqtt_topic_summary} . '"></label>';
print '<label>Fallback tariff name<br><input name="fallback_tariff_name" type="text" value="' . $cfg->{fallback_tariff_name} . '"></label>';
print '</fieldset>';

print '<fieldset><legend>Advanced</legend>';
print '<label>Token store path (optional)<br><input name="token_store_path" type="text" size="80" value="' . $cfg->{token_store_path} . '"><br>';
print '<small>Example: <code>/opt/loxberry/data/ekz/tokens.json</code></small></label>';
print '</fieldset>';

print '<p class="actions"><button type="submit">Save</button> ';
print '<a href="' . $LBPURL . '/index.html">Back</a></p>';

print '</form></body></html>';
