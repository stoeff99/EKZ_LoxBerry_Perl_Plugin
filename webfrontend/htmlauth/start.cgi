#!/usr/bin/perl
use strict;
use warnings;

use LoxBerry::System;            # SDK globals ($lbpdatadir, $lbpurl, $lbptemplatedir)
use CGI;
use JSON::PP;
use URI::Escape qw(uri_escape_utf8);
use FindBin;
require "$FindBin::Bin/common.pl";

our ($lbpdatadir, $lbpurl, $lbptemplatedir);

my $q = CGI->new;
print $q->redirect( -uri => _build_auth_url() );
exit;

sub _build_auth_url {
  my $cfg = load_cfg();
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

  # ensure redirect_uri is absolute with scheme (Keycloak rejects bare hostnames)
  if ($redirect_uri !~ m{^https?://}i) {
    $redirect_uri = "https://$redirect_uri";
  }

  my %p = (
    client_id     => $cfg->{client_id},
    response_type => 'code',
    response_mode => $cfg->{response_mode} || 'query',
    scope         => $cfg->{scope}         || 'openid',
    redirect_uri  => $redirect_uri,
    state         => $state,
    nonce         => $nonce,
  );

  my $qs = join '&', map { $_ . '=' . uri_escape_utf8($p{$_}) } sort keys %p;
  return "$auth?$qs";
}
