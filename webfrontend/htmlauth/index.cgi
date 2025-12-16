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

# Load config and ensure EMS link is established
my $cfg = load_cfg();
my ($link_status, $link_url) = ensure_linked($cfg);
if ($link_status eq 'link_required' && $link_url) {
  # Redirect the customer to EKZ's linking flow
  print $q->redirect($link_url);
  exit;
}

print $q->header('text/html; charset=utf-8');

print <<"HTML";
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>EKZ Dynamic Price</title>
  <link rel="stylesheet" href="$BASEURL/style.css">
</head>
<body id="ekz-plugin" class="plugincontent">
  <h2>EKZ Dynamic Price (Perl)</h2>
  <nav>
    <a href="$BASEURL/start.cgi">Sign in (OIDC)</a> |
    <a href="$BASEURL/run_rolling_fetch.cgi">Fetch now (rolling 24h)</a> |
    <a href="$BASEURL/health.cgi">Health</a> |
    <a href="$BASEURL/settings.cgi">Settings</a>
  </nav>
  <p>EMS link status: $link_status</p>
</body>
</html>
HTML
