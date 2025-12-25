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

# Safe escaped base for href attributes
my $SAFE_BASEURL = CGI::escapeHTML($BASEURL);

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

# Prepare escaped template variables and helpers to avoid @{[ ... ]} interpolation in heredoc
my %esc;
for my $k (qw/
  auth_server_base realm client_id redirect_uri api_base ems_instance_id
  scope response_mode timezone
  mqtt_host mqtt_port mqtt_username
  mqtt_topic_raw mqtt_topic_summary
  mqtt_topic_intervals mqtt_topic_hourly
  fallback_tariff_name token_store_path retries
/) {
  $esc{$k} = h($cfg->{$k}//'');
}
my $checked_mqtt_enabled   = $cfg->{mqtt_enabled} ? 'checked' : '';
my $checked_publish_relative = $cfg->{publish_relative_hourly} ? 'checked' : '';

# Select helpers for fetch schedule
my $fs = $cfg->{fetch_schedule} // '';
my $sel_schedule_1  = ($fs eq '1')  ? 'selected' : '';
my $sel_schedule_2  = ($fs eq '2')  ? 'selected' : '';
my $sel_schedule_12 = ($fs eq '12') ? 'selected' : '';
my $sel_schedule_24 = ($fs eq '24') ? 'selected' : '';

# Handle POST to update config JSON (write back to $LBPDATADIR/ekz_config.json)
my $msg = '';
if ($q->request_method eq 'POST') {
  my @fields = qw/
    auth_server_base realm client_id redirect_uri api_base ems_instance_id
    scope response_mode timezone
    mqtt_host mqtt_port mqtt_username
    mqtt_topic_raw mqtt_topic_summary
    mqtt_topic_intervals mqtt_topic_hourly
    fallback_tariff_name token_store_path fetch_schedule retries
  /;

  # Update regular fields
  for my $f (@fields) {
    my $v = $q->param($f);
    $cfg->{$f} = defined $v ? $v : $cfg->{$f};
  }

  # Boolean checkboxes
  $cfg->{mqtt_enabled} = $q->param('mqtt_enabled') ? JSON::PP::true : JSON::PP::false;
  $cfg->{publish_relative_hourly} = $q->param('publish_relative_hourly') ? JSON::PP::true : JSON::PP::false;

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

    # Update cron wrappers. Pass the whole cfg hashref so update_cron_schedule can manage compute wrapper too.
    my $ok = update_cron_schedule($cfg);
    $msg = $ok ? "<div class='alert alert-ok'>Settings saved. Cron schedule updated.</div>"
               : "<div class='alert alert-warn'>Settings saved but cron update failed. Check permissions.</div>";
  } else {
    $msg = "<div class='alert alert-err'>Cannot write " . h($cfgfile) . ": " . h($!) . "</div>";
  }

  # Refresh escaped values and helpers after save so the form shows new values
  for my $k (keys %esc) { $esc{$k} = h($cfg->{$k}//''); }
  $checked_mqtt_enabled = $cfg->{mqtt_enabled} ? 'checked' : '';
  $checked_publish_relative = $cfg->{publish_relative_hourly} ? 'checked' : '';
  $fs = $cfg->{fetch_schedule} // '';
  $sel_schedule_1  = ($fs eq '1')  ? 'selected' : '';
  $sel_schedule_2  = ($fs eq '2')  ? 'selected' : '';
  $sel_schedule_12 = ($fs eq '12') ? 'selected' : '';
  $sel_schedule_24 = ($fs eq '24') ? 'selected' : '';
}

# Render page using shared styles (same look as index.cgi)
print <<"HTML_HEAD";
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>EKZ Settings</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="preload" as="image" href="$ICON_BASE/banner.jpg">
  <link rel="stylesheet" href="$BASEURL/style.css">
  <link rel="stylesheet" href="$ASSET_BASE/styles.css?v=20251220">
</head>
<body id="ekz-plugin" class="plugincontent">
  <div class="app-header">
    <div class="banner">
      <div class="title">EKZ Tariffs – Plugin Settings</div>
    </div>
  </div>

  <div class="nav-actions">
    <a class="btn btn-primary" href="start.cgi"><span class="emoji">🔐</span> Sign in (OIDC)</a>
    <a class="btn btn-green"   href="run_rolling_fetch.cgi"><span class="emoji">⚡</span> Fetch now</a>
    <a class="btn btn-slate"   href="health.cgi"><span class="emoji">🩺</span> Health</a>
    <a class="btn btn-primary" href="index.cgi"><span class="emoji">🏠</span> Home</a>
    <a class="btn btn-primary" href="settings.cgi"><span class="emoji">⚙️</span> Settings</a>
  </div>

  <div class="container">
    <div class="card">
      $msg
      <form method="post" autocomplete="off" novalidate>

        <fieldset>
          <legend>EKZ / OIDC</legend>
          <label>Auth server base</label>
          <input name="auth_server_base" type="text" size="60" value="$esc{auth_server_base}">
          <label>Realm</label>
          <input name="realm" type="text" value="$esc{realm}">
          <label>Client ID</label>
          <input name="client_id" type="text" value="$esc{client_id}">
          <label>Client secret <span class="small">(enter to update)</span></label>
          <input type="password" name="client_secret" placeholder="••••••••">
          <label>Redirect URI</label>
          <input name="redirect_uri" type="text" size="80" value="$esc{redirect_uri}">
          <div class="hint small">Example: https://your.host/admin/plugins/ekz_plugin/callback.cgi</div>
          <label>API base</label>
          <input name="api_base" type="text" size="60" value="$esc{api_base}">
          <label>EMS instance ID</label>
          <input name="ems_instance_id" type="text" value="$esc{ems_instance_id}">
          <label>Scope</label>
          <input name="scope" type="text" value="$esc{scope}">
          <label>Response mode</label>
          <input name="response_mode" type="text" value="$esc{response_mode}">
          <label>Timezone</label>
          <input name="timezone" type="text" value="$esc{timezone}">
        </fieldset>

        <fieldset>
          <legend>MQTT</legend>
          <label><input type="checkbox" name="mqtt_enabled" $checked_mqtt_enabled> Enable MQTT</label>
          <label>Broker host</label>
          <input name="mqtt_host" type="text" value="$esc{mqtt_host}">
          <label>Broker port</label>
          <input name="mqtt_port" type="text" value="$esc{mqtt_port}">
          <label>Username (optional)</label>
          <input name="mqtt_username" type="text" value="$esc{mqtt_username}">
          <label>Password (optional) <span class="small">(enter to update)</span></label>
          <input type="password" name="mqtt_password" placeholder="••••••••">
          <div class="hr"></div>
          <label>Raw topic (legacy)</label>
          <input name="mqtt_topic_raw" type="text" size="50" value="$esc{mqtt_topic_raw}">
          <label>Summary topic (legacy)</label>
          <input name="mqtt_topic_summary" type="text" size="50" value="$esc{mqtt_topic_summary}">
          <div class="hr"></div>
          <label>Intervals topic (computed 15‑min values)</label>
          <input name="mqtt_topic_intervals" type="text" size="50" value="$esc{mqtt_topic_intervals}">
          <label>Hourly averages topic (computed hourly means)</label>
          <input name="mqtt_topic_hourly" type="text" size="50" value="$esc{mqtt_topic_hourly}">
          <label>Fallback tariff name</label>
          <input name="fallback_tariff_name" type="text" value="$esc{fallback_tariff_name}">
        </fieldset>

        <fieldset>
          <legend>Scheduling</legend>
          <label>Fetch frequency</label>
          <select name="fetch_schedule">
            <option value="1"  $sel_schedule_1>1x per day (at 18:00)</option>
            <option value="2"  $sel_schedule_2>2x per day (at 06:00 and 18:00)</option>
            <option value="12" $sel_schedule_12>12x per day (every 2 hours, even hours)</option>
            <option value="24" $sel_schedule_24>24x per day (every hour)</option>
          </select>
          <div class="hint">After each fetch, computed costs are published to MQTT (intervals + hourly).</div>

          <div style="margin-top:.5rem;">
            <label><input type="checkbox" name="publish_relative_hourly" $checked_publish_relative> Publish relative 24h view hourly (compute_costs executed hourly)</label>
            <div class="hint small">When enabled, a cron.hourly wrapper will be created to run compute_costs.cgi every hour and republish the relative +0..+23 topic.</div>
          </div>
        </fieldset>

        <fieldset>
          <legend>Advanced</legend>
          <label>Token store path (optional)</label>
          <input name="token_store_path" type="text" size="80" value="$esc{token_store_path}">
          <div class="hint small">Example: /opt/loxberry/data/plugins/ekz_plugin/tokens.json</div>
          <label>Retries (HTTP/API)</label>
          <input name="retries" type="text" value="$esc{retries}">
        </fieldset>

        <div class="hr"></div>
        <div class="actions">
          <button type="submit">Save</button>
          <a href="index.cgi"><button class="btn-secondary" type="button">Back</button></a>
        </div>
      </form>
    </div>
  </div>
</body>
</html>
HTML_HEAD

# ----------------------------
# Cron updater (updated to accept either $schedule or $cfg hashref and manage compute wrapper)
# ----------------------------
sub update_cron_schedule {
  my ($cfg_or_schedule) = @_;

  # Determine $schedule and whether relative hourly publish is enabled
  my $schedule;
  my $publish_relative = 0;
  if (ref($cfg_or_schedule) eq 'HASH') {
    $schedule = $cfg_or_schedule->{fetch_schedule} // '';
    $publish_relative = $cfg_or_schedule->{publish_relative_hourly} ? 1 : 0;
  } else {
    $schedule = $cfg_or_schedule // '';
    $publish_relative = 0;
  }

  my $cron_file;
  my $cron_content;

  # Run CGI directly with Perl to avoid any web auth/session
  my $run_cmd     = "/usr/bin/env perl \"$lbphtmlauthdir/run_rolling_fetch.cgi\" >/dev/null 2>&1";
  my $compute_cmd = "/usr/bin/env perl \"$lbphtmlauthdir/compute_costs.cgi\" >/dev/null 2>&1";

  # Paths for wrappers
  my $fetch_wrapper_name   = $lbpplugindir;
  my $compute_wrapper_name = "${lbpplugindir}-compute";

  # Decide fetch wrapper content based on schedule
  if ($schedule && $schedule eq '1') {
    # Once per day at 18:00
    $cron_file = "$lbhomedir/system/cron/cron.hourly/$fetch_wrapper_name";
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
    $cron_file = "$lbhomedir/system/cron/cron.hourly/$fetch_wrapper_name";
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
    $cron_file = "$lbhomedir/system/cron/cron.hourly/$fetch_wrapper_name";
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
    $cron_file = "$lbhomedir/system/cron/cron.hourly/$fetch_wrapper_name";
    $cron_content = <<"BASH";
#!/bin/bash
$run_cmd
BASH
  }
  else {
    # Disabled/invalid schedule: remove fetch wrapper and compute wrapper
    my @cron_dirs = ("$lbhomedir/system/cron/cron.01min",
                     "$lbhomedir/system/cron/cron.03min",
                     "$lbhomedir/system/cron/cron.05min",
                     "$lbhomedir/system/cron/cron.10min",
                     "$lbhomedir/system/cron/cron.15min",
                     "$lbhomedir/system/cron/cron.30min",
                     "$lbhomedir/system/cron/cron.hourly",
                     "$lbhomedir/system/cron/cron.daily");
    foreach my $dir (@cron_dirs) {
      unlink "$dir/$fetch_wrapper_name"   if -e "$dir/$fetch_wrapper_name";
      unlink "$dir/$compute_wrapper_name" if -e "$dir/$compute_wrapper_name";
    }
    return 0;
  }

  # Write or update fetch wrapper
  eval {
    open my $fh, '>', $cron_file or die "Cannot write $cron_file: $!";
    print $fh $cron_content;
    close $fh;
    chmod 0755, $cron_file;
    1;
  } or do {
    return 0;
  };

  # Manage compute wrapper (create under cron.hourly if publish_relative is enabled,
  # otherwise remove compute wrappers from all cron dirs)
  my $compute_wrapper_path = "$lbhomedir/system/cron/cron.hourly/$compute_wrapper_name";
  if ($publish_relative) {
    my $compute_content = <<"BASH";
#!/bin/bash
# Wrapper to run compute_costs.cgi hourly to republish relative +0..+23 topic
$compute_cmd
BASH
    eval {
      open my $cfh, '>', $compute_wrapper_path or die "Cannot write $compute_wrapper_path: $!";
      print $cfh $compute_content;
      close $cfh;
      chmod 0755, $compute_wrapper_path;
      1;
    } or do {
      # Unable to write compute wrapper; log and continue (we already created fetch wrapper)
      return 0;
    };
  } else {
    # Remove compute wrapper from all known cron dirs
    my @cron_dirs_all = ("$lbhomedir/system/cron/cron.01min",
                         "$lbhomedir/system/cron/cron.03min",
                         "$lbhomedir/system/cron/cron.05min",
                         "$lbhomedir/system/cron/cron.10min",
                         "$lbhomedir/system/cron/cron.15min",
                         "$lbhomedir/system/cron/cron.30min",
                         "$lbhomedir/system/cron/cron.hourly",
                         "$lbhomedir/system/cron/cron.daily");
    foreach my $dir (@cron_dirs_all) {
      unlink "$dir/$compute_wrapper_name" if -e "$dir/$compute_wrapper_name";
    }
  }

  # Clean up old fetch wrapper locations (keep only the new one)
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
    unlink "$dir/$fetch_wrapper_name" if -e "$dir/$fetch_wrapper_name";
  }

  return 1;
}
