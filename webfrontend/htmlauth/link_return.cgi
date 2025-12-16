#!/usr/bin/perl
use strict;
use warnings;

use CGI;
use LoxBerry::System;
use FindBin;
require "$FindBin::Bin/common.pl";

our ($lbpdatadir, $lbpurl, $lbptemplatedir);

my $q = CGI->new;

# After EKZ sends the customer back, re-check link status
my $cfg = load_cfg();
my ($link_status, $link_url) = ensure_linked($cfg);

if ($link_status eq 'linked') {
  my $index = ($lbpurl && $lbpurl ne '') ? "$lbpurl/index.cgi" : "index.cgi";
  print $q->redirect($index);
  exit;
}

print $q->header('text/html; charset=utf-8');
print <<"HTML";
<!doctype html>
<html>
<head><meta charset="utf-8"><title>EKZ Linking Required</title></head>
<body>
  <h2>Linking still required</h2>
  <p>Please complete the linking process.</p>
  @{[ $link_url ? qq{<p><a href="$link_url">Continue linking</a></p>} : qq{<p>No linking URL received. Retry sign-in.</p>} ]}
  <p><a href="$lbpurl/start.cgi">Re-start sign-in</a></p>
</body>
</html>
HTML
