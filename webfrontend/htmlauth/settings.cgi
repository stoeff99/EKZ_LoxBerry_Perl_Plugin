#!/usr/bin/perl
use strict;
use warnings;

use LoxBerry::System;
use CGI;
use JSON::PP;
use File::Spec;
use File::Path qw(make_path);
use FindBin;

# SDK globals
our ($lbpurl, $lbpdatadir, $lbptemplatedir, $lbhomedir, $lbpplugindir, $lbphtmlauthdir);

# HTML escape
sub h { return '' unless defined $_[0]; return CGI::escapeHTML($_[0]); }

# Base URLs and assets
my $BASEURL    = $lbpurl || do { (my $p = $ENV{SCRIPT_NAME}//'') =~ s{/[^/]+$}{}r || '.' };
my $ASSET_BASE = "$BASEURL/assets";
my $ICON_BASE  = "$BASEURL/Icons";

# Load shared helpers from common.pl (provides load_cfg())
require "$FindBin::Bin/common.pl";

my $q = CGI->new;
print $q->header('text/html; charset=utf-8');

# Ensure plugin data dir exists
my $LBPDATADIR = $lbpdatadir;
eval { make_path($LBPDATADIR) unless -d $LBPDATADIR; 1 } or do {
  print "<p class='alert alert-err'>Failed to create data dir " . h($LBPDATADIR) . ": " . h($@) . "</p>";
  exit;
};

# Common runtime config JSON path (shared across the plugin)
my $cfgfile = File::Spec->catfile($LBPDATADIR, 'ekz_config.json');

# Load configuration (runtime JSON if present, otherwise shipped default via common.pl)
my $cfg = eval { load_cfg() };
if ($@ || !defined $cfg || ref($cfg) ne 'HASH') {
  $cfg = {};
}

# Handle POST to update config JSON (write back to $LBPDATADIR/ekz_config.json)
my $msg = '';
if ($q->request_method eq 'POST') {
  my @fields = qw/
    auth_server_base realm client_id redirect_uri api_base ems_instance_id
    scope response_mode timezone mqtt_topic_raw mqtt_topic_summary
    mqtt_host mqtt_port mqtt_username
    fallback_tariff_name token_store_path fetch_schedule retries
  /;

  # Update regular fields
  for my $f (@fields) {
    my $v = $q->param($f);
    $cfg->{$f} = defined $v ? $v : $cfg->{$f};
  }

  # Boolean checkbox
  $cfg->{mqtt_enabled} = $q->param('mqtt_enabled') ? JSON::PP::true : JSON::PP::false;

  # Sensitive fields: only update if non-empty
  if (defined $q->param('client_secret')) {
    my $newsec = $q->param('client_secret');
    $cfg->{client_secret} = $newsec if defined $newsec && $newsec ne '';
  }
  if (defined $q->param('mqtt_password')) {
    my $newpw = $q->param('mqtt_password');
    $cfg->{mqtt_password} = $newpw if defined $newpw && $newpw ne '';
  }

  # Write updated JSON
  if (open my $fh, '>', $cfgfile) {
    print $fh encode_json($cfg);
    close $fh;
    chmod 0640, $cfgfile;

    my $ok = update_cron_schedule($cfg->{fetch_schedule});
    $msg = $ok ? "<div class='alert alert-ok'>Settings saved. Cron schedule updated.</div>"
               : "<div class='alert alert-warn'>Settings saved but cron update failed. Check permissions.</div>";
  } else {
    $msg = "<div class='alert alert-err'>Cannot write " . h($cfgfile) . ": " . h($!) . "</div>";
  }
}

# Render page using shared styles (same look as index.cgi)
print <<"HTML";
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>EKZ Settings</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="preload" as="image" href="$ICON_BASE/banner.jpg">
  <link rel="stylesheet" href="$BASEURL/style.css">
  <link rel="stylesheet" href="$ASSET_BASE/styles.css?v=20251217">
</head>
<body id="ekz-plugin" class="plugincontent">
  <div class="app-header">
    <div class="banner">
      <div class="title">EKZ Tariffs – Plugin Settings</div>
    </div>
  </div>

  <div class="nav-actions">
    <a class="btn btn-primary" href="@{[ h($BASEURL) ]}/start.cgi"><span class="emoji">🔐</span> Sign in (OIDC)</a>
    <a class="btn btn-green"   href="@{[ h($BASEURL) ]}/run_rolling_fetch.cgi"><span class="emoji">⚡</span> Fetch now (rolling 24h)</a>
    <a class="btn btn-orange"  href="@{[ h($BASEURL) ]}/health.cgi"><span class="emoji">🩺</span> Health</a>
    <a class="btn btn-slate"   href="@{[ h($BASEURL) ]}/settings.cgi"><span class="emoji">⚙️</span> Settings</a>
  </div>

  <div class="container">
    <div class="card">
      $msg
      <form method="post" autocomplete="off" novalidate>

        <fieldset>
          <legend>EKZ / OIDC</legend>
          <label>Auth server base</label>
          <input name="auth_server_base" type="text" size="60" value="@{[ h($cfg->{auth_server_base}//'') ]}">
          <label>Realm</label>
          <input name="realm" type="text" value="@{[ h($cfg->{realm}//'') ]}">
          <label>Client ID</label>
          <input name="client_id" type="text" value="@{[ h($cfg->{client_id}//'') ]}">
          <label>Client secret <span class="small">(enter to update)</span></label>
          <input type="password" name="client_secret" placeholder="••••••••">
          <label>Redirect URI</label>
          <input name="redirect_uri" type="text" size="80" value="@{[ h($cfg->{redirect_uri}//'') ]}">
          <div class="hint small">Example: https://your.host/admin/plugins/ekz_plugin/callback.cgi</div>
          <label>API base</label>
          <input name="api_base" type="text" size="60" value="@{[ h($cfg->{api_base}//'') ]}">
          <label>EMS instance ID</label>
          <input name="ems_instance_id" type="text" value="@{[ h($cfg->{ems_instance_id}//'') ]}">
          <label>Scope</label>
          <input name="scope" type="text" value="@{[ h($cfg->{scope}//'') ]}">
          <label>Response mode</label>
          <input name="response_mode" type="text" value="@{[ h($cfg->{response_mode}//'') ]}">
          <label>Timezone</label>
          <input name="timezone" type="text" value="@{[ h($cfg->{timezone}//'') ]}">
        </fieldset>

        <fieldset>
          <legend>MQTT</legend>
          <label><input type="checkbox" name="mqtt_enabled" @{[ $cfg->{mqtt_enabled} ? 'checked' : '' ]}> Enable MQTT</label>
          <label>Broker host</label>
          <input name="mqtt_host" type="text" value="@{[ h($cfg->{mqtt_host}//'') ]}">
          <label>Broker port</label>
          <input name="mqtt_port" type="text" value="@{[ h($cfg->{mqtt_port}//'') ]}">
          <label>Username (optional)</label>
          <input name="mqtt_username" type="text" value="@{[ h($cfg->{mqtt_username}//'') ]}">
          <label>Password (optional) <span class="small">(enter to update)</span></label>
          <input type="password" name="mqtt_password" placeholder="••••••••">
          <label>Raw topic</label>
          <input name="mqtt_topic_raw" type="text" size="50" value="@{[ h($cfg->{mqtt_topic_raw}//'') ]}">
          <label>Summary topic</label>
          <input name="mqtt_topic_summary" type="text" size="50" value="@{[ h($cfg->{mqtt_topic_summary}//'') ]}">
          <label>Fallback tariff name</label>
          <input name="fallback_tariff_name" type="text" value="@{[ h($cfg->{fallback_tariff_name}//'') ]}">
        </fieldset>

        <fieldset>
          <legend>Scheduling</legend>
          <label>Fetch frequency</label>
          <select name="fetch_schedule">
            <option value="1"  @{[ ($cfg->{fetch_schedule}//'') eq '1'  ? 'selected' : '' ]}>1x per day (at 18:00)</option>
            <option value="2"  @{[ ($cfg->{fetch_schedule}//'') eq '2'  ? 'selected' : '' ]}>2x per day (at 06:00 and 18:00)</option>
            <option value="12" @{[ ($cfg->{fetch_schedule}//'') eq '12' ? 'selected' : '' ]}>12x per day (every 2 hours, even hours)</option>
            <option value="24" @{[ ($cfg->{fetch_schedule}//'') eq '24' ? 'selected' : '' ]}>24x per day (every hour)</option>
          </select>
          <div class="hint">The plugin fetches a rolling 24h window to include current + next day's tariffs.</div>
        </fieldset>

        <fieldset>
          <legend>Advanced</legend>
          <label>Token store path (optional)</label>
          <input name="token_store_path" type="text" size="80" value="@{[ h($cfg->{token_store_path}//'') ]}">
          <div class="hint small">Example: /opt/loxberry/data/plugins/ekz_plugin/tokens.json</div>
          <label>Retries (HTTP/API)</label>
          <input name="retries" type="text" value="@{[ h($cfg->{retries}//'3') ]}">
        </fieldset>

        <div class="hr"></div>
        <div class="actions">
          <button type="submit">Save</button>
          <a href="@{[ h($BASEURL) ]}/index.cgi"><button class="btn-secondary" type="button">Back</button></a>
        </div>
      </form>
    </div>
  </div>
</body>
</html>
HTML

# ----------------------------
# Cron updater (hour-based schedules for reliability)
# ----------------------------
sub update_cron_schedule {
  my ($schedule) = @_;

  my $cron_file;
  my $cron_content;

  # Run CGI directly with Perl to avoid any web auth/session
  my $run_cmd = "/usr/bin/env perl \"$lbphtmlauthdir/run_rolling_fetch.cgi\" >/dev/null 2>&1";

  if ($schedule && $schedule eq '1') {
    # Once per day at 18:00
    $cron_file = "$lbhomedir/system/cron/cron.hourly/$lbpplugindir";
    $cron_content = <<"BASH";
#!/bin/bash
HOUR=\$(date +\\%H)
if [[ "\$HOUR" == "18" ]]; then
  $run_cmd
fi
BASH
  }
  elsif ($schedule && $schedule eq '2') {
    # Twice per day at 06:00 and 18:00
    $cron_file = "$lbhomedir/system/cron/cron.hourly/$lbpplugindir";
    $cron_content = <<"BASH";
#!/bin/bash
HOUR=\$(date +\\%H)
if [[ "\$HOUR" == "06" || "\$HOUR" == "18" ]]; then
  $run_cmd
fi
BASH
  }
  elsif ($schedule && $schedule eq '12') {
    # Every 2 hours on even hours: 00, 02, 04, ...
    $cron_file = "$lbhomedir/system/cron/cron.hourly/$lbpplugindir";
    $cron_content = <<"BASH";
#!/bin/bash
HOUR=\$(date +\\%H)
if (( \$HOUR % 2 == 0 )); then
  $run_cmd
fi
BASH
  }
  elsif ($schedule && $schedule eq '24') {
    # Every hour
    $cron_file = "$lbhomedir/system/cron/cron.hourly/$lbpplugindir";
    $cron_content = <<"BASH";
#!/bin/bash
$run_cmd
BASH
  }
  else {
    # Disabled/invalid: remove existing wrappers
    my @cron_dirs = ("$lbhomedir/system/cron/cron.01min",
                     "$lbhomedir/system/cron/cron.03min",
                     "$lbhomedir/system/cron/cron.05min",
                     "$lbhomedir/system/cron/cron.10min",
                     "$lbhomedir/system/cron/cron.15min",
                     "$lbhomedir/system/cron/cron.30min",
                     "$lbhomedir/system/cron/cron.hourly",
                     "$lbhomedir/system/cron/cron.daily");
    foreach my $dir (@cron_dirs) { unlink "$dir/$lbpplugindir" if -e "$dir/$lbpplugindir" }
    return 0;
  }

  # Clean up old locations (keep only the new one)
  my @all_dirs = ("$lbhomedir/system/cron/cron.01min",
                  "$lbhomedir/system/cron/cron.03min",
                  "$lbhomedir/system/cron/cron.05min",
                  "$lbhomedir/system/cron/cron.10min",
                  "$lbhomedir/system/cron/cron.15min",
                  "$lbhomedir/system/cron/cron.30min",
                  "$lbhomedir/system/cron/cron.hourly",
                  "$lbhomedir/system/cron/cron.daily");
  foreach my $dir (@all_dirs) {
    next if $cron_file =~ /\Q$dir\E/;
    unlink "$dir/$lbpplugindir" if -e "$dir/$lbpplugindir";
  }

  # Write wrapper
  eval {
    open my $fh, '>', $cron_file or die "Cannot write $cron_file: $!";
    print $fh $cron_content;
    close $fh;
    chmod 0755, $cron_file;
    1;
  } or do {
    return 0;
  };

  return 1;
}
