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
my ($start_iso, $end_iso) = build_scheduled_window();

# Ensure linked
my ($link_status, $link_url) = ensure_linked($cfg);
if ($link_status ne 'linked') {
  print encode_json({ error => 'link_required', linking_process_redirect_uri => $link_url });
  exit;
}

# Customer-only fetch
my $access = ensure_access_token($cfg);
my %hdr = ( Authorization => "Bearer $access", accept => "application/json" );
my $base = $cfg->{api_base};

my $payload = get_json_with_retry(
  "$base/customerTariffs", \%hdr,
  { ems_instance_id => $cfg->{ems_instance_id}, start_timestamp => $start_iso, end_timestamp => $end_iso },
  int($cfg->{retries})
);

print encode_json({
  from => $start_iso, to => $end_iso, source => 'customer',
  rows => $payload->{rows} // [], interval_count => $payload->{interval_count} // 0
});
