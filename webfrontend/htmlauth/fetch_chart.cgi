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
print <<'JS_PATCH';
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

    // IMPORTANT: destroy any existing chart on this canvas to avoid
    // "Canvas is already in use. Chart with ID 'X' must be destroyed..."
    const existing = (window.Chart && Chart.getChart) ? Chart.getChart(ctx.canvas) : null;
    if (existing) {
      existing.destroy();
    }

    chart = new Chart(ctx, { data, options });
  }
JS_PATCH

# Close HTML
print <<"HTML";
  </script>
</body>
</html>
HTML
