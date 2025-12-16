#!/usr/bin/perl
use strict;
use warnings;

use CGI;
use LoxBerry::System;
use FindBin;
require "$FindBin::Bin/common.pl";

our ($lbpdatadir, $lbpurl, $lbptemplatedir);

my $q = CGI->new;
print $q->header('application/json; charset=utf-8');

my $cfg = load_cfg();

# Ensure linked; if not, instruct user to link first
my ($link_status, $link_url) = ensure_linked($cfg);
if ($link_status eq 'link_required') {
  my $msg = {
    error => 'link_required',
    message => 'EMS is not linked. Redirect customer to linking flow.',
    linking_process_redirect_uri => $link_url,
  };
  print encode_json($msg);
  exit;
}

# Build window: now 18:00 local → +24h
my ($start_iso, $end_iso) = build_scheduled_window();

# Try customer tariffs first, fallback to public tariffs
my $access = ensure_access_token($cfg);
my ($payload, $source) = fetch_window($cfg, $access, $start_iso, $end_iso);

# Return JSON
my $out = {
  from         => $start_iso,
  to           => $end_iso,
  source       => $source,
  rows         => $payload->{rows} // [],
  interval_count => $payload->{interval_count} // 0,
};
print encode_json($out);
