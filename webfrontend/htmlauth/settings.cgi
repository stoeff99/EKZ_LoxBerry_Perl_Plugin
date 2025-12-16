#!/usr/bin/perl
use strict;
use warnings;

use LoxBerry::System;            # import SDK globals (paths/urls)
use CGI;
use JSON::PP;
use File::Spec;
use File::Path qw(make_path);
use FindBin;
# Optional: show errors in browser while debugging
# use CGI::Carp qw(fatalsToBrowser warningsToBrowser);

# Load common.pl for shared config loading logic
require File::Spec->catfile($FindBin::Bin, 'common.pl');

# Declare SDK globals so 'strict' allows them
our ($lbpurl, $lbpdatadir, $lbptemplatedir);

# Use SDK base URL if present; otherwise derive from current script path
my $BASEURL = $lbpurl;
if (!$BASEURL) {
    my $path = $ENV{SCRIPT_NAME} // '';
    $path =~ s{/[^/]+$}{};
    $BASEURL = $path || '.';
}

my $q = CGI->new;
print $q->header('text/html; charset=utf-8');

# Use the SDK globals
my $LBPDATADIR = $lbpdatadir;    # e.g. /opt/loxberry/data/plugins/<folder>
my $LBPURL     = $lbpurl;        # e.g. /admin/loxberry/webfrontend/htmlauth/plugins/<folder>


# --- Ensure data dir exists ---
eval { make_path($LBPDATADIR) unless -d $LBPDATADIR; 1 } or do {
    print "<p style='color:#b00'>Failed to create data dir $LBPDATADIR: $@</p>";
    exit;
};

# --- Config file path ---
my $cfgfile = File::Spec->catfile($LBPDATADIR, 'ekz_config.json');

# --- Load config from JSON only (no hardcoded defaults) ---
my $cfg = load_cfg_for_ui();

# Normalize any old .pl redirect to .cgi
if ($cfg->{redirect_uri}) {
    $cfg->{redirect_uri} =~ s/callback\.pl/callback.cgi/;
}

# Display-only fallback for redirect_uri if empty (for UI rendering only)
my $display_redirect_uri = $cfg->{redirect_uri} || ($LBPURL ? "$LBPURL/callback.cgi" : '');

# Ensure mqtt_enabled is a proper boolean
$cfg->{mqtt_enabled} = JSON::PP::true unless exists $cfg->{mqtt_enabled};
$cfg->{mqtt_enabled} = $cfg->{mqtt_enabled} ? JSON::PP::true : JSON::PP::false;

