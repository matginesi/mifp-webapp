(() => {
'use strict';
/* ── Charts (Chart.js) ─────────────────────────────────────── */
function parseChartData(raw, fallback) {
  try {
    var parsed = JSON.parse(raw || '');
    return Array.isArray(parsed) ? parsed : fallback;
  } catch (_) {
    return fallback;
  }
}

function showChartFallback(canvas, message) {
  var fallback = document.createElement('div');
  fallback.className = 'chart-fallback';
  fallback.textContent = message;
  canvas.replaceWith(fallback);
}

function initCharts() {
  var canvases = document.querySelectorAll('canvas[data-chart]');
  if (!canvases.length) return;
  if (!window.Chart) {
    canvases.forEach(function (canvas) { showChartFallback(canvas, 'Chart.js non caricato.'); });
    return;
  }

  var palette = ['#a72b31', '#315f9b', '#197149', '#9a5b08', '#68519a', '#64748b', '#a95608', '#277f86'];
  var chartColors = palette;

  canvases.forEach(function (canvas) {
    if (canvas.dataset.chartReady === '1') return;
    var type = canvas.dataset.chart || 'bar';
    var labels = parseChartData(canvas.dataset.labels, []);
    var values = parseChartData(canvas.dataset.values, []);
    var colors = parseChartData(canvas.dataset.colors, null);
    var datasetLabel = canvas.dataset.chartLabel || '';

    if (!labels.length || !values.length) {
      showChartFallback(canvas, 'Nessun dato disponibile.');
      return;
    }

    var bgColors = colors || labels.map(function (_, i) { return chartColors[i % chartColors.length]; });
    var borderColor = type === 'line' ? (colors ? colors[0] : palette[0]) : (type === 'doughnut' ? '#ffffff' : 'transparent');
    var opts = {
      responsive: true,
      maintainAspectRatio: false,
      resizeDelay: 100,
      plugins: {
        legend: {
          position: type === 'doughnut' ? 'right' : 'bottom',
          labels: { boxWidth: 10, padding: 10, font: { size: 11, family: 'Inter' }, color: '#5f6670' },
        },
        tooltip: {
          backgroundColor: 'rgba(24,24,27,.95)',
          titleFont: { size: 12, family: 'Inter' },
          bodyFont: { size: 11, family: 'Inter' },
          cornerRadius: 6,
          padding: 10,
          borderColor: '#343a44',
          borderWidth: 1,
        },
      },
    };

    if (type === 'doughnut') {
      opts.cutout = '62%';
      opts.plugins.legend.position = 'right';
    }
    if (type === 'bar') {
      opts.scales = {
        x: { grid: { display: false }, ticks: { font: { size: 10, family: 'Inter' }, color: '#71717a' } },
        y: { beginAtZero: true, grid: { color: 'rgba(24,27,32,.08)' }, ticks: { precision: 0, font: { size: 10, family: 'Inter' }, color: '#71717a' } },
      };
    }
    if (type === 'line') {
      opts.plugins.legend.display = false;
      opts.scales = {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 8, font: { size: 10, family: 'Inter' }, color: '#7b838e' } },
        y: { beginAtZero: true, grid: { color: 'rgba(24,27,32,.08)' }, ticks: { precision: 0, font: { size: 10, family: 'Inter' }, color: '#7b838e' } },
      };
    }

    try {
      if (window.Chart.getChart(canvas)) window.Chart.getChart(canvas).destroy();
      new Chart(canvas, {
        type: type,
        data: {
          labels: labels,
          datasets: [{
            label: datasetLabel,
            data: values,
            backgroundColor: type === 'line' ? 'rgba(167,43,49,.10)' : bgColors,
            borderColor: borderColor,
            borderWidth: type === 'line' ? 2 : (type === 'doughnut' ? 2 : 0),
            borderRadius: type === 'bar' ? 4 : 0,
            fill: type === 'line',
            tension: type === 'line' ? 0.28 : 0,
            pointRadius: type === 'line' ? 2 : 0,
            pointHoverRadius: type === 'line' ? 4 : 0,
            barPercentage: 0.65,
            categoryPercentage: 0.8,
          }],
        },
        options: opts,
      });
      canvas.dataset.chartReady = '1';
    } catch (err) {
      showChartFallback(canvas, 'Grafico non renderizzato: ' + err.message);
    }
  });
}

document.addEventListener('DOMContentLoaded', initCharts, { once: true });
})();
