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

# --- Defaults ---
# NOTE: If you haven't renamed callback.pl -> callback.cgi yet, either rename it
# or temporarily change the default below back to .../callback.pl.
my %defaults = (
  auth_server_base     => 'https://login-test.ekz.ch/auth',
  realm                => 'myEKZ',
  client_id            => 'ems-bowles',
  client_secret        => '',
  redirect_uri         => ($cfg_from_file && $cfg_from_file->{redirect_uri}) ? $cfg_from_file->{redirect_uri} : 'https://ems.bowles.ch/callback.cgi',
    api_base             => 'https://test-api.tariffs.ekz.ch/v1',
    ems_instance_id      => 'ems-bowles',
    scope                => 'openid',            # add 'offline_access' if allowed
    response_mode        => 'query',
    timezone             => 'Europe/Zurich',
    mqtt_enabled         => JSON::PP::true,
    mqtt_host            => 'localhost',
    mqtt_port            => 1883,
    mqtt_username        => '',
    mqtt_password        => '',
    mqtt_topic_raw       => 'ekz/ems/tariffs/raw',
    mqtt_topic_summary   => 'ekz/ems/tariffs/now_plus_24h',
    fallback_tariff_name => 'electricity_standard',
    retries              => 3,
    token_store_path     => '',
    fetch_schedule       => '1'  # 1=once/day, 2=twice/day, 12=every 2h, 24=hourly
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
            scope response_mode timezone mqtt_topic_raw mqtt_topic_summary
            mqtt_host mqtt_port mqtt_username
            fallback_tariff_name token_store_path fetch_schedule
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
        
        # Update cron job based on fetch_schedule
        my $cron_result = update_cron_schedule($cfg->{fetch_schedule});
        if ($cron_result) {
            $msg = "<div style='color:#080'>Settings saved. Cron schedule updated.</div>";
        } else {
            $msg = "<div style='color:#f80'>Settings saved but cron update failed. Check permissions.</div>";
        }
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

print '<fieldset><legend>Scheduling</legend>';
print '<label>Fetch frequency<br>';
print '<select name="fetch_schedule">';
my @schedules = (
  ['1', '1x per day (at 18:05)'],
  ['2', '2x per day (at 18:05 and 06:05)'],
  ['12', '12x per day (every 2 hours)'],
  ['24', '24x per day (every hour)']
);
foreach my $opt (@schedules) {
  my $sel = ($cfg->{fetch_schedule} eq $opt->[0]) ? ' selected' : '';
  print '<option value="' . $opt->[0] . '"' . $sel . '>' . $opt->[1] . '</option>';
}
print '</select></label>';
print '<p class="hint">Data is published at 18:00 daily. The plugin fetches a rolling 24h window to ensure you always have current + next day data.</p>';
print '</fieldset>';

print '<fieldset><legend>Advanced</legend>';
print '<label>Token store path (optional)<br><input name="token_store_path" type="text" size="80" value="' . $cfg->{token_store_path} . '"><br>';
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
