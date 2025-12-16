#!/usr/bin/perl
use strict;
use warnings;

use CGI;
use JSON::PP;
use File::Spec;
use File::Path qw(make_path);
use FindBin;
use LoxBerry::System;

our ($lbpdatadir, $lbpurl, $lbptemplatedir);

# Base URL
my $BASEURL = $lbpurl;
if (!$BASEURL) {
    my $path = $ENV{SCRIPT_NAME} // '';
    $path =~ s{/[^/]+$}{};
    $BASEURL = $path || '.';
}

# Load shared config loader
require File::Spec->catfile($FindBin::Bin, 'common.pl');

my $q = CGI->new;
print $q->header('text/html; charset=utf-8');

my $LBPDATADIR = $lbpdatadir;
my $LBPURL     = $lbpurl;

# Ensure data dir exists
eval { make_path($LBPDATADIR) unless -d $LBPDATADIR; 1 } or do {
    print "<p style='color:#b00'>Failed to create data dir $LBPDATADIR: $@</p>";
    exit;
};

# Paths
my $cfgfile = File::Spec->catfile($LBPDATADIR, 'ekz_config.json');

# Load current cfg (runtime or shipped default via common.pl::load_cfg)
my $cfg = eval { load_cfg() };
if ($@) {
  print "<p style='color:#b00'>Cannot load configuration: $@</p>";
  $cfg = {};
}

