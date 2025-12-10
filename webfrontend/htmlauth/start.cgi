#!/usr/bin/perl
use strict;
use warnings;
use CGI;
use JSON::PP;
require 'common.pl';

my $q = CGI->new;
print $q->redirect(-uri => _build_auth_url());

sub _build_auth_url {
    my $cfg = load_cfg();
    my $state = _randhex(16);
    my $nonce = _randhex(16);
    # save to file for cross-domain callback
    my $stpath = '/opt/loxberry/data/plugins/ekz_dynamic_price_perl/oidc_state.json';
    open my $fh, '>', $stpath or die "Cannot write $stpath: $!";
    print $fh encode_json({ state => $state, nonce => $nonce });
    close $fh;

    my $auth = $cfg->{auth_server_base} . "/realms/$cfg->{realm}/protocol/openid-connect/auth";
    my %p = (
        client_id     => $cfg->{client_id},
        response_type => 'code',
        response_mode => $cfg->{response_mode},
        scope         => $cfg->{scope},
        redirect_uri  => $cfg->{redirect_uri},
        state         => $state,
        nonce         => $nonce,
    );
    my $qs = join '&', map { $_.'='.$p{$_} } keys %p;
    return "$auth?$qs";
}

sub _randhex { my ($bytes) = @_; my @h = map { sprintf "%02x", int(rand(256)) } (1..$bytes); return join('', @h); }
