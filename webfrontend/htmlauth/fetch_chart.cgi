#!/usr/bin/perl
use strict;
use warnings;

use CGI::Carp qw(fatalsToBrowser warningsToBrowser);
use CGI;

my $q = CGI->new;
print $q->header('text/html; charset=utf-8');

# Derive base URL from this script path
my $BASEURL = do {
  my $p = $ENV{SCRIPT_NAME} // '';
  $p =~ s{/[^/]+$}{};
  $p || '.'
};
my $ASSET_BASE = "$BASEURL/assets";
my $ICON_BASE  = "$BASEURL/Icons";

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
HTML

# JavaScript printed with SINGLE-QUOTED heredoc to avoid Perl interpolation of ${...}
print <<'JS';
(() => {
  const $ = (sel) => document.querySelector(sel);
  const viewSel = $('#view');
  const ctx = document.getElementById('priceChart').getContext('2d');

  let chart;
  let lastReport = null;

  // Loader helpers (CDN first, local fallback under BASEURL/assets)
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
    if (!window.Chart) throw new Error('Chart.js not available');
  }
  async function ensureTimeAdapter() {
    if (window.Chart && Chart._adapters && Chart._adapters._date) return;
    try { await loadScript('https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3'); } catch {}
    if (window.Chart && Chart._adapters && Chart._adapters._date) return;
    const local = (typeof BASEURL === 'string' ? BASEURL : '.') + '/assets/chartjs-adapter-date-fns.bundle.min.js';
    try { await loadScript(local); } catch {}
  }

  // Color scale by quantiles
  function colorByQuantiles(values) {
    const vs = [...values].filter(v => Number.isFinite(v)).sort((a,b)=>a-b);
    const q = (p) => {
      if (vs.length === 0) return 0;
      const idx = (vs.length-1) * p;
      const lo = Math.floor(idx), hi = Math.ceil(idx);
      if (lo === hi) return vs[lo];
      return vs[lo] + (vs[hi]-vs[lo])*(idx-lo);
    };
    const q25 = q(0.25), q50 = q(0.50), q75 = q(0.75);
    return (v) => v <= q25 ? '#16a34a' : v <= q50 ? '#4ade80' : v <= q75 ? '#fbbf24' : '#ef4444';
  }

  // Build points [{x: ISO, y: number}] for mode
  function pointsFromReport(mode) {
    if (!lastReport) return [];
    if (mode === 'hourly') {
      const rows = Array.isArray(lastReport.hourly) ? lastReport.hourly : [];
      return rows.map(r => ({ x: r.hour_start, y: Number(r.avg_total_chf || 0) }));
    } else {
      const rows = Array.isArray(lastReport.intervals) ? lastReport.intervals : [];
      return rows.map(r => ({ x: r.start_timestamp, y: Number(r.total_chf || 0) }));
    }
  }

  // Create/update chart with true time axis
  async function renderChart(mode) {
    await ensureChartLib();
    await ensureTimeAdapter();

    const unit = mode === 'hourly' ? 'hour' : 'minute';
    const points = pointsFromReport(mode);
    const colorFn = colorByQuantiles(points.map(p => p.y));

    const data = {
      datasets: [{
        type: 'bar',
        label: 'Total',
        data: points,        // each point: {x: ISO timestamp, y: value}
        parsing: false,      // we already provide x/y
        backgroundColor: points.map(p => colorFn(p.y)),
        borderRadius: 4,
      }]
    };

    const options = {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        x: {
          type: 'time',
          time: {
            unit,
            displayFormats: { hour: 'HH:mm', minute: 'HH:mm' }
          },
          ticks: {
            source: 'data',
            color: '#cbd5e1',
            maxRotation: 0,
            autoSkip: true
          },
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
        tooltip: {
          callbacks: {
            title: (items) => {
              const ts = items?.[0]?.parsed?.x;
              if (!ts) return '';
              const d = new Date(ts);
              return isNaN(d) ? String(ts) : d.toLocaleString(undefined, { hour: '2-digit', minute: '2-digit', weekday: 'short', month: 'short', day: '2-digit' });
            },
            label: (ctx) => ` Total: ${Number(ctx.parsed.y).toFixed(4)} CHF/kWh`
          }
        }
      }
    };

    if (!chart) {
      chart = new Chart(ctx, { data, options });
    } else {
      chart.data = data;
      chart.options = options;
      chart.update();
    }
  }

  // Expose a helper if you prefer calling from elsewhere
  window.ChartEKZ = {
    async drawWith(report, mode) {
      lastReport = report;
      await renderChart(mode || (viewSel ? viewSel.value : 'intervals'));
    }
  };

  if (viewSel) {
    viewSel.addEventListener('change', () => renderChart(viewSel.value));
  }

  // If you want auto-load on page open (read from disk without fetch):
  // fetch('compute_costs.cgi?nopublish=1').then(r => r.json()).then(j => { lastReport = j; renderChart(viewSel.value); });
})();
JS

# Close HTML
print <<"HTML";
  </script>
</body>
</html>
HTML