# Handle POST
my $msg = '';
if ($q->request_method eq 'POST') {
    # Define which fields are editable via this form
    my @fields = qw/
      auth_server_base realm client_id redirect_uri api_base ems_instance_id
      scope response_mode timezone
      mqtt_enabled mqtt_host mqtt_port mqtt_username mqtt_topic_raw mqtt_topic_summary
      fallback_tariff_name retries token_store_path
    /;

    # Update booleans and strings
    for my $f (@fields) {
        my $v = $q->param($f);
        if ($f eq 'mqtt_enabled') {
            $cfg->{$f} = $q->param('mqtt_enabled') ? JSON::PP::true : JSON::PP::false;
        } else {
            $cfg->{$f} = defined $v ? $v : $cfg->{$f};
        }
    }

    # Sensitive fields: only update if non-empty
    if (defined $q->param('client_secret')) {
      my $newsec = $q->param('client_secret');
      if (defined $newsec && $newsec ne '') {
        $cfg->{client_secret} = $newsec;
      }
    }
    if (defined $q->param('mqtt_password')) {
      my $newpass = $q->param('mqtt_password');
      if (defined $newpass && $newpass ne '') {
        $cfg->{mqtt_password} = $newpass;
      }
    }

    # Write JSON
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
print '<label>Broker host<br><input name="mqtt_host" type="text" value="' . $cfg->{mqtt_host} . '"></label>';
print '<label>Broker port<br><input name="mqtt_port" type="text" value="' . $cfg->{mqtt_port} . '"></label>';
print '<label>Username (optional)<br><input name="mqtt_username" type="text" value="' . $cfg->{mqtt_username} . '"></label>';
print '<label>Password (optional)<br><input type="password" name="mqtt_password" placeholder="(enter to update)"></label>';
print '<label>Raw topic<br><input name="mqtt_topic_raw" type="text" size="50" value="' . $cfg->{mqtt_topic_raw} . '"></label>';
print '<label>Summary topic<br><input name="mqtt_topic_summary" type="text" size="50" value="' . $cfg->{mqtt_topic_summary} . '"></label>';
print '<label>Fallback tariff name<br><input name="fallback_tariff_name" type="text" value="' . $cfg->{fallback_tariff_name} . '"></label>';
print '</fieldset>';

print '<fieldset><legend>Advanced</legend>';
print '<label>Token store path (optional)<br><input name="token_store_path" type="text" size="80" value="' . $cfg->{token_store_path} . '"><br>';
print '<small>Example: <code>/opt/loxberry/data/ekz/tokens.json</code></small></label>';
print '</fieldset>';

print '<p class="actions"><button type="submit">Save</button> ';
print '<a href="' . $BASEURL . '/index.cgi">Back</a></p>';

print '</form></body></html>';


# Add into webfrontend/htmlauth/settings.cgi (paste below other helper subs)
use File::Basename;

sub update_cron_schedule {
    my ($schedule, $lbpurl) = @_;
    # LoxBerry installation base (adjust only if your LB is installed elsewhere)
    my $LBHOME = '/opt/loxberry';

    # Determine plugin id from $lbpurl if possible: "/admin/.../plugins/<plugindir>/..."
    my $plugindir = 'ekz_plugin'; # fallback
    if ($lbpurl && $lbpurl =~ m{/admin/[^/]+/plugins/([^/]+)}) {
        $plugindir = $1;
    }

    # Host-local fetch script path used by the cron script
    my $fetch_script = "/admin/plugins/$plugindir/run_rolling_fetch.cgi";

    my $cron_file;
    my $cron_content;

    if ($schedule eq '1') {
        # Once per day at 18:05
        $cron_file = "$LBHOME/system/cron/cron.daily/$plugindir";
        $cron_content = "#!/bin/bash\n# Run at 18:05 daily\n";
        $cron_content .= "if [ \$(date +\\%H:\\%M) = \"18:05\" ]; then\n";
        $cron_content .= "  curl -s http://localhost$fetch_script >/dev/null 2>&1\n";
        $cron_content .= "fi\n";
    }
    elsif ($schedule eq '2') {
        # Twice per day at 18:05 and 06:05
        $cron_file = "$LBHOME/system/cron/cron.hourly/$plugindir";
        $cron_content = "#!/bin/bash\n# Run at 18:05 and 06:05\n";
        $cron_content .= "HOUR=\$(date +\\%H)\nMINUTE=\$(date +\\%M)\n";
        $cron_content .= "if [[ \$MINUTE == \"05\" && (\$HOUR == \"18\" || \$HOUR == \"06\") ]]; then\n";
        $cron_content .= "  curl -s http://localhost$fetch_script >/dev/null 2>&1\n";
        $cron_content .= "fi\n";
    }
    elsif ($schedule eq '12') {
        # Every 2 hours (12x per day)
        $cron_file = "$LBHOME/system/cron/cron.hourly/$plugindir";
        $cron_content = "#!/bin/bash\n# Run every 2 hours\n";
        $cron_content .= "HOUR=\$(date +\\%H)\n";
        $cron_content .= "if (( \$HOUR % 2 == 0 )); then\n";
        $cron_content .= "  curl -s http://localhost$fetch_script >/dev/null 2>&1\n";
        $cron_content .= "fi\n";
    }
    elsif ($schedule eq '24') {
        # Every hour (24x per day)
        $cron_file = "$LBHOME/system/cron/cron.hourly/$plugindir";
        $cron_content = "#!/bin/bash\n# Run every hour\n";
        $cron_content .= "curl -s http://localhost$fetch_script >/dev/null 2>&1\n";
    }
    else {
        # Remove any existing cron file and return success (treat unknown/0 as disabled)
        foreach my $dir ("$LBHOME/system/cron/cron.01min",
                         "$LBHOME/system/cron/cron.03min",
                         "$LBHOME/system/cron/cron.05min",
                         "$LBHOME/system/cron/cron.10min",
                         "$LBHOME/system/cron/cron.15min",
                         "$LBHOME/system/cron/cron.30min",
                         "$LBHOME/system/cron/cron.hourly",
                         "$LBHOME/system/cron/cron.daily") {
            my $old = "$dir/$plugindir";
            unlink $old if -e $old;
        }
        return 1;
    }

    # Remove old cron files from other locations
    foreach my $dir ("$LBHOME/system/cron/cron.01min",
                     "$LBHOME/system/cron/cron.03min",
                     "$LBHOME/system/cron/cron.05min",
                     "$LBHOME/system/cron/cron.10min",
                     "$LBHOME/system/cron/cron.15min",
                     "$LBHOME/system/cron/cron.30min",
                     "$LBHOME/system/cron/cron.hourly",
                     "$LBHOME/system/cron/cron.daily") {
        my $old_file = "$dir/$plugindir";
        unlink $old_file if -e $old_file;
    }

    # Write new cron file
    eval {
        # Ensure directory exists
        my ($dir) = $cron_file =~ m{^(.+)/[^/]+$};
        if ($dir && !-d $dir) {
            mkdir $dir or die "Cannot create cron dir $dir: $!";
        }
        open my $fh, '>', $cron_file or die "Cannot write $cron_file: $!";
        print $fh $cron_content;
        close $fh;
        chmod 0755, $cron_file;
        1;
    } or do {
        my $err = $@ || 'unknown';
        # Log to plugin data dir if available
        eval {
          my $logfile = File::Spec->catfile($lbpdatadir || '/opt/loxberry/data', 'cron_update.log');
          if (open my $lf, '>>', $logfile) {
            print $lf scalar(localtime) . " - update_cron_schedule failed: $err\n";
            close $lf;
          }
          1;
        };
        return 0;
    };

    return 1;
}
}

