#!/usr/bin/perl

use FindBin;
require "$FindBin::Bin/common.pl";

use strict;
use warnings;
use CGI;
use JSON::PP;
use LWP::UserAgent;
use HTTP::Request::Common qw(POST);
require 'common.pl';

my $q = CGI->new;
my $error = $q->param('error');
my $state = $q->param('state');
my $code  = $q->param('code');

if ($error) { print $q->header('text/plain'); print "OIDC error: $error\n"; exit; }
unless ($state && $code) { print $q->header('text/plain'); print "Missing state or code\n"; exit; }

# validate state from file
my $stpath = '/opt/loxberry/data/plugins/ekz_dynamic_price_perl/oidc_state.json';
my $expected = undef;
if (-f $stpath) {
    open my $fh, '<', $stpath; local $/ = undef; my $raw = <$fh>; close $fh;
    my $st = eval { decode_json($raw) } // {};
    $expected = $st->{state};
}
unless ($expected && $state eq $expected) { print $q->header('text/plain'); print "State mismatch. Start sign-in again.\n"; exit; }

# exchange code
my $cfg  = load_cfg();
my $ua   = LWP::UserAgent->new(timeout => 30);
my $token_endpoint = $cfg->{auth_server_base} . "/realms/$cfg->{realm}/protocol/openid-connect/token";
my $res = $ua->request(POST $token_endpoint, [
    grant_type    => 'authorization_code',
    client_id     => $cfg->{client_id},
    client_secret => $cfg->{client_secret},
    code          => $code,
    redirect_uri  => $cfg->{redirect_uri},
]);

if (!$res->is_success) { print $q->header('text/plain'); print "Token HTTP ".$res->code.": ".$res->decoded_content."\n"; exit; }
my $tok = eval { decode_json($res->decoded_content) } // {};
unless ($tok->{access_token}) { print $q->header('text/plain'); print "No access_token in token response\n"; exit; }

my $persist = {
    access_token  => $tok->{access_token},
    refresh_token => $tok->{refresh_token} // '',
    expires_at    => time() + int($tok->{expires_in} // 300),
};
save_tokens($persist, $cfg);

# Instead of hard-coded 
#print $q->redirect('/admin/loxberry/webfrontend/htmlauth/plugins/ekz_dynamic_price_perl/index.html');

# redirect to your installed plugin's web path, preferably to index.cgi:
print $q->redirect("$lbpurl/index.cgi");

