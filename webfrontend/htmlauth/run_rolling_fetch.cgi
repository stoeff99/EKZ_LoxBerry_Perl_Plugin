#!/usr/bin/perl
use strict;
use warnings;

# Show Perl runtime errors in the browser for debugging (remove or restrict later)
use CGI::Carp qw(fatalsToBrowser);

use CGI;
use JSON::PP;
use File::Spec;
use LoxBerry::System;
use FindBin;
require "$FindBin::Bin/common.pl";

our ($lbpdatadir, $lbpurl, $lbptemplatedir);

my $q = CGI->new;

# We'll always send JSON content-type (even on errors)
print $q->header('application/json; charset=utf-8');

# Run the main logic inside an eval to capture any die() and return JSON error details
my $ok = eval {
    my $cfg = load_cfg();

    # Ensure linked; if not, instruct user to link first (return structured JSON)
    my ($link_status, $link_url) = try_ensure_linked($cfg);
    if ($link_status eq 'not_signed_in') {
      print encode_json({ error => 'not_signed_in', message => 'User not signed in. Please sign in via the plugin UI.' });
      return 1;
    }
    if ($link_status eq 'link_required') {
      print encode_json({
        error => 'link_required',
        message => 'EMS is not linked to customer account. Redirect customer to linking flow.',
        linking_process_redirect_uri => $link_url,
      });
      return 1;
    }
    if ($link_status eq 'error') {
      # show the specific error reported by try_ensure_linked (third return param)
      my (undef, undef, $err) = try_ensure_linked($cfg);
      print encode_json({ error => 'link_check_failed', message => $err // 'Unknown error checking link status' });
      return 1;
    }

    # Build window: now 18:00 local → +24h
    my ($start_iso, $end_iso) = build_scheduled_window();

    # Try customer tariffs first, fallback to public tariffs
    my $access = ensure_access_token($cfg);      # may die -> caught by eval
    my ($payload, $source) = fetch_window($cfg, $access, $start_iso, $end_iso);

    # Defensive normalization: ensure $payload is a HASH ref and $source is a string label
    if (!defined $payload || ref($payload) ne 'HASH') {
      # If the values were accidentally swapped by fetch_window, fix it:
      if (defined $source && ref($source) eq 'HASH') {
        ($payload, $source) = ($source, $payload);
      }
    }

    # If still not a hashref, return a helpful error (and log it)
    unless (defined $payload && ref($payload) eq 'HASH') {
      my $ptype = defined $payload ? ref($payload) || 'SCALAR' : 'UNDEF';
      my $stype = defined $source  ? ref($source)  || 'SCALAR' : 'UNDEF';
      my $msg = "Unexpected response from fetch_window: payload_type=$ptype, source_type=$stype, payload_value="
                . (defined $payload ? "$payload" : '<undef>') . ", source_value=" . (defined $source ? "$source" : '<undef>');
      # log
      eval {
        my $logfile = File::Spec->catfile($lbpdatadir, 'fetch.log');
        if (open my $fh, '>>', $logfile) {
          print $fh scalar(localtime) . " - run_rolling_fetch: $msg\n";
          close $fh;
        }
        1;
      };
      print encode_json({ error => 'invalid_fetch_response', message => $msg });
      return 1;
    }

    # SUCCESS PATH: persist and publish full payload
    eval { save_tariffs_json($cfg, $payload, $source, $start_iso, $end_iso); 1 };
    eval { publish_tariffs_to_mqtt($cfg, $payload, $source, $start_iso, $end_iso); 1 };

    # Return JSON to caller
    my $out = {
      from           => $start_iso,
      to             => $end_iso,
      source         => $source // 'unknown',
      rows           => $payload->{rows} // [],
      interval_count => $payload->{interval_count} // 0,
    };
    print encode_json($out);
    return 1;
};

if (!$ok) {
  my $err = $@ // 'Unknown exception';
  # Log error to plugin fetch.log for investigation
  eval {
    my $logfile = File::Spec->catfile($lbpdatadir, 'fetch.log');
    if (open my $fh, '>>', $logfile) {
      print $fh scalar(localtime) . " - run_rolling_fetch CGI died: $err\n";
      close $fh;
    }
    1;
  };

  # Return JSON error to the caller (do not dump sensitive internals in production)
  print encode_json({ error => 'internal_error', message => "$err" });
}

exit 0;
