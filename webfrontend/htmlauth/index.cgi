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
my $BASEURL    = $lbpurl || do { (my $p = $ENV{SCRIPT_NAME}//'') =~ s{/[^/]+$}{}r || '.' };
my $ASSET_BASE = "$BASEURL/assets";
my $ICON_BASE  = "$BASEURL/Icons";

my $q = CGI->new;
print $q->header('text/html; charset=utf-8');

# Determine status line from runtime (non-fatal if helpers unavailable)
my $cfg = eval { load_cfg() } // {};
my ($link_status, $link_url, $err) = eval { try_ensure_linked($cfg) } // ('unknown','',undef);
my $signed_in = eval { has_tokens($cfg) } // 0;

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
  <link rel="stylesheet" href="$ASSET_BASE/styles.css?v=20251219">
  <style>
    .costs-table { width: 100%; border-collapse: collapse; margin-top: 6px; }
    .costs-table th, .costs-table td { padding: 8px 10px; border-bottom: 1px solid rgba(255,255,255,.08); }
    .costs-table th { text-align: left; color: #9fb0c9; font-weight: 700; }
    .costs-table td.cost { text-align: right; font-variant-numeric: tabular-nums; }
    .muted { color: #9fb0c9; }
    #chartwrap { position: relative; width: 100%; min-height: 320px; }
    canvas { max-height: 520px; }
    .legend { display:flex; gap:16px; align-items:center; flex-wrap:wrap; margin-top:.5rem; color:#cbd5e1 }
    .dot { width:11px; height:11px; border-radius:50%; display:inline-block; margin-right:6px; vertical-align:middle }
    .controls { display:flex; gap:.75rem; align-items:center; flex-wrap:wrap; margin: 6px 0 8px 0; }
    select { padding:.35rem .6rem; border-radius:8px; border:1px solid rgba(255,255,255,.15); background:#0b1220; color:#e5e7eb }
    .status { margin:.35rem 0; font-size:.9rem; color:#94a3b8 }
  </style>
  <!-- Try CDN first; JS will also have a fallback to local assets -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <script>
    const BASEURL = "$BASEURL";
  </script>
</head>
<body id="ekz-plugin" class="plugincontent">
  <div class="app-header">
    <div class="banner">
      <div class="title">EKZ Dynamic Price</div>
    </div>
  </div>

  <div class="nav-actions">
    <a class="btn btn-primary" href="$BASEURL/start.cgi"><span class="emoji">🔐</span> Sign in (OIDC)</a>
    <a class="btn btn-green"   href="$BASEURL/fetch_chart.cgi"><span class="emoji">⚡</span> Fetch now</a>
    <a class="btn btn-orange"  href="$BASEURL/health.cgi"><span class="emoji">🩺</span> Health</a>
    <a class="btn btn-slate"   href="$BASEURL/settings.cgi"><span class="emoji">⚙️</span> Settings</a>
  </div>

  <h2 class="status-title">Status: $status_line</h2>
  $linking_note

  <div class="container">
    <div class="card">
      <div class="controls">
        <button id="btnFetch" class="btn"><span class="emoji">⚡</span> Fetch now and draw</button>
        <label for="view" class="sr-only">View</label>
        <select id="view">
          <option value="intervals" selected>15‑minute intervals (total)</option>
          <option value="hourly">Hourly average (total)</option>
        </select>
        <span class="muted small">Bars colored by relative level (very low → very high)</span>
      </div>
      <div id="status" class="status muted">Press “Fetch now and draw”. This fetches (backend), computes for UI (no MQTT), then renders.</div>
      <div id="chartwrap">
        <canvas id="priceChart" aria-label="Price chart" role="img"></canvas>
      </div>
      <div class="legend">
        <span><span class="dot" style="background:#16a34a"></span>Sehr niedrig</span>
        <span><span class="dot" style="background:#4ade80"></span>Niedrig</span>
        <span><span class="dot" style="background:#fbbf24"></span>Hoch</span>
        <span><span class="dot" style="background:#ef4444"></span>Sehr hoch</span>
      </div>
    </div>
  </div>

    <!-- Existing: Next 12 hours table -->
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
            <th scope="col" class="cost">Avg total (CHF)</th>
          </tr>
        </thead>
        <tbody id="next12h-body"></tbody>
      </table>
    </div>

    <div class="card">
      <p>“Fetch now” opens a page that fetches new data, publishes computed MQTT (via backend), and shows an interactive chart.</p>
    </div>
  </div>

  <script>

# HTML up to the <script> tag (variables interpolate here)
print <<"HTML";
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>EKZ – Fetch & Chart</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="preload" as="image" href="$ICON_BASE/banner.jpg">
  <link rel="stylesheet" href="$BASEURL/style.css">
  <link rel="stylesheet" href="$ASSET_BASE/styles.css?v=20251219">
  <style>
    #chartwrap { position: relative; width: 100%; min-height: 340px; }
    canvas { max-height: 520px; }
    .controls { display:flex; gap:.75rem; align-items:center; flex-wrap:wrap; margin-bottom:.5rem;}
    .legend { display:flex; gap:16px; align-items:center; flex-wrap:wrap; margin-top:.5rem; color:#cbd5e1 }
    .dot { width:11px; height:11px; border-radius:50%; display:inline-block; margin-right:6px; vertical-align:middle }
    .muted { color:#94a3b8 }
    .btn { padding:.45rem .8rem; border-radius:8px; border:1px solid rgba(255,255,255,.18); background:#0f172a; color:#e5e7eb; cursor:pointer }
    .btn:hover { background:#111827 }
    select { padding:.35rem .6rem; border-radius:8px; border:1px solid rgba(255,255,255,.15); background:#0b1220; color:#e5e7eb }
    .status { margin:.4rem 0 .3rem 0; font-size:.9rem }
    .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); border:0 }
  </style>
  <!-- Try to load Chart.js early; we still have an async fallback in JS -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <script>
    // Provide BASEURL for JS (used for local fallback)
    const BASEURL = "$BASEURL";
  </script>
</head>
<body id="ekz-plugin" class="plugincontent">
  <div class="app-header">
    <div class="banner">
      <div class="title">EKZ – Preise (berechnet)</div>
    </div>
  </div>

  <div class="nav-actions">
    <a class="btn btn-primary" href="$BASEURL/start.cgi"><span class="emoji">🔐</span> Sign in (OIDC)</a>
    <a class="btn btn-slate" href="$BASEURL/index.cgi"><span class="emoji">🏠</span> Overview</a>
    <a class="btn btn-slate" href="$BASEURL/settings.cgi"><span class="emoji">⚙️</span> Settings</a>
  </div>

  <div class="container">
    <div class="card">
      <div class="controls">
        <button id="btnFetch" class="btn"><span class="emoji">⚡</span> Fetch now and draw</button>
        <label for="view" class="sr-only">View</label>
        <select id="view">
          <option value="intervals" selected>15‑minute intervals (total)</option>
          <option value="hourly">Hourly average (total)</option>
        </select>
        <span class="muted small">Bars colored by relative level (very low → very high)</span>
      </div>
      <div id="status" class="status muted">Press “Fetch now and draw”. This fetches (backend), computes for UI (no MQTT), then renders.</div>
      <div id="chartwrap">
        <canvas id="priceChart" aria-label="Price chart" role="img"></canvas>
      </div>
      <div class="legend">
        <span><span class="dot" style="background:#16a34a"></span>Sehr niedrig</span>
        <span><span class="dot" style="background:#4ade80"></span>Niedrig</span>
        <span><span class="dot" style="background:#fbbf24"></span>Hoch</span>
        <span><span class="dot" style="background:#ef4444"></span>Sehr hoch</span>
      </div>
    </div>
  </div>

  <script>

# JavaScript printed with SINGLE-QUOTED heredoc to avoid Perl interpolation of ${...}
print <<'JS';
  (() => {
    const $ = (sel) => document.querySelector(sel);
    const status = $('#status');
    const viewSel = $('#view');
    const btnFetch = $('#btnFetch');
    const canvas = document.getElementById('priceChart');
    const ctx = canvas.getContext('2d');

    let chart;
    let lastReport = null;

    function setStatus(msg, isError=false) {
      status.textContent = msg;
      status.style.color = isError ? '#ef4444' : '#94a3b8';
    }

    // Robust loader for Chart.js with CDN first, local fallback
    function loadScript(src) {
      return new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = src;
        s.async = true;
        s.onload = () => resolve();
        s.onerror = () => reject(new Error('Failed to load ' + src));
        document.head.appendChild(s);
      });
    }

    async function ensureChartLib() {
      if (window.Chart) return;
      try {
        await loadScript('https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js');
      } catch (e) {
        console.warn(e.message);
      }
      if (window.Chart) return;
      // fallback to local copy under assets (put chart.umd.min.js there)
      const local = (typeof BASEURL === 'string' ? BASEURL : '.') + '/assets/chart.umd.min.js';
      try {
        await loadScript(local);
      } catch (e) {
        console.error(e.message);
      }
      if (!window.Chart) {
        throw new Error('Chart.js library not available');
      }
    }

    function colorByQuantiles(values) {
      const vs = [...values].filter(v => Number.isFinite(v)).sort((a,b)=>a-b);
      const q = (p) => {
        if (vs.length === 0) return 0;
        const idx = (vs.length-1) * p;
        const lo = Math.floor(idx), hi = Math.ceil(idx);
        if (lo === hi) return vs[lo];
        return vs[lo] + (vs[hi]-vs[lo])*(idx-lo);
      };
      const q25 = q(0.25);
      const q50 = q(0.50);
      const q75 = q(0.75);
      return (v) => {
        if (v <= q25) return '#16a34a';
        if (v <= q50) return '#4ade80';
        if (v <= q75) return '#fbbf24';
        return '#ef4444';
      };
    }

    function makeDataset(labels, values) {
      const colorFn = colorByQuantiles(values);
      const colors = values.map(v => colorFn(v));
      return {
        labels,
        datasets: [{
          type: 'bar',
          label: 'Total',
          data: values,
          backgroundColor: colors,
          borderRadius: 4,
        }]
      };
    }

    function ensureChartInstance() {
      if (chart) return chart;
      chart = new Chart(ctx, {
        data: { labels: [], datasets: [] },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: { ticks: { color: '#cbd5e1', maxRotation: 0, autoSkip: true },
                 grid: { color: 'rgba(148,163,184,0.15)' } },
            y: { ticks: { color: '#cbd5e1' },
                 grid: { color: 'rgba(148,163,184,0.15)' },
                 title: { display: true, text: 'CHF/kWh', color: '#cbd5e1' } }
          },
          plugins: {
            legend: { display: false },
            tooltip: { callbacks: { label: (ctx) => ` Total: ${Number(ctx.raw).toFixed(4)} CHF/kWh` } }
          }
        }
      });
      return chart;
    }

    function fmtTime(iso, hourly=false) {
      const d = new Date(iso);
      if (isNaN(d)) return iso;
      return d.toLocaleString(undefined, {
        weekday: 'short',
        hour: '2-digit',
        minute: hourly ? undefined : '2-digit',
        month: 'short',
        day: '2-digit'
      }).replace(',','');
    }

    function draw(mode) {
      if (!lastReport) return;
      let labels = [], values = [];

      if (mode === 'hourly') {
        const rows = Array.isArray(lastReport.hourly) ? lastReport.hourly : [];
        labels = rows.map(r => fmtTime(r.hour_start, true));
        values = rows.map(r => Number(r.avg_total_chf || 0));
      } else {
        const rows = Array.isArray(lastReport.intervals) ? lastReport.intervals : [];
        labels = rows.map(r => fmtTime(r.start_timestamp, false));
        values = rows.map(r => Number(r.total_chf || 0));
      }

      const ds = makeDataset(labels, values);
      const c = ensureChartInstance();
      c.data = ds;
      c.update();
    }

    async function fetchBackendAndCompute() {
      setStatus('Fetching (backend)…');
      // 1) Backend fetch
      const r1 = await fetch('run_rolling_fetch.cgi', { cache: 'no-store' });
      if (!r1.ok) throw new Error('Fetch backend failed: HTTP ' + r1.status);

      // 2) Compute for UI only (no MQTT publish)
      setStatus('Computing costs for UI…');
      const r2 = await fetch('compute_costs.cgi?nopublish=1', { cache: 'no-store' });
      if (!r2.ok) throw new Error('compute_costs failed: HTTP ' + r2.status);
      lastReport = await r2.json();

      // 3) Ensure chart library, then render
      await ensureChartLib();

      setStatus('Rendering…');
      draw(document.getElementById('view').value);

      const ic = (lastReport && lastReport.interval_count_output) || 0;
      const hc = (lastReport && lastReport.hour_count_output) || 0;
      setStatus(`Ready. Intervals: ${ic}, hours: ${hc}.`);
    }

    document.getElementById('btnFetch').addEventListener('click', async (e) => {
      e.preventDefault();
      const btn = e.currentTarget;
      btn.disabled = true;
      try {
        await fetchBackendAndCompute();
      } catch (err) {
        setStatus(err.message, true);
      } finally {
        btn.disabled = false;
      }
    });

    document.getElementById('view').addEventListener('change', (e) => {
      draw(e.target.value);
    });
  })();
JS

# Close HTML
print <<"HTML";
  </script>
</body>
</html>
HTML
