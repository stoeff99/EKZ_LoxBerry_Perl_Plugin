#!/usr/bin/perl
use strict;
use warnings;

use LoxBerry::System;
use CGI;
use CGI::Carp qw(fatalsToBrowser);
use JSON::PP;
use LWP::UserAgent;
use HTTP::Request::Common qw(POST);
use MIME::Base64 ();             # for base64url decoding of JWT parts
use FindBin;
require "$FindBin::Bin/common.pl";

our ($lbpdatadir, $lbpurl, $lbptemplatedir);

my $q = CGI->new;

my $error = $q->param('error');
my $state = $q->param('state');
my $code  = $q->param('code');

if ($error) { print $q->header('text/plain'); print "OIDC error: $error\n"; exit; }
unless ($state && $code) { print $q->header('text/plain'); print "Missing state or code\n"; exit; }

# validate state (and get nonce) from file
my $stpath = "$lbpdatadir/oidc_state.json";
my ($expected_state, $expected_nonce) = ('','');
if (-f $stpath) {
  open my $fh, '<', $stpath; local $/ = undef; my $raw = <$fh>; close $fh;
  my $st = eval { decode_json($raw) } // {};
  $expected_state = $st->{state} // '';
  $expected_nonce = $st->{nonce} // '';
}
unless ($expected_state && $state eq $expected_state) { print $q->header('text/plain'); print "State mismatch. Start sign-in again.\n"; exit; }

# exchange code
my $cfg = load_cfg();
my $ua  = LWP::UserAgent->new(timeout => 30);
my $token_endpoint = $cfg->{auth_server_base} . "/realms/$cfg->{realm}/protocol/openid-connect/token";

my $redirect_uri = ($cfg->{redirect_uri} && $cfg->{redirect_uri} ne '')
  ? $cfg->{redirect_uri}
  : "$lbpurl/callback.cgi";

# Build request body WITHOUT client_secret; authenticate via HTTP Basic header
my $req = POST $token_endpoint, [
  grant_type   => 'authorization_code',
  code         => $code,
  redirect_uri => $redirect_uri,
];
# Set HTTP Basic Authorization: Basic base64(client_id:client_secret)
$req->authorization_basic($cfg->{client_id}, $cfg->{client_secret});

my $res = $ua->request($req);
if (!$res->is_success) { print $q->header('text/plain'); print "Token HTTP ".$res->code.": ".$res->decoded_content."\n"; exit; }
my $tok = eval { decode_json($res->decoded_content) } // {};
unless ($tok->{access_token}) { print $q->header('text/plain'); print "No access_token in token response\n"; exit; }

# Optional but recommended: Validate nonce from ID token (if provided)
if (my $idtok = $tok->{id_token}) {
  my $claims = _decode_jwt_claims($idtok);
  if (defined $claims->{nonce} && defined $expected_nonce && $expected_nonce ne '') {
    unless ($claims->{nonce} eq $expected_nonce) {
      print $q->header('text/plain');
      print "Nonce mismatch. Start sign-in again.\n";
      exit;
    }
  }
}

my $persist = {
  access_token  => $tok->{access_token},
  refresh_token => $tok->{refresh_token} // '',
  expires_at    => time() + int($tok->{expires_in} // 300),
};
save_tokens($persist, $cfg);

# Build redirect URL: replace /callback.cgi with /index.cgi
my $redirect_url = $redirect_uri;
$redirect_url =~ s{/callback\.cgi$}{/index.cgi};

# HTML with meta-refresh redirect
print $q->header('text/html; charset=utf-8');
print '<!DOCTYPE html>' . "\n";
print '<html><head>' . "\n";
print '<meta charset="utf-8">' . "\n";
print '<title>Login Success</title>' . "\n";
print '<meta http-equiv="refresh" content="2; url=' . $redirect_url . '">' . "\n";
print '</head><body>' . "\n";
print '<h2>Login Successful!</h2>' . "\n";
print '<p>Redirecting to plugin UI...</p>' . "\n";
print '<p><a href="' . $redirect_url . '">Click here if not redirected</a></p>' . "\n";
print '</body></html>' . "\n";

# --- helpers ---
sub _decode_jwt_claims {
  my ($jwt) = @_;
  my (undef, $payload_b64, undef) = split /\./, $jwt, 3;
  return {} unless $payload_b64;
  my $json = _b64url_decode($payload_b64);
  my $claims = eval { decode_json($json) } || {};
  return $claims;
}

sub _b64url_decode {
  my ($s) = @_;
  $s =~ tr/-_/+\//;
  $s .= '=' x ((4 - length($s) % 4) % 4);
  return MIME::Base64::decode_base64($s);
}
