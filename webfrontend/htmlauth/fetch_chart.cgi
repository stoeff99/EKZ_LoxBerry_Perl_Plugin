#!/usr/bin/perl
use strict;
use warnings;

use CGI::Carp qw(fatalsToBrowser warningsToBrowser);
use CGI;
use FindBin;
require "$FindBin::Bin/common.pl";  # SDK globals like $lbpurl

# SDK globals
our ($lbpurl);

my $q = CGI->new;
print $q->header('text/html; charset=utf-8');

# Prefer LoxBerry plugin base URL for assets/links
my $BASEURL = $lbpurl // do {
  my $p = $ENV{SCRIPT_NAME} // '';
  $p =~ s{/[^/]+$}{};
  $p || '.'
};

# Conditional assets to avoid 404s
my $HAS_STYLE  = -f "$FindBin::Bin/style.css" ? 1 : 0;
my $HAS_BANNER = -f "$FindBin::Bin/Icons/banner.jpg" ? 1 : 0;

my $ICON_BASE  = "$BASEURL/Icons";

# HTML head and body up to the <script> tag
print <<"HTML_HEAD";
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>EKZ – Fetch & Chart</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
HTML_HEAD

# Include banner preload only if present
print qq{  <link rel="preload" as="image" href="$ICON_BASE/banner.jpg">\n} if $HAS_BANNER;

# Include style.css only if present; otherwise rely on inline styles below
print qq{  <link rel="stylesheet" href="$BASEURL/style.css">\n} if $HAS_STYLE;

