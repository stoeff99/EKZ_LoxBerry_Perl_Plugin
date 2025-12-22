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

# HTML shell + styles
print <<"HTML";
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>EKZ – Preise (berechnet)</title>
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

    /* Next 24 hours table */
    .costs-table { width: 100%; border-collapse: collapse; margin-top: 6px; }
    .costs-table th, .costs-table td { padding: 8px 10px; border-bottom: 1px solid rgba(255,255,255,.08); }
    .costs-table th { text-align: left; color: #9fb0c9; font-weight: 700; }
    .costs-table td.cost { text-align: right; font-variant-numeric: tabular-nums; }
  </style>

  <!-- Try to load Chart.js early; JS below also has a fallback to local assets -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <script>
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
    <a class="btn btn-primary" href="@{[ h($BASEURL) ]}/start.cgi"><span class="emoji">🔐</span> Sign in (OIDC)</a>
    <a class="btn btn-green"   href="@{[ h($BASEURL) ]}/run_rolling_fetch.cgi"><span class="emoji">⚡</span> Fetch now</a>
    <a class="btn btn-slate"  href="@{[ h($BASEURL) ]}/health.cgi"><span class="emoji">🩺</span> Health</a>
    <a class="btn btn-primary"   href="@{[ h($BASEURL) ]}/settings.cgi"><span class="emoji">⚙️</span> Settings</a>
  </div>

  <div class="container">
    <!-- Chart card -->
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

    <!-- Next 24 hours table -->
    <div class="card" id="next24h-card">
      <div style="display:flex;align-items:baseline;gap:10px;">
        <h3 style="margin:0;">Next 24 hours</h3>
        <span class="small muted">Hourly average = mean of four 15‑min intervals</span>
      </div>
      <div id="next24h-note" class="small muted" style="margin-top:6px;">Reading computed costs…</div>
      <table class="costs-table" id="next24h-table" style="display:none;">
        <thead>
          <tr>
            <th scope="col">Hour (local)</th>
            <th scope="col" class="cost">Avg total (CHF)</th>
          </tr>
        </thead>
        <tbody id="next24h-body"></tbody>
      </table>
    </div>
  </div>

  <script>
HTML

# Use single-quoted heredoc to avoid Perl interpolation in JS template literals
print <<'JS';
(() => {
  const $ = (sel) => document.querySelector(sel);
  const status = $('#status');
  const viewSel = $('#view');
  const btnFetch = $('#btnFetch');
  const ctx = document.getElementById('priceChart').getContext('2d');

  const next24Note  = $('#next24h-note');
  const next24Table = $('#next24h-table');
  const next24Body  = $('#next24h-body');

  let chart;
  let lastReport = null;

  function setStatus(msg, isError=false) {
    status.textContent = msg;
    status.style.color = isError ? '#ef4444' : '#94a3b8';
  }

  // Loader helpers (CDN-first, local fallback)
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

  // Correct adapter loader (bundle build) + local fallback
  async function ensureTimeAdapter() {
    const hasAdapter = () => !!(window.Chart && Chart._adapters && Chart._adapters._date && Chart._adapters._date.parse);
    if (hasAdapter()) return;
    try {
      await loadScript('https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3/dist/chartjs-adapter-date-fns.bundle.min.js');
    } catch (e) {
      console.warn('CDN adapter failed:', e.message);
    }
    if (hasAdapter()) return;
    const local = (typeof BASEURL === 'string' ? BASEURL : '.') + '/assets/chartjs-adapter-date-fns.bundle.min.js';
    try {
      await loadScript(local);
    } catch (e) {
      console.error('Local adapter failed:', e.message);
    }
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

  function fmtLabel(iso, hourly=false) {
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

  // Create/update chart; uses time axis if adapter is ready, else category fallback
  async function renderChart(mode) {
    await ensureChartLib();
    await ensureTimeAdapter();

    const unit = mode === 'hourly' ? 'hour' : 'minute';
    const points = pointsFromReport(mode);
    const colorFn = colorByQuantiles(points.map(p => p.y));
    const adapterReady = !!(window.Chart && Chart._adapters && Chart._adapters._date && Chart._adapters._date.parse);

    // Destroy any existing chart instance to avoid "canvas in use"
    const existing = (window.Chart && Chart.getChart) ? Chart.getChart(document.getElementById('priceChart')) : null;
    if (existing) existing.destroy();

    let data, options;
    if (adapterReady) {
      // True time axis
      data = {
        datasets: [{
          type: 'bar',
          label: 'Total',
          data: points,      // [{x: ISO, y: number}]
          parsing: false,
          backgroundColor: points.map(p => colorFn(p.y)),
          borderRadius: 4,
        }]
      };
      options = {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            type: 'time',
            time: { unit, displayFormats: { hour: 'HH:mm', minute: 'HH:mm' } },
            ticks: { source: 'data', color: '#cbd5e1', maxRotation: 0, autoSkip: true },
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
                return isNaN(d)
                  ? String(ts)
                  : d.toLocaleString(undefined, { hour: '2-digit', minute: '2-digit', weekday: 'short', month: 'short', day: '2-digit' });
              },
              label: (ctx) => ` Total: ${Number(ctx.parsed.y).toFixed(4)} CHF/kWh`
            }
          }
        }
      };
    } else {
      // Fallback to category axis, with HH:mm labels
      const hourly = mode === 'hourly';
      const labels = points.map(p => fmtLabel(p.x, hourly));
      const values = points.map(p => p.y);

      data = {
        labels,
        datasets: [{
          type: 'bar',
          label: 'Total',
          data: values,
          backgroundColor: values.map(v => colorFn(v)),
          borderRadius: 4,
        }]
      };
      options = {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            type: 'category',
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
          tooltip: {
            callbacks: {
              title: (items) => items?.[0]?.label ?? '',
              label: (ctx) => ` Total: ${Number(ctx.raw).toFixed(4)} CHF/kWh`
            }
          }
        }
      };
    }

    chart = new Chart(ctx, { data, options });
  }

  function fillNext24Hours() {
    if (!lastReport) return;

    const now = Date.now();
    const items = (Array.isArray(lastReport.hourly) ? lastReport.hourly : [])
      .map(h => Object.assign({ _t: Date.parse(h.hour_start) }, h))
      .filter(h => !isNaN(h._t) && h._t >= now)
      .sort((a,b) => a._t - b._t)
      .slice(0, 24);

    if (items.length === 0) {
      next24Note.textContent = 'No hourly data available. Click “Fetch now and draw”.';
      next24Table.style.display = 'none';
      return;
    }

    next24Body.innerHTML = '';
    for (const h of items) {
      const tr = document.createElement('tr');
      const tdTime = document.createElement('td');
      const tdCost = document.createElement('td');
      const d = new Date(h.hour_start);
      tdTime.textContent = isNaN(d)
        ? h.hour_start
        : d.toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit', month: 'short', day: '2-digit' }).replace(',','');
      tdCost.textContent = Number(h.avg_total_chf || 0).toFixed(4);
      tdCost.className = 'cost';
      tr.appendChild(tdTime);
      tr.appendChild(tdCost);
      next24Body.appendChild(tr);
    }
    next24Note.style.display = 'none';
    next24Table.style.display = '';
  }

  async function fetchBackendAndCompute() {
    setStatus('Fetching (backend)…');
    const r1 = await fetch('run_rolling_fetch.cgi', { cache: 'no-store' });
    if (!r1.ok) throw new Error('Fetch backend failed: HTTP ' + r1.status);

    setStatus('Computing costs for UI…');
    const r2 = await fetch('compute_costs.cgi?nopublish=1', { cache: 'no-store' });
    if (!r2.ok) throw new Error('compute_costs failed: HTTP ' + r2.status);
    lastReport = await r2.json();

    if (lastReport && lastReport.error) {
      throw new Error('compute_costs error: ' + (lastReport.message || lastReport.error));
    }

    setStatus('Rendering…');
    await renderChart(document.getElementById('view').value);
    fillNext24Hours();

    const ic = (lastReport && lastReport.interval_count_output) || 0;
    const hc = (lastReport && lastReport.hour_count_output) || 0;
    setStatus(`Ready. Intervals: ${ic}, hours: ${hc}.`);
  }

  btnFetch.addEventListener('click', async (e) => {
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

  viewSel.addEventListener('change', () => renderChart(viewSel.value));

  // Initial load: fetch + compute + render
  (async () => {
    try {
      setStatus('Fetching (backend)…');
      const r1 = await fetch('run_rolling_fetch.cgi', { cache: 'no-store' });
      if (!r1.ok) throw new Error('Fetch backend failed: HTTP ' + r1.status);

      setStatus('Computing costs for UI…');
      const r2 = await fetch('compute_costs.cgi?nopublish=1', { cache: 'no-store' });
      if (!r2.ok) throw new Error('compute_costs failed: HTTP ' + r2.status);
      lastReport = await r2.json();

      if (lastReport && lastReport.error) {
        throw new Error('compute_costs error: ' + (lastReport.message || lastReport.error));
      }

      setStatus('Rendering…');
      await renderChart(document.getElementById('view').value);
      fillNext24Hours();
      setStatus('Ready.');
    } catch (err) {
      setStatus(err.message, true);
    }
  })();
})();
JS

print <<"HTML";
  </script>
</body>
</html>
HTML
