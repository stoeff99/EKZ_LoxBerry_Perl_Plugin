#!/usr/bin/perl
use strict;
use warnings;

use LoxBerry::System;            # SDK globals ($lbpdatadir, $lbpurl, $lbptemplatedir)
use CGI;
use JSON::PP;
use FindBin;
require "$FindBin::Bin/common.pl";

our ($lbpdatadir, $lbpurl, $lbptemplatedir);

# Local fallback implementation of _randhex in case the function is not
# provided by any included library. Returns a hex string of $len bytes
# (e.g. _randhex(16) => 32 hex chars). Safe and small.
sub _randhex {
  my ($len) = @_;
  $len ||= 16;    # default bytes
  my $s = '';
  for (1 .. $len) {
    $s .= sprintf "%02x", int(rand(256));
  }
  return $s;
}

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

  my %p = (
    client_id     => $cfg->{client_id},
    response_type => 'code',
    response_mode => $cfg->{response_mode} || 'query',
    scope         => $cfg->{scope}         || 'openid',
    redirect_uri  => $redirect_uri,
    state         => $state,
    nonce         => $nonce,
  );
  my $qs = join '&', map { $_.'='.$p{$_} } keys %p;
  return "$auth?$qs";
}
