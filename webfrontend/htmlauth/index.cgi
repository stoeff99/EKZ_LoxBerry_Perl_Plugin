#!/usr/bin/perl
use strict;
use warnings;

use CGI;
use LoxBerry::System;
use FindBin;
require "$FindBin::Bin/common.pl";

# Declare SDK globals under 'strict'
our ($lbpdatadir, $lbpurl, $lbptemplatedir);

# Prefer SDK-provided base URL, otherwise derive from the current script path
my $BASEURL = $lbpurl;
if (!$BASEURL) {
  my $path = $ENV{SCRIPT_NAME} // '';
  $path =~ s{/[^/]+$}{};   # drop the filename
  $BASEURL = $path || '.'; # fallback to relative dir
}

my $q = CGI->new;
my $cfg = load_cfg();

# Decide what to do based on sign-in and link status
my $signed_in = has_tokens($cfg);
my ($link_status, $link_url, $err) = try_ensure_linked($cfg) if $signed_in;

print $q->header('text/html; charset=utf-8');

my $status_line = !$signed_in       ? 'Not signed in'
                 : $link_status eq 'linked' ? 'Linked'
                 : $link_status eq 'link_required' ? 'Link required'
                 : 'Unknown';

my $linking_note = '';
if ($signed_in && defined $link_status && $link_status eq 'link_required' && $link_url) {
  $linking_note = qq{<p><a href="$link_url">Complete EKZ linking</a></p>};
} elsif (defined $err && $err ne '') {
  # Optional: show a gentle error (avoid crashing the page)
  $linking_note = qq{<p style="color:#b00">Link check error: } . CGI::escapeHTML($err) . qq{</p>};
}

print <<"HTML";
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>EKZ Dynamic Price</title>
  <link rel="stylesheet" href="$BASEURL/style.css">
</head>
<body id="ekz-plugin" class="plugincontent">
  <h2>EKZ Dynamic Price</h2>
  <nav>
    <a href="$BASEURL/start.cgi">Sign in (OIDC)</a> |
    <a href="$BASEURL/run_rolling_fetch.cgi">Fetch now (rolling 24h)</a> |
    <a href="$BASEURL/health.cgi">Health</a> |
    <a href="$BASEURL/settings.cgi">Settings</a>
  </nav>

  <p>Status: $status_line</p>
  $linking_note
</body>
</html>
HTML
