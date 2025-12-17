#!/usr/bin/perl
use strict;
use warnings;

use CGI;
use LoxBerry::System;
use FindBin;
require "$FindBin::Bin/common.pl";

# SDK globals
our ($lbpdatadir, $lbpurl, $lbptemplatedir);

# Base URLs
my $BASEURL    = $lbpurl;
if (!$BASEURL) {
  my $path = $ENV{SCRIPT_NAME} // '';
  $path =~ s{/[^/]+$}{};
  $BASEURL = $path || '.';
}
my $ASSET_BASE = "$BASEURL/assets";
my $ICON_BASE  = "$BASEURL/Icons";

my $q   = CGI->new;
my $cfg = load_cfg();

# Sign-in / link status
my $signed_in = has_tokens($cfg);
my ($link_status, $link_url, $err) = try_ensure_linked($cfg) if $signed_in;

print $q->header('text/html; charset=utf-8');

my $status_line = !$signed_in                           ? 'Not signed in'
                 : (($link_status // '') eq 'linked')        ? 'Linked to myEKZ'
                 : (($link_status // '') eq 'link_required') ? 'Link required'
                 : 'Unknown';

my $linking_note = '';
if ($signed_in && defined $link_status && $link_status eq 'link_required' && $link_url) {
  $linking_note = qq{<p class="alert alert-warn"><a href="$link_url">Complete EKZ linking</a></p>};
} elsif (defined $err && $err ne '') {
  $linking_note = qq{<p class="alert alert-err">Link check error: } . CGI::escapeHTML($err) . qq{</p>};
}

print <<"HTML";
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>EKZ Dynamic Price</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <!-- Keep original plugin stylesheet, then our enhanced styles with cache-bust -->
  <link rel="stylesheet" href="$BASEURL/style.css">
  <link rel="stylesheet" href="$ASSET_BASE/styles.css?v=20251217">
</head>
<body id="ekz-plugin" class="plugincontent">
  <div class="app-header">
    <div class="banner">
      <div class="title">EKZ Dynamic Price</div>
    </div>
  </div>

  <div class="nav-actions">
    <a class="btn btn-primary" href="$BASEURL/start.cgi"><span class="emoji">🔐</span> Sign in (OIDC)</a>
    <a class="btn btn-green"   href="$BASEURL/run_rolling_fetch.cgi"><span class="emoji">⚡</span> Fetch now (rolling 24h)</a>
    <a class="btn btn-orange"  href="$BASEURL/health.cgi"><span class="emoji">🩺</span> Health</a>
    <a class="btn btn-slate"   href="$BASEURL/settings.cgi"><span class="emoji">⚙️</span> Settings</a>
  </div>

  <h2 class="status-title">Status: $status_line</h2>
  $linking_note
  <p>Use Settings to configure OIDC and MQTT. “Fetch now” returns JSON in the browser.</p>
</body>
</html>
HTML

exit 0;
