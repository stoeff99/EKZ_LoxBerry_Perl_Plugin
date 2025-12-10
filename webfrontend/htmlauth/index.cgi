#!/usr/bin/perl
use strict;
use warnings;

use LoxBerry::Web;
use LoxBerry::System;

# SDK globals under strict
our ($lbpurl, $lbptemplatedir);

# LoxBerry header / footer
LoxBerry::Web::lbheader("EKZ Dynamic Price", "", "");

print qq{
  <h2>EKZ Dynamic Price</h2>
  <p>
    $lbpurl/start.cgiSign in (OIDC)</a> |
    $lbpurl/run_rolling_fetch.cgiFetch now (rolling 24h)</a> |
    <a href="$lbpurl/health.cgi> |
    $lbpurl/settings.cgiSettings</a>
  </p>
};

LoxBerry::Web::lbfooter();
