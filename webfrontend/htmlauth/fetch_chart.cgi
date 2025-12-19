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
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
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
      <div id="status" class="status muted">Press “Fetch now and draw”. This fetches, publishes (via backend), computes for UI, then renders.</div>
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
  (() => {
    const $ = (sel) => document.querySelector(sel);
    const status = $('#status');
    const viewSel = $('#view');
    const btnFetch = $('#btnFetch');
    const ctx = document.getElementById('priceChart').getContext('2d');

    let chart;
    let lastReport = null;

    function setStatus(msg, isError=false) {
      status.textContent = msg;
      status.style.color = isError ? '#ef4444' : '#94a3b8';
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

    function ensureChart() {
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
      const c = ensureChart();
      c.data = ds;
      c.update();
    }

    async function fetchBackendAndCompute() {
      setStatus('Fetching (backend)…');
      // 1) Backend fetch which also publishes MQTT via compute_costs on the server side
      const r1 = await fetch('run_rolling_fetch.cgi', { cache: 'no-store' });
      if (!r1.ok) throw new Error('Fetch backend failed: HTTP ' + r1.status);

      // 2) Compute for UI only (no MQTT publish)
      setStatus('Computing costs for UI…');
      const r2 = await fetch('compute_costs.cgi?nopublish=1', { cache: 'no-store' });
      if (!r2.ok) throw new Error('compute_costs failed: HTTP ' + r2.status);
      lastReport = await r2.json();

      setStatus('Rendering…');
      draw(document.getElementById('view').value);

      const ic = (lastReport && lastReport.interval_count_output) || 0;
      const hc = (lastReport && lastReport.hour_count_output) || 0;
      setStatus(\`Ready. Intervals: \${ic}, hours: \${hc}.\`);
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
  </script>
</body>
</html>
HTML
