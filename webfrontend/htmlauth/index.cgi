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
  <p>
    $lbpurl/start.cgiSign in (OIDC)</a> |
    $lbpurl/run_rolling_fetch.cgiFetch now (rolling 24h)</a> |
    $lbpurl/health.cgiHealth</a> |
    $lbpurl/settings.cgiSettings</a>
  </p>
</body>
</html>
HTML

