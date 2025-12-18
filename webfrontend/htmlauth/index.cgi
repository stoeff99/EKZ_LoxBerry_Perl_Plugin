#!/usr/bin/perl
use strict;
use warnings;

use CGI;
use LoxBerry::System;
use FindBin;
require "$FindBin::Bin/common.pl";

# SDK globals
our ($lbpurl, $lbpdatadir, $lbptemplatedir);

# Base URLs
my $BASEURL    = $lbpurl;
if (!$BASEURL) {
  my $path = $ENV{SCRIPT_NAME} // '';
  $path =~ s{/[^/]+$}{};
  $BASEURL = $path || '.';
}
my $ASSET_BASE = "$BASEURL/assets";
my $ICON_BASE  = "$BASEURL/Icons";

my $q   = CGI->new;
my $cfg = load_cfg();

# Sign-in / link status
my $signed_in = has_tokens($cfg);
my ($link_status, $link_url, $err) = try_ensure_linked($cfg) if $signed_in;

print $q->header('text/html; charset=utf-8');

my $status_line = !$signed_in                           ? 'Not signed in'
                 : (($link_status // '') eq 'linked')        ? 'Linked to myEKZ'
                 : (($link_status // '') eq 'link_required') ? 'Link required'
                 : 'Unknown';

my $linking_note = '';
if ($signed_in && defined $link_status && $link_status eq 'link_required' && $link_url) {
  $linking_note = qq{<p class="alert alert-warn"><a href="$link_url">Complete EKZ linking</a></p>};
} elsif (defined $err && $err ne '') {
  $linking_note = qq{<p class="alert alert-err">Link check error: } . CGI::escapeHTML($err) . qq{</p>};
}

print <<"HTML";
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>EKZ Dynamic Price</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="preload" as="image" href="$ICON_BASE/banner.jpg">
  <link rel="stylesheet" href="$BASEURL/style.css">
  <link rel="stylesheet" href="$ASSET_BASE/styles.css?v=20251217">
</head>
<body id="ekz-plugin" class="plugincontent">
  <div class="app-header">
    <div class="banner">
      <div class="title">EKZ Dynamic Price</div>
    </div>
  </div>

  <div class="nav-actions">
    <a class="btn btn-primary" href="$BASEURL/start.cgi"><span class="emoji">🔐</span> Sign in (OIDC)</a>
    <a class="btn btn-green"   href="$BASEURL/run_rolling_fetch.cgi"><span class="emoji">⚡</span> Fetch now (rolling 24h)</a>
    <a class="btn btn-orange"  href="$BASEURL/health.cgi"><span class="emoji">🩺</span> Health</a>
    <a class="btn btn-slate"   href="$BASEURL/settings.cgi"><span class="emoji">⚙️</span> Settings</a>
  </div>

  <h2 class="status-title">Status: $status_line</h2>
  $linking_note
  <p>Use Settings to configure OIDC and MQTT. “Fetch now” returns JSON in the browser.</p>

<style>
    /* Lightweight table styling for the costs panel */
    .costs-table { width: 100%; border-collapse: collapse; margin-top: 6px; }
    .costs-table th, .costs-table td { padding: 8px 10px; border-bottom: 1px solid rgba(255,255,255,.08); }
    .costs-table th { text-align: left; color: #9fb0c9; font-weight: 700; }
    .costs-table td.cost { text-align: right; font-variant-numeric: tabular-nums; }
    .muted { color: #9fb0c9; }
  </style>

  <div class="container">
    <!-- New panel: Next 12 hours based on hourly averages (from compute_costs.cgi) -->
    <div class="card" id="next12h-card">
      <div style="display:flex;align-items:baseline;gap:10px;">
        <h3 style="margin:0;">Next 12 hours</h3>
        <span class="small muted">Hourly average = mean of four 15‑min intervals</span>
      </div>
      <div id="next12h-note" class="small muted" style="margin-top:6px;">Reading computed costs…</div>
      <table class="costs-table" id="next12h-table" style="display:none;">
        <thead>
          <tr>
            <th scope="col">Hour (local)</th>
            <th scope="col" class="cost">Avg total (CHF/kWh)</th>
          </tr>
        </thead>
        <tbody id="next12h-body"></tbody>
      </table>
    </div>

    <div class="card">
      <p>Use Settings to configure OIDC and MQTT. “Fetch now” returns JSON in the browser.</p>
    </div>
  </div>

  <script>
    (function() {
      const note   = document.getElementById('next12h-note');
      const table  = document.getElementById('next12h-table');
      const tbody  = document.getElementById('next12h-body');

      function fmtTime(iso) {
        const d = new Date(iso);
        if (isNaN(d)) return iso;
        return d.toLocaleString(undefined, { hour: '2-digit', minute: '2-digit', weekday: 'short', month: 'short', day: '2-digit' });
      }

      function fmtCHF(x) {
        if (typeof x !== 'number') x = Number(x);
        if (!isFinite(x)) return String(x);
        // Show up to 4 decimals to preserve precision of tariff values
        return x.toFixed(4);
      }

      fetch('compute_costs.cgi', { cache: 'no-store' })
        .then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)))
        .then(data => {
          const now = Date.now();
          const items = (data && Array.isArray(data.hourly) ? data.hourly : [])
            .map(h => Object.assign({ _t: Date.parse(h.hour_start) }, h))
            .filter(h => !isNaN(h._t) && h._t >= now)
            .sort((a,b) => a._t - b._t)
            .slice(0, 12);

          if (items.length === 0) {
            note.textContent = 'No hourly data available. Click “Fetch now” first.';
            return;
          }

          // Build rows
          tbody.innerHTML = '';
          for (const h of items) {
            const tr = document.createElement('tr');
            const tdTime = document.createElement('td');
            const tdCost = document.createElement('td');
            tdTime.textContent = fmtTime(h.hour_start);
            tdCost.textContent = fmtCHF(h.avg_total_chf);
            tdCost.className = 'cost';
            tr.appendChild(tdTime);
            tr.appendChild(tdCost);
            tbody.appendChild(tr);
          }

          note.style.display = 'none';
          table.style.display = '';
        })
        .catch(err => {
          note.textContent = 'Failed to load computed costs: ' + err.message;
        });
    })();
  </script>
</body>
</html>
HTML

exit 0;
