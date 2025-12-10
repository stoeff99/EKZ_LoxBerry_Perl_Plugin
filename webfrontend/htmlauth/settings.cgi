#!/usr/bin/perl
use strict;
use warnings;

use CGI;
my $q = CGI->new;

print $q->header('text/html; charset=utf-8');
print <<'HTML';
<!doctype html>
<html><head><meta charset="utf-8"><title>Settings minimal</title></head>
<body>
  <h2>Settings minimal</h2>
  <p>If you see this, CGI execution and headers work.</p>
  <p>index.htmlBack</a></p>
</body></html>
