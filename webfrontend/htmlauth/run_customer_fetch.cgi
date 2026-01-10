#!/usr/bin/perl
use strict;
use warnings;
use CGI::Carp qw(fatalsToBrowser);
use CGI;
use JSON::PP;
use Time::Piece;
use POSIX qw(strftime);
use File::Spec;
use FindBin;
require "$FindBin::Bin/common.pl";

our ($lbpdatadir);

my $q = CGI->new;
print $q->header('application/json; charset=utf-8');

my $cfg = load_cfg();

# Use configured timezone for window building
if ($cfg->{timezone}) {
  local $ENV{TZ} = $cfg->{timezone};
}

# Build TODAY (00:00..23:59:59) unless next-day is explicitly requested via ?nextday=1
my $nextday = ($q->param('nextday') // '') eq '1' ? 1 : 0;

my ($start_iso, $end_iso);
my $now = localtime(time);

if ($nextday) {
  ($start_iso, $end_iso) = build_scheduled_window();
} else {
  my $start = Time::Piece->strptime($now->strftime('%Y-%m-%d') . ' 00:00:00', '%Y-%m-%d %H:%M:%S');
  my $end   = $start + 24*3600 - 1;
  my $off   = $now->strftime('%z'); $off =~ s/^([+-])(\d{2})(\d{2})$/$1$2:$3/;
  $start_iso = $start->strftime('%Y-%m-%dT%H:%M:%S') . $off;
  $end_iso   = $end->strftime('%Y-%m-%dT%H:%M:%S') . $off;
}

# Fetch customer tariffs window
my $payload;
eval {
  $payload = fetch_customer_tariffs_window($cfg, $start_iso, $end_iso);
  1;
} or do {
  my $err = $@ || 'unknown';
  print encode_json({ error => 'customer_fetch_failed', message => $err });
  exit 0;
};

# Normalize and respond, with explicit source=customer
my $rows = $payload->{rows} // $payload->{prices} // [];
my $doc = {
  publication_timestamp => strftime('%Y-%m-%dT%H:%M:%S', localtime) . do {
    my $z = strftime('%z', localtime); $z =~ s/(\+|-)(\d{2})(\d{2})/$1$2:$3/; $z;
  },
  source                => 'customer',
  from                  => $start_iso,
  to                    => $end_iso,
  prices                => $rows,
  rows                  => $rows,
  interval_count        => scalar(@$rows),
};

print JSON::PP->new->pretty(1)->encode($doc);
