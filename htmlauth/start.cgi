#!/usr/bin/perl
use strict;
use warnings;

use LoxBerry::System;            # SDK globals ($lbpdatadir, $lbpurl, $lbptemplatedir)
use CGI;
use JSON::PP;
use FindBin;
use URI::Escape qw(uri_escape);  # NEW: for URL-encoding
require "$FindBin::Bin/common.pl";

our ($lbpdatadir, $lbpurl, $lbptemplatedir);

my $q = CGI->new;
print $q->redirect( -uri => _build_auth_url() );
exit;

sub _build_auth_url {
  my $cfg   = load_cfg();
  my $state = _randhex(16);
  my $nonce = _randhex(16);

  # Persist state/nonce so callback.cgi can validate
  my $stpath = "$lbpdatadir/oidc_state.json";
  open my $fh, '>', $stpath or die "Cannot write $stpath: $!";
  print $fh encode_json({ state => $state, nonce => $nonce });
  close $fh;
  chmod 0640, $stpath;

  my $auth = $cfg->{auth_server_base} . "/realms/$cfg->{realm}/protocol/openid-connect/auth";
  my $redirect_uri = ($cfg->{redirect_uri} && $cfg->{redirect_uri} ne '')
    ? $cfg->{redirect_uri}
    : "$lbpurl/callback.cgi";

  # Ensure scope includes openid and offline_access as required
  my $scope = $cfg->{scope} || 'openid offline_access';

  my %p = (
    client_id     => $cfg->{client_id},
    response_type => 'code',
    response_mode => $cfg->{response_mode} || 'query',
    scope         => $scope,
    redirect_uri  => $redirect_uri,
    state         => $state,
    nonce         => $nonce,
  );

  # URL-encode all parameter values (robust against spaces/colons in redirect_uri/scope)
  my @pairs = map { $_ . '=' . uri_escape($p{$_}) } sort keys %p;
  my $qs = join '&', @pairs;

  return "$auth?$qs";
}
