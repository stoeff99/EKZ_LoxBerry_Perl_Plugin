#!/usr/bin/perl
use strict;
use warnings;
use File::Spec;

print "Content-Type: text/plain; charset=utf-8\n\n";

my $lbpdatadir = '/opt/loxberry/data/plugins/ekz_dynamic_price_perl';
my $cfg_path = File::Spec->catfile($lbpdatadir, 'ekz_config.json');
my $tok_plugin = File::Spec->catfile($lbpdatadir, 'tokens.json');
my $tok_central = '/opt/loxberry/data/ekz/tokens.json';

my $cfg_ok = -f $cfg_path ? 'OK' : 'MISSING';
my $tok_file = -f $tok_central ? $tok_central : (-f $tok_plugin ? $tok_plugin : '');
my $tok_ok = $tok_file ? "present ($tok_file)" : 'MISSING';

print "health: OK\n";
print "config: $cfg_ok\n";
print "tokens: $tok_ok\n";
