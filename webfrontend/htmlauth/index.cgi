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
    <!-- New: Chart on the front page -->
    <div class="card" id="chart-card">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
        <h3 style="margin:0;">Computed prices</h3>
        <div class="controls">
          <select id="view">
            <option value="intervals" selected>15‑minute totals</option>
            <option value="hourly">Hourly average totals</option>
          </select>
          <button id="btnRefresh" class="btn btn-slate">Refresh</button>
        </div>
      </div>
      <div id="chart-status" class="status">Loading computed costs…</div>
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
HTML
# Keep JS in single-quoted heredoc to avoid Perl ${} interpolation
print <<'JS';
(() => {
  const $ = (sel) => document.querySelector(sel);

  // Elements
  const chartStatus = $('#chart-status');
  const viewSel = $('#view');
  const btnRefresh = $('#btnRefresh');
  const ctx = document.getElementById('priceChart').getContext('2d');

  const next12Note = $('#next12h-note');
  const next12Table = $('#next12h-table');
  const next12Body = $('#next12h-body');

  let chart;
  let lastReport = null;

  function setChartStatus(msg, isError=false) {
    chartStatus.textContent = msg;
    chartStatus.style.color = isError ? '#ef4444' : '#94a3b8';
  }

  // Loader helpers
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
    try { await loadScript('https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js'); } catch {}
    if (window.Chart) return;
    const local = (typeof BASEURL === 'string' ? BASEURL : '.') + '/assets/chart.umd.min.js';
    try { await loadScript(local); } catch {}
    if (!window.Chart) throw new Error('Chart.js library not available');
  }

  async function ensureTimeAdapter() {
    // Chart.js time scale needs an adapter, e.g., date-fns adapter
    if (window.Chart && Chart._adapters && Chart._adapters._date) return;
    try {
      await loadScript('https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3');
    } catch {}
    if (window.Chart && Chart._adapters && Chart._adapters._date) return;
    const local = (typeof BASEURL === 'string' ? BASEURL : '.') + '/assets/chartjs-adapter-date-fns.bundle.min.js';
    try { await loadScript(local); } catch {}
    // If still not available, we'll fall back to category scale
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

  function ensureChartInstance() {
    if (chart) return chart;
    chart = new Chart(ctx, {
      data: { labels: [], datasets: [] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { // will be set dynamically to 'time' or 'category'
            ticks: { color: '#cbd5e1', maxRotation: 0, autoSkip: true },
            grid: { color: 'rgba(148,163,184,0.15)' }
          },
          y: {
            ticks: { color: '#cbd5e1' },
            grid: { color: 'rgba(148,163,184,0.15)' },
            title: { display: true, text: 'CHF/kWh', color: '#cbd5e1' }
          }
        },
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => ` Total: ${Number(ctx.raw?.y ?? ctx.raw).toFixed(4)} CHF/kWh` } }
        }
      }
    });
    return chart;
  }

  function buildTimeDataset(points) {
    const colorFn = colorByQuantiles(points.map(p => p.y));
    return {
      datasets: [{
        type: 'bar',
        label: 'Total',
        data: points, // [{x: ISO, y: number}]
        parsing: false, // we already provide x/y
        backgroundColor: points.map(p => colorFn(p.y)),
        borderRadius: 4,
      }]
    };
  }

  function buildCategoryDataset(labels, values) {
    const colorFn = colorByQuantiles(values);
    return {
      labels,
      datasets: [{
        type: 'bar',
        label: 'Total',
        data: values,
        backgroundColor: values.map(v => colorFn(v)),
        borderRadius: 4,
      }]
    };
  }

  function fmtLabel(iso, hourly=false) {
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString(undefined, {
      weekday: 'short', hour: '2-digit', minute: hourly ? undefined : '2-digit',
      month: 'short', day: '2-digit'
    }).replace(',','');
  }

  function draw(mode) {
    if (!lastReport) return;

    const c = ensureChartInstance();

    // Prefer true time scale if adapter is available; else fall back to category labels
    const hasTime = !!(window.Chart && Chart._adapters && Chart._adapters._date);

    if (mode === 'hourly') {
      const rows = Array.isArray(lastReport.hourly) ? lastReport.hourly : [];
      if (hasTime) {
        const points = rows.map(r => ({ x: r.hour_start, y: Number(r.avg_total_chf || 0) }));
        c.config.options.scales.x.type = 'time';
        c.config.options.scales.x.time = { unit: 'hour', displayFormats: { hour: 'HH:mm' } };
        c.data = buildTimeDataset(points);
      } else {
        const labels = rows.map(r => fmtLabel(r.hour_start, true));
        const values = rows.map(r => Number(r.avg_total_chf || 0));
        c.config.options.scales.x.type = 'category';
        c.data = buildCategoryDataset(labels, values);
      }
    } else {
      const rows = Array.isArray(lastReport.intervals) ? lastReport.intervals : [];
      if (hasTime) {
        const points = rows.map(r => ({ x: r.start_timestamp, y: Number(r.total_chf || 0) }));
        c.config.options.scales.x.type = 'time';
        c.config.options.scales.x.time = { unit: 'hour', displayFormats: { hour: 'HH:mm' } };
        c.data = buildTimeDataset(points);
      } else {
        const labels = rows.map(r => fmtLabel(r.start_timestamp, false));
        const values = rows.map(r => Number(r.total_chf || 0));
        c.config.options.scales.x.type = 'category';
        c.data = buildCategoryDataset(labels, values);
      }
    }

    c.update();
  }

  function fillNext12Hours() {
    if (!lastReport) return;
    const now = Date.now();
    const items = (Array.isArray(lastReport.hourly) ? lastReport.hourly : [])
      .map(h => Object.assign({ _t: Date.parse(h.hour_start) }, h))
      .filter(h => !isNaN(h._t) && h._t >= now)
      .sort((a,b) => a._t - b._t)
      .slice(0, 12);

    if (items.length === 0) {
      next12Note.textContent = 'No hourly data available. Click “Fetch now” on the top bar.';
      next12Table.style.display = 'none';
      return;
    }

    next12Body.innerHTML = '';
    for (const h of items) {
      const tr = document.createElement('tr');
      const tdTime = document.createElement('td');
      const tdCost = document.createElement('td');
      const d = new Date(h.hour_start);
      tdTime.textContent = isNaN(d) ? h.hour_start : d.toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit', month: 'short', day: '2-digit' }).replace(',','');
      tdCost.textContent = Number(h.avg_total_chf || 0).toFixed(4);
      tdCost.className = 'cost';
      tr.appendChild(tdTime);
      tr.appendChild(tdCost);
      next12Body.appendChild(tr);
    }
    next12Note.style.display = 'none';
    next12Table.style.display = '';
  }

  async function loadComputed() {
    setChartStatus('Loading computed costs…');
    // UI-only compute (no MQTT publish)
    const r = await fetch('compute_costs.cgi?nopublish=1', { cache: 'no-store' });
    if (!r.ok) throw new Error('compute_costs failed: HTTP ' + r.status);
    lastReport = await r.json();
    await ensureChartLib();
    await ensureTimeAdapter();
    setChartStatus('Rendering…');
    draw(viewSel.value);
    fillNext12Hours();
    setChartStatus('Ready.');
  }

  btnRefresh.addEventListener('click', (e) => {
    e.preventDefault();
    loadComputed().catch(err => setChartStatus(err.message, true));
  });

  viewSel.addEventListener('change', () => draw(viewSel.value));

  // Initial load
  loadComputed().catch(err => setChartStatus(err.message, true));
})();
JS
print <<"HTML";
  </script>
</body>
</html>
HTML
