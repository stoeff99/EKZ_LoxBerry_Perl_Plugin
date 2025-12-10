#!/usr/bin/perl
use strict;
use warnings;

use LoxBerry::System;
use FindBin;
require "$FindBin::Bin/common.pl";

use JSON::PP;
use File::Spec;

our ($lbpdatadir, $lbpurl, $lbptemplatedir);

print "Content-Type: text/plain; charset=utf-8\n\n";

eval {
  my $cfg = load_cfg();
  my $access = ensure_access_token($cfg);
  my ($start_iso, $end_iso) = build_scheduled_window();
  my ($payload, $source)    = fetch_window($cfg, $access, $start_iso, $end_iso);

  my $prices = $payload->{prices} // [];
  my $file   = File::Spec->catfile($lbpdatadir, $cfg->{output_base}.'.json');

  my $doc = {
    from            => $start_iso,
    to              => $end_iso,
    source          => $source,
    rows            => $prices,
    interval_count  => scalar(@$prices),
  };

  open my $fh, '>', $file or die "Cannot write $file: $!";
  print $fh encode_json($doc);
  close $fh;

  # Publish MQTT (raw payload and summary)
  eval { publish_mqtt($cfg, $cfg->{mqtt_topic_raw}, $payload) };
  eval { publish_mqtt($cfg, $cfg->{mqtt_topic_summary}, $doc) };

  print "OK intervals=".(scalar(@$prices))." source=$source\n";
  1;
} or do {
  my $err = $@ || 'unknown error';
  print "ERROR: $err\n";
};

