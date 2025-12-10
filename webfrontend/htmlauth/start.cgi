#!/usr/bin/perl
use strict;
use warnings;

use LoxBerry::System;            # SDK: provides $lbpdatadir, $lbpurl, $lbptemplatedir
use CGI;
use JSON::PP;
use FindBin;
require "$FindBin::Bin/common.pl";   # safe local include of your helper

# Declare SDK globals so 'strict' allows them
our ($lbpdatadir, $lbpurl, $lbptemplatedir);

my $q = CGI->new;

# Immediately redirect the browser to the OIDC auth URL
print $q->redirect( -uri => _build_auth_url() );
exit;

# -------- Helpers --------

sub _build_auth_url {

    # Load config via common.pl (common.pl should use $lbpdatadir internally)
    my $cfg = load_cfg();

    my $state = _randhex(16);
    my $nonce = _randhex(16);

    # Persist state/nonce so callback.cgi can validate
    my $stpath = "$lbpdatadir/oidc_state.json";
    open my $fh, '>', $stpath or die "Cannot write $stpath: $!";
    print $fh encode_json({ state => $state, nonce => $nonce });
    close $fh;
    chmod 0640, $stpath;

    # Build the OIDC authorization URL
    my $auth = $cfg->{auth_server_base} . "/realms/$cfg->{realm}/protocol/openid-connect/auth";

    # Default redirect_uri to your plugin's callback if empty
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

    my $qs = join '&', map { $_ . '=' . $p{$_} } keys %p;
    return "$auth?$qs";
}

sub _randhex {
    my ($bytes) = @_;
    my @h = map { sprintf "%02x", int(rand(256)) } (1..$bytes);
    return join('', @h);
}
