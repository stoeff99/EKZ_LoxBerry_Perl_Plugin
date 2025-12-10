#!/usr/bin/perl

use FindBin;
require "$FindBin::Bin/common.pl";

use strict;
use warnings;
use JSON::PP;
use File::Spec;
require 'common.pl';

print "Content-Type: text/plain; charset=utf-8\n\n";

eval {
    my $cfg = load_cfg();
    my $access = ensure_access_token($cfg);
    my ($start_iso, $end_iso) = build_scheduled_window();
    my ($payload, $source) = fetch_window($cfg, $access, $start_iso, $end_iso);

    my $prices = $payload->{prices} // [];
    my $file = File::Spec->catfile('/opt/loxberry/data/plugins/ekz_dynamic_price_perl', $cfg->{output_base}.'.json');
    open my $fh, '>', $file or die "Cannot write $file: $!";
    print $fh encode_json({
        from => $start_iso, to => $end_iso,
        source => $source,
        rows => $prices,
        interval_count => scalar(@$prices)
    });
    close $fh;

    print "OK intervals=".(scalar(@$prices))." source=$source\n";
    1;
} or do {
    my $err = $@ || 'unknown error';
    print "ERROR: $err\n";
};
