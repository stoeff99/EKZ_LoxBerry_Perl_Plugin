#!/usr/bin/perl
use strict;
use warnings;
use File::Spec;
use LoxBerry::System;
use LoxBerry::Log;

# Get plugin data directory from LoxBerry SDK
our ($lbpdatadir, $lbplogdir);

# Initialize logging
my $log = LoxBerry::Log->new(
  name      => 'rotate',
  filename  => "$lbplogdir/rotate.log",
  append    => 1,
  loglevel  => 6,
  addtime   => 1,
  stderr    => 0,
  nosession => 1,
);

LOGSTART("rotate_tariffs started");

my $today_file = File::Spec->catfile($lbpdatadir, 'tariffs_today.json');
my $tomorrow_file = File::Spec->catfile($lbpdatadir, 'tariffs_tomorrow.json');

if (-f $tomorrow_file) {
  # Copy tomorrow to today (overwrite if exists)
  if (open my $src, '<', $tomorrow_file) {
    local $/ = undef;
    my $content = <$src>;
    close $src;
    if (open my $dst, '>', $today_file) {
      print $dst $content;
      close $dst;
      chmod 0640, $today_file;
      LOGINF("Rotated tariffs_tomorrow.json -> tariffs_today.json at midnight");
    } else {
      LOGWARN("Failed to write to $today_file during rotation: $!");
    }
  } else {
    LOGWARN("Failed to read $tomorrow_file during rotation: $!");
  }
  unlink $tomorrow_file or LOGWARN("Failed to delete $tomorrow_file after rotation: $!");
} else {
  LOGWARN("No tariffs_tomorrow.json found to rotate.");
}

LOGEND("rotate_tariffs completed");

exit 0;