# --- Handle POST ---
my $msg = '';
if ($q->request_method eq 'POST') {
        my @fields = qw/
            auth_server_base realm client_id redirect_uri api_base ems_instance_id
            scope response_mode timezone mqtt_topic_raw mqtt_topic_summary
            mqtt_host mqtt_port mqtt_username
            fallback_tariff_name token_store_path
        /;

    for my $f (@fields) {
        my $v = $q->param($f);
        $cfg->{$f} = defined $v ? $v : ($cfg->{$f} // '');
    }

    # mqtt_enabled checkbox
    $cfg->{mqtt_enabled} = $q->param('mqtt_enabled') ? JSON::PP::true : JSON::PP::false;
    
    # Ensure retries is numeric (default to 3 if missing)
    $cfg->{retries} = int($cfg->{retries} || 3);

        # client_secret: only update if non-empty provided
    if (defined $q->param('client_secret')) {
      my $newsec = $q->param('client_secret');
      if (defined $newsec && $newsec ne '') {
        $cfg->{client_secret} = $newsec;
      }
    }

        # mqtt_password: only update if non-empty provided
        if (defined $q->param('mqtt_password')) {
            my $newpw = $q->param('mqtt_password');
            if (defined $newpw && $newpw ne '') {
                $cfg->{mqtt_password} = $newpw;
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
print '<label>Auth server base<br><input name="auth_server_base" type="text" size="60" value="' . ($cfg->{auth_server_base} // '') . '"></label>';
print '<label>Realm<br><input name="realm" type="text" value="' . ($cfg->{realm} // '') . '"></label>';
print '<label>Client ID<br><input name="client_id" type="text" value="' . ($cfg->{client_id} // '') . '"></label>';
print '<label>Client secret<br><input type="password" name="client_secret" placeholder="(enter to update)"></label>';
print '<label>Redirect URI<br><input name="redirect_uri" type="text" size="80" value="' . $display_redirect_uri . '"></label>';
print '<label>API base<br><input name="api_base" type="text" size="60" value="' . ($cfg->{api_base} // '') . '"></label>';
print '<label>EMS instance ID<br><input name="ems_instance_id" type="text" value="' . ($cfg->{ems_instance_id} // '') . '"></label>';
print '<label>Scope<br><input name="scope" type="text" value="' . ($cfg->{scope} // '') . '"> <small>Use <code>openid offline_access</code> if allowed.</small></label>';
print '<label>Response mode<br><input name="response_mode" type="text" value="' . ($cfg->{response_mode} // '') . '"></label>';
print '<label>Timezone<br><input name="timezone" type="text" value="' . ($cfg->{timezone} // '') . '"></label>';
print '</fieldset>';

print '<fieldset><legend>MQTT</legend>';
my $mqtt_checked = $cfg->{mqtt_enabled} ? ' checked' : '';
print '<label><input type="checkbox" name="mqtt_enabled"' . $mqtt_checked . '> Enable MQTT</label>';
print '<label>Broker host<br><input name="mqtt_host" type="text" value="' . ($cfg->{mqtt_host} // '') . '"></label>';
print '<label>Broker port<br><input name="mqtt_port" type="text" value="' . ($cfg->{mqtt_port} // '') . '"></label>';
print '<label>Username (optional)<br><input name="mqtt_username" type="text" value="' . ($cfg->{mqtt_username} // '') . '"></label>';
print '<label>Password (optional)<br><input type="password" name="mqtt_password" placeholder="(enter to update)"></label>';
print '<label>Raw topic<br><input name="mqtt_topic_raw" type="text" size="50" value="' . ($cfg->{mqtt_topic_raw} // '') . '"></label>';
print '<label>Summary topic<br><input name="mqtt_topic_summary" type="text" size="50" value="' . ($cfg->{mqtt_topic_summary} // '') . '"></label>';
print '<label>Fallback tariff name<br><input name="fallback_tariff_name" type="text" value="' . ($cfg->{fallback_tariff_name} // '') . '"></label>';
print '</fieldset>';

print '<fieldset><legend>Advanced</legend>';
print '<label>Token store path (optional)<br><input name="token_store_path" type="text" size="80" value="' . ($cfg->{token_store_path} // '') . '"><br>';
print '<small>Example: <code>/opt/loxberry/data/ekz/tokens.json</code></small></label>';
print '</fieldset>';

print '<p class="actions"><button type="submit">Save</button> ';
print '<a href="' . $BASEURL . '/index.cgi">Back</a></p>';

print '</form></body></html>';


##########################################################################
# Update cron schedule based on fetch_schedule setting
##########################################################################
sub update_cron_schedule {
    my ($schedule) = @_;
    
    # Determine cron file based on frequency
    my $cron_file;
    my $cron_content;
    
    my $fetch_script = "$lbphtmlauthdir/run_rolling_fetch.cgi";
    
    if ($schedule eq '1') {
        # Once per day at 18:05
        $cron_file = "$lbhomedir/system/cron/cron.daily/$lbpplugindir";
        $cron_content = "#!/bin/bash\n# Run at 18:05 daily\n";
        $cron_content .= "if [ \$(date +\\%H:\\%M) = \"18:05\" ]; then\n";
        $cron_content .= "  curl -s http://localhost/admin/plugins/$lbpplugindir/run_rolling_fetch.cgi >/dev/null 2>&1\n";
        $cron_content .= "fi\n";
    }
    elsif ($schedule eq '2') {
        # Twice per day at 18:05 and 06:05
        $cron_file = "$lbhomedir/system/cron/cron.hourly/$lbpplugindir";
        $cron_content = "#!/bin/bash\n# Run at 18:05 and 06:05\n";
        $cron_content .= "HOUR=\$(date +\\%H)\nMINUTE=\$(date +\\%M)\n";
        $cron_content .= "if [[ \$MINUTE == \"05\" && (\$HOUR == \"18\" || \$HOUR == \"06\") ]]; then\n";
        $cron_content .= "  curl -s http://localhost/admin/plugins/$lbpplugindir/run_rolling_fetch.cgi >/dev/null 2>&1\n";
        $cron_content .= "fi\n";
    }
    elsif ($schedule eq '12') {
        # Every 2 hours (12x per day)
        $cron_file = "$lbhomedir/system/cron/cron.hourly/$lbpplugindir";
        $cron_content = "#!/bin/bash\n# Run every 2 hours\n";
        $cron_content .= "HOUR=\$(date +\\%H)\n";
        $cron_content .= "if (( \$HOUR % 2 == 0 )); then\n";
        $cron_content .= "  curl -s http://localhost/admin/plugins/$lbpplugindir/run_rolling_fetch.cgi >/dev/null 2>&1\n";
        $cron_content .= "fi\n";
    }
    elsif ($schedule eq '24') {
        # Every hour (24x per day)
        $cron_file = "$lbhomedir/system/cron/cron.hourly/$lbpplugindir";
        $cron_content = "#!/bin/bash\n# Run every hour\n";
        $cron_content .= "curl -s http://localhost/admin/plugins/$lbpplugindir/run_rolling_fetch.cgi >/dev/null 2>&1\n";
    }
    else {
        return 0;  # Invalid schedule
    }
    
    # Remove old cron files from other locations
    my @cron_dirs = ("$lbhomedir/system/cron/cron.01min",
                     "$lbhomedir/system/cron/cron.03min",
                     "$lbhomedir/system/cron/cron.05min",
                     "$lbhomedir/system/cron/cron.10min",
                     "$lbhomedir/system/cron/cron.15min",
                     "$lbhomedir/system/cron/cron.30min",
                     "$lbhomedir/system/cron/cron.hourly",
                     "$lbhomedir/system/cron/cron.daily");
    
    foreach my $dir (@cron_dirs) {
        my $old_file = "$dir/$lbpplugindir";
        unlink $old_file if -e $old_file;
    }
    
    # Write new cron file
    eval {
        open my $fh, '>', $cron_file or die "Cannot write $cron_file: $!";
        print $fh $cron_content;
        close $fh;
        chmod 0755, $cron_file;
    };
    
    return $@ ? 0 : 1;
}