print <<"HTML_HEAD_CONTD";
  <style>
    /* Minimal inline styles to avoid external CSS dependency */
    body { background:#0b1220; color:#e5e7eb; margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,sans-serif; }
    .app-header { padding: 10px 16px; border-bottom: 1px solid rgba(255,255,255,.08); }
    .title { font-size: 1.25rem; font-weight: 600; }
    .nav-actions { display:flex; gap:.5rem; padding: 10px 16px; border-bottom: 1px solid rgba(255,255,255,.05); }
    .btn { padding:.45rem .8rem; border-radius:8px; border:1px solid rgba(255,255,255,.18); background:#0f172a; color:#e5e7eb; cursor:pointer; text-decoration:none; display:inline-block }
    .btn:hover { background:#111827 }
    .container { padding: 16px; }
    .card { background:#0f172a; border:1px solid rgba(255,255,255,.12); border-radius:10px; padding:16px; }
    .controls { display:flex; gap:.75rem; align-items:center; flex-wrap:wrap; margin-bottom:.5rem;}
    .legend { display:flex; gap:16px; align-items:center; flex-wrap:wrap; margin-top:.5rem; color:#cbd5e1 }
    .dot { width:11px; height:11px; border-radius:50%; display:inline-block; margin-right:6px; vertical-align:middle }
    .muted { color:#94a3b8 }
    .status { margin:.4rem 0 .3rem 0; font-size:.9rem }
    #chartwrap { position: relative; width: 100%; min-height: 340px; }
    canvas { max-height: 520px; }
    .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); border:0 }
  </style>
  <!-- Load Chart.js from CDN; local fallback removed to avoid 404 -->
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
</head>
<body id="ekz-plugin" class="plugincontent">
  <div class="app-header">
    <div class="banner">
      <div class="title">EKZ – Preise (berechnet)</div>
    </div>
  </div>

  <div class="nav-actions">
    <a class="btn btn-green"   href="run_rolling_fetch.cgi"><span class="emoji">⚡</span> Fetch now</a>
    <a class="btn btn-primary" href="index.cgi"><span class="emoji">🏠</span> Home</a>
    <a class="btn btn-primary" href="settings.cgi"><span class="emoji">⚙️</span> Settings</a>
  </div>

  <div class="container">
    <div class="card">
      <div class="controls">
        <button id="btnCompute" class="btn"><span class="emoji">📊</span> Compute and draw (no fetch)</button>
        <label for="view" class="sr-only">View</label>
        <select id="view">
          <option value="intervals" selected>15‑minute intervals (total)</option>
          <option value="hourly">Hourly average (total)</option>
        </select>
        <span class="muted small">Bars colored by relative level (very low → very high)</span>
      </div>
      <div id="status" class="status muted">This uses the latest saved JSON (no EKZ fetch), computes for UI, then renders.</div>
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
HTML_HEAD_CONTD

# Emit JS with a single-quoted heredoc
print <<'JAVASCRIPT';
(() => {
  const $ = (sel) => document.querySelector(sel);
  const status   = $('#status');
  const viewSel  = $('#view');
  const btnCompute = $('#btnCompute');
  const ctx      = document.getElementById('priceChart').getContext('2d');

  let chart;
  let lastReport = null;

  function setStatus(msg, isError=false) {
    status.textContent = msg;
    status.style.color = isError ? '#ef4444' : '#94a3b8';
  }

  async function ensureChartLib() {
    if (window.Chart) return;
    // CDN already loaded in <head>; if it failed, show a clear error
    throw new Error('Chart.js not available');
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
    const q25 = q(0.25), q50 = q(0.50), q75 = q(0.75);
    return (v) => v <= q25 ? '#16a34a' : v <= q50 ? '#4ade80' : v <= q75 ? '#fbbf24' : '#ef4444';
  }

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

  async function renderChart(mode) {
    await ensureChartLib();

    const unit = mode === 'hourly' ? 'hour' : 'minute';
    const points = pointsFromReport(mode);
    const colorFn = colorByQuantiles(points.map(p => p.y));

    // Destroy existing chart if present
    const existing = (window.Chart && Chart.getChart) ? Chart.getChart(document.getElementById('priceChart')) : null;
    if (existing) existing.destroy();

    // Adapter-less mode: use category labels
    const hourly = mode === 'hourly';
    const labels = points.map(p => fmtLabel(p.x, hourly));
    const values = points.map(p => p.y);

    const data = {
      labels,
      datasets: [{
        type: 'bar',
        label: 'Total',
        data: values,
        backgroundColor: values.map(v => colorFn(v)),
        borderRadius: 4,
      }]
    };
    const options = {
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

    chart = new Chart(ctx, { data, options });
  }

  // Compute only (no backend fetch); uses latest tariffs_latest.json
  async function computeAndDrawLatest() {
    setStatus('Computing costs from latest file…');
    const r = await fetch('compute_costs.cgi?nopublish=1', { cache: 'no-store' });
    if (!r.ok) throw new Error('compute_costs failed: HTTP ' + r.status);
    const text = await r.text();
    if (!text || text.trim() === '') throw new Error('Empty JSON from compute_costs.cgi');
    try {
      lastReport = JSON.parse(text);
    } catch (e) {
      console.error('Bad JSON:', text);
      throw e;
    }

    if (lastReport && lastReport.error) {
      throw new Error('compute_costs error: ' + (lastReport.message || lastReport.error));
    }

    setStatus('Rendering…');
    await renderChart(viewSel.value);

    const ic = (lastReport && lastReport.interval_count_output) || 0;
    const hc = (lastReport && lastReport.hour_count_output) || 0;
    setStatus(`Ready. Intervals: ${ic}, hours: ${hc}.`);
  }

  btnCompute.addEventListener('click', async (e) => {
    e.preventDefault();
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      // IMPORTANT: do NOT call run_rolling_fetch.cgi here
      await computeAndDrawLatest();
    } catch (err) {
      setStatus(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  viewSel.addEventListener('change', () => renderChart(viewSel.value));

  // Initial load: compute only (no fetch)
  (async () => {
    try {
      setStatus('Loading computed costs…');
      await computeAndDrawLatest();
    } catch (err) {
      setStatus(err.message, true);
    }
  })();
})();
JAVASCRIPT

# Tail HTML
print <<"HTML_TAIL";
  </script>
</body>
</html>
HTML_TAIL
