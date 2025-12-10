#!/usr/bin/perl
use strict;
use warnings;

use CGI;
use LoxBerry::System;

# Declare SDK globals under 'strict'
our ($lbpdatadir, $lbpurl, $lbptemplatedir);

my $q = CGI->new;
print $q->header('text/html; charset=utf-8');

print <<"HTML";
<!doctype html>
<html>
<head><meta charset="utf-8"><title>EKZ Dynamic Price (Perl)</title></head>
<body>
  <h2>EKZ Dynamic Price (Perl)</h2>
  <nav>
    <a href="$lbpurl/start.cgi">Sign in (OIDC)</a> |
    <a href="$lbpurl/run_rolling_fetch.cgi">Fetch now (rolling 24h)</a> |
    <a href="$lbpurl/health.cgi">Health</a> |
    <a href="$lbpurl/settings.cgi">Settings</a>
  </nav>
</body>
</html>
HTML

