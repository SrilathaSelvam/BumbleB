/**
 * INSIGHT CHART ENGINE
 * High-performance, zero-dependency, projector-ready HTML5 Canvas time-series chart.
 * Tailored for real-time log anomaly detection:
 * - Dynamic baseline EWMA band with upper guard limit
 * - Segment-aware multi-color line rendering (normal vs anomaly severity)
 * - Soft gradient fill under curve
 * - Vertical dashed anomaly & recovery markers (incident spans)
 * - Interactive hover crosshair & high-DPI crisp rendering
 */

(function (global) {
  'use strict';

  class InsightChart {
    constructor(canvas, options = {}) {
      if (!canvas) throw new Error("InsightChart: Canvas element is required");
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d');
      this.options = Object.assign({
        maxBufferDurationSec: 900, // 15 minutes of history retained
        visibleWindowSec: 300,      // Default 5 minutes (300s)
        padding: { top: 24, right: 30, bottom: 30, left: 54 },
        lineWidth: 2,
        colors: {
          line: '#a5b4fc',
          lineFillTop: 'rgba(165, 180, 252, 0.22)',
          lineFillBottom: 'rgba(165, 180, 252, 0.00)',
          bandFill: 'rgba(139, 92, 246, 0.14)',
          bandBorder: 'rgba(139, 92, 246, 0.35)',
          grid: 'rgba(255, 255, 255, 0.05)',
          text: '#9a9aa6',
          dot: '#ffffff',
          warning: '#f5b83d',
          high: '#fb923c',
          critical: '#f43f5e',
          recovery: '#71717a'
        }
      }, options);

      // Data store
      this.dataPoints = [];       // Array of { ts: epochMs, error_rate: float, baseline_mean: float|null, baseline_upper: float|null, warming_up: bool }
      this.anomalyMarkers = [];   // Array of { id: string, ts: epochMs, severity: string, error_rate: float, z_score: float }
      this.recoveryMarkers = [];  // Array of { ts: epochMs, text: string }

      // State
      this.visibleWindowSec = this.options.visibleWindowSec;
      this.hoveredPoint = null;
      this.hoveredMarker = null;
      this.mouseX = null;
      this.mouseY = null;
      this.dpr = window.devicePixelRatio || 1;

      // Event listener references
      this._onResize = this._handleResize.bind(this);
      this._onMouseMove = this._handleMouseMove.bind(this);
      this._onMouseLeave = this._handleMouseLeave.bind(this);

      this._init();
    }

    _init() {
      window.addEventListener('resize', this._onResize);
      this.canvas.addEventListener('mousemove', this._onMouseMove);
      this.canvas.addEventListener('mouseleave', this._onMouseLeave);
      this._handleResize();
    }

    destroy() {
      window.removeEventListener('resize', this._onResize);
      this.canvas.removeEventListener('mousemove', this._onMouseMove);
      this.canvas.removeEventListener('mouseleave', this._onMouseLeave);
    }

    _handleResize() {
      const rect = this.canvas.parentElement.getBoundingClientRect();
      const width = rect.width;
      const height = rect.height;

      this.dpr = window.devicePixelRatio || 1;
      this.canvas.width = Math.floor(width * this.dpr);
      this.canvas.height = Math.floor(height * this.dpr);
      this.canvas.style.width = width + 'px';
      this.canvas.style.height = height + 'px';

      this.width = width;
      this.height = height;

      this.render();
    }

    setVisibleWindow(seconds) {
      this.visibleWindowSec = seconds;
      this.render();
    }

    /**
     * Add a real-time metric point to the rolling buffer.
     */
    addMetric(point) {
      // Defensive timestamp parsing
      let ts = point.ts;
      if (typeof ts === 'string') {
        ts = Date.parse(ts);
      } else if (typeof ts === 'number') {
        if (ts < 1e11) ts *= 1000;
      }
      if (!ts || isNaN(ts)) ts = Date.now();

      const normalized = {
        ts: ts,
        error_rate: typeof point.error_rate === 'number' ? point.error_rate : 0,
        baseline_mean: typeof point.baseline_mean === 'number' ? point.baseline_mean : null,
        baseline_upper: typeof point.baseline_upper === 'number' ? point.baseline_upper : null,
        warming_up: Boolean(point.warming_up)
      };

      // Detect recovery transition: was the previous point above upper bound, and is this one back inside?
      if (this.dataPoints.length > 0) {
        const prev = this.dataPoints[this.dataPoints.length - 1];
        if (prev.baseline_upper !== null && normalized.baseline_upper !== null) {
          if (prev.error_rate > prev.baseline_upper && normalized.error_rate <= normalized.baseline_upper) {
            this.recoveryMarkers.push({
              ts: normalized.ts,
              text: 'Recovered'
            });
          }
        }
      }

      this.dataPoints.push(normalized);

      // Prune buffer older than maxBufferDurationSec (15 minutes)
      const cutoff = Date.now() - (this.options.maxBufferDurationSec * 1000);
      while (this.dataPoints.length > 0 && this.dataPoints[0].ts < cutoff) {
        this.dataPoints.shift();
      }
      while (this.recoveryMarkers.length > 0 && this.recoveryMarkers[0].ts < cutoff) {
        this.recoveryMarkers.shift();
      }
      while (this.anomalyMarkers.length > 0 && this.anomalyMarkers[0].ts < cutoff) {
        this.anomalyMarkers.shift();
      }

      this.render();
    }

    /**
     * Register an anomaly alert marker.
     */
    addAnomaly(alert) {
      let ts = alert.ts;
      if (typeof ts === 'string') ts = Date.parse(ts);
      else if (typeof ts === 'number' && ts < 1e11) ts *= 1000;
      if (!ts || isNaN(ts)) ts = Date.now();

      // Avoid exact duplicate timestamp markers for same alert id
      if (!this.anomalyMarkers.some(m => m.id === alert.id)) {
        this.anomalyMarkers.push({
          id: alert.id,
          ts: ts,
          severity: alert.severity || 'WARNING',
          error_rate: alert.error_rate,
          z_score: alert.z_score
        });
      }
      this.render();
    }

    clear() {
      this.dataPoints = [];
      this.anomalyMarkers = [];
      this.recoveryMarkers = [];
      this.render();
    }

    _handleMouseMove(evt) {
      const rect = this.canvas.getBoundingClientRect();
      this.mouseX = evt.clientX - rect.left;
      this.mouseY = evt.clientY - rect.top;
      this.render();
    }

    _handleMouseLeave() {
      this.mouseX = null;
      this.mouseY = null;
      this.render();
      if (typeof this.options.onTooltipUpdate === 'function') {
        this.options.onTooltipUpdate(null);
      }
    }

    /**
     * Main canvas rendering pass.
     */
    render() {
      if (!this.ctx || !this.width || !this.height) return;

      const ctx = this.ctx;
      const dpr = this.dpr;
      const pad = this.options.padding;
      const plotW = this.width - pad.left - pad.right;
      const plotH = this.height - pad.top - pad.bottom;

      ctx.save();
      ctx.scale(dpr, dpr);
      ctx.clearRect(0, 0, this.width, this.height);

      // Determine active time window
      const now = this.dataPoints.length > 0
        ? this.dataPoints[this.dataPoints.length - 1].ts
        : Date.now();
      const minTs = now - (this.visibleWindowSec * 1000);
      const maxTs = now;

      // Filter points in active window + 1 preceding point for smooth edge entry
      const visiblePoints = [];
      for (let i = 0; i < this.dataPoints.length; i++) {
        const pt = this.dataPoints[i];
        if (pt.ts >= minTs || (i < this.dataPoints.length - 1 && this.dataPoints[i + 1].ts >= minTs)) {
          visiblePoints.push(pt);
        }
      }

      // Compute Y-scale (auto-scale in percentage)
      let maxRate = 0.06; // At least 6% scale for clean low-rate headroom
      for (const p of visiblePoints) {
        if (p.error_rate > maxRate) maxRate = p.error_rate;
        if (p.baseline_upper !== null && p.baseline_upper > maxRate) maxRate = p.baseline_upper;
      }
      // Add 25% headroom and cap at 100% (1.0)
      maxRate = Math.min(1.0, maxRate * 1.25);
      // Round maxRate up to nearest 5% or 10%
      if (maxRate <= 0.1) maxRate = Math.max(0.08, Math.ceil(maxRate * 100) / 100);
      else maxRate = Math.ceil(maxRate * 20) / 20;

      // Coordinate transformers
      const getX = (ts) => pad.left + ((ts - minTs) / (maxTs - minTs)) * plotW;
      const getY = (val) => pad.top + plotH - (val / maxRate) * plotH;

      // 1. Draw Gridlines & Y-Axis Labels
      this._drawAxes(ctx, pad, plotW, plotH, minTs, maxTs, maxRate);

      if (visiblePoints.length < 2) {
        ctx.restore();
        return;
      }

      // 2. Draw Baseline EWMA Guard Band (mean to upper limit)
      this._drawBaselineBand(ctx, visiblePoints, getX, getY, pad, plotH);

      // 3. Draw Soft Gradient Area Fill under the line
      this._drawAreaFill(ctx, visiblePoints, getX, getY, pad, plotH);

      // 4. Draw Main Error Rate Line (segmented by severity when breaching band)
      this._drawLine(ctx, visiblePoints, getX, getY);

      // 5. Draw Recovery Markers
      this._drawRecoveryMarkers(ctx, minTs, maxTs, getX, pad, plotH);

      // 6. Draw Anomaly Vertical Dashed Markers & Dots
      this._drawAnomalyMarkers(ctx, minTs, maxTs, getX, getY, pad, plotH);

      // 7. Draw Latest Point Glowing White Dot
      const latest = visiblePoints[visiblePoints.length - 1];
      const latestX = getX(latest.ts);
      const latestY = getY(latest.error_rate);

      ctx.beginPath();
      ctx.arc(latestX, latestY, 5, 0, Math.PI * 2);
      ctx.fillStyle = '#ffffff';
      ctx.shadowColor = 'rgba(255, 255, 255, 0.8)';
      ctx.shadowBlur = 8;
      ctx.fill();
      ctx.shadowBlur = 0; // reset shadow

      // 8. Handle Hover Crosshair and Tooltip
      this._handleHoverInteraction(ctx, visiblePoints, minTs, maxTs, getX, getY, pad, plotW, plotH);

      ctx.restore();
    }

    _drawAxes(ctx, pad, plotW, plotH, minTs, maxTs, maxRate) {
      ctx.strokeStyle = this.options.colors.grid;
      ctx.lineWidth = 1;
      ctx.fillStyle = this.options.colors.text;
      ctx.font = '11px ui-monospace, SFMono-Regular, monospace';
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';

      // 4 horizontal gridlines
      const yTicks = 4;
      for (let i = 0; i <= yTicks; i++) {
        const val = (maxRate / yTicks) * i;
        const y = pad.top + plotH - (i / yTicks) * plotH;

        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(pad.left + plotW, y);
        ctx.stroke();

        const pctLabel = (val * 100).toFixed(val < 0.05 ? 1 : 0) + '%';
        ctx.fillText(pctLabel, pad.left - 8, y);
      }

      // X-axis time ticks (5 evenly spaced)
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      const xTicks = 5;
      for (let i = 0; i <= xTicks; i++) {
        const tickTs = minTs + (i / xTicks) * (maxTs - minTs);
        const x = pad.left + (i / xTicks) * plotW;
        const d = new Date(tickTs);
        const timeStr = d.toTimeString().split(' ')[0]; // HH:MM:SS

        ctx.fillText(timeStr, x, pad.top + plotH + 8);
      }
    }

    _drawBaselineBand(ctx, points, getX, getY, pad, plotH) {
      // Find segments where baseline is non-null
      const bandPoints = points.filter(p => p.baseline_mean !== null && p.baseline_upper !== null);
      if (bandPoints.length < 2) return;

      ctx.save();
      // Translucent shaded region between mean and upper
      ctx.beginPath();
      // Upper curve left to right
      ctx.moveTo(getX(bandPoints[0].ts), getY(bandPoints[0].baseline_upper));
      for (let i = 1; i < bandPoints.length; i++) {
        ctx.lineTo(getX(bandPoints[i].ts), getY(bandPoints[i].baseline_upper));
      }
      // Mean curve right to left
      for (let i = bandPoints.length - 1; i >= 0; i--) {
        ctx.lineTo(getX(bandPoints[i].ts), getY(bandPoints[i].baseline_mean));
      }
      ctx.closePath();
      ctx.fillStyle = this.options.colors.bandFill;
      ctx.fill();

      // Dashed upper boundary line
      ctx.beginPath();
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = this.options.colors.bandBorder;
      ctx.lineWidth = 1;
      for (let i = 0; i < bandPoints.length; i++) {
        const x = getX(bandPoints[i].ts);
        const y = getY(bandPoints[i].baseline_upper);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();
    }

    _drawAreaFill(ctx, points, getX, getY, pad, plotH) {
      if (points.length < 2) return;

      const gradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
      gradient.addColorStop(0, this.options.colors.lineFillTop);
      gradient.addColorStop(1, this.options.colors.lineFillBottom);

      ctx.beginPath();
      ctx.moveTo(getX(points[0].ts), pad.top + plotH);
      for (const p of points) {
        ctx.lineTo(getX(p.ts), getY(p.error_rate));
      }
      ctx.lineTo(getX(points[points.length - 1].ts), pad.top + plotH);
      ctx.closePath();
      ctx.fillStyle = gradient;
      ctx.fill();
    }

    _drawLine(ctx, points, getX, getY) {
      if (points.length < 2) return;

      ctx.lineWidth = this.options.lineWidth;
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';

      // Draw segment by segment to color-code portions that breach the baseline upper limit
      for (let i = 0; i < points.length - 1; i++) {
        const p1 = points[i];
        const p2 = points[i + 1];

        const x1 = getX(p1.ts);
        const y1 = getY(p1.error_rate);
        const x2 = getX(p2.ts);
        const y2 = getY(p2.error_rate);

        const isExceeded = p2.baseline_upper !== null && p2.error_rate > p2.baseline_upper;

        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);

        if (isExceeded) {
          // Color based on rate magnitude
          if (p2.error_rate >= 0.12) ctx.strokeStyle = this.options.colors.critical;
          else if (p2.error_rate >= 0.07) ctx.strokeStyle = this.options.colors.high;
          else ctx.strokeStyle = this.options.colors.warning;
        } else {
          ctx.strokeStyle = this.options.colors.line;
        }
        ctx.stroke();
      }
    }

    _drawRecoveryMarkers(ctx, minTs, maxTs, getX, pad, plotH) {
      const visibleRecoveries = this.recoveryMarkers.filter(m => m.ts >= minTs && m.ts <= maxTs);
      if (visibleRecoveries.length === 0) return;

      ctx.save();
      ctx.setLineDash([3, 4]);
      ctx.lineWidth = 1;
      ctx.strokeStyle = this.options.colors.recovery;
      ctx.fillStyle = this.options.colors.recovery;
      ctx.font = '10px sans-serif';

      for (const rec of visibleRecoveries) {
        const x = getX(rec.ts);
        ctx.beginPath();
        ctx.moveTo(x, pad.top);
        ctx.lineTo(x, pad.top + plotH);
        ctx.stroke();

        ctx.fillText('✓ Recovered', x + 4, pad.top + 14);
      }
      ctx.restore();
    }

    _drawAnomalyMarkers(ctx, minTs, maxTs, getX, getY, pad, plotH) {
      const visibleAlerts = this.anomalyMarkers.filter(m => m.ts >= minTs && m.ts <= maxTs);
      if (visibleAlerts.length === 0) return;

      ctx.save();
      ctx.font = '11px sans-serif';
      ctx.textAlign = 'center';

      for (const alert of visibleAlerts) {
        const x = getX(alert.ts);
        let color = this.options.colors.warning;
        let icon = '▲';
        if (alert.severity === 'HIGH') {
          color = this.options.colors.high;
          icon = '⯄';
        } else if (alert.severity === 'CRITICAL') {
          color = this.options.colors.critical;
          icon = '●';
        }

        // Dashed vertical alert line
        ctx.beginPath();
        ctx.setLineDash([4, 4]);
        ctx.lineWidth = 1.5;
        ctx.strokeStyle = color;
        ctx.moveTo(x, pad.top);
        ctx.lineTo(x, pad.top + plotH);
        ctx.stroke();

        // Marker pill dot at top
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.arc(x, pad.top + 6, 6, 0, Math.PI * 2);
        ctx.fillStyle = color;
        ctx.fill();

        // Dot on actual error line if available
        if (typeof alert.error_rate === 'number') {
          const y = getY(alert.error_rate);
          ctx.beginPath();
          ctx.arc(x, y, 4.5, 0, Math.PI * 2);
          ctx.fillStyle = color;
          ctx.strokeStyle = '#ffffff';
          ctx.lineWidth = 1.5;
          ctx.stroke();
          ctx.fill();
        }
      }
      ctx.restore();
    }

    _handleHoverInteraction(ctx, points, minTs, maxTs, getX, getY, pad, plotW, plotH) {
      if (this.mouseX === null || this.mouseY === null) return;
      if (this.mouseX < pad.left || this.mouseX > pad.left + plotW) return;

      // Find closest data point along X
      const hoverTs = minTs + ((this.mouseX - pad.left) / plotW) * (maxTs - minTs);
      let closestPoint = null;
      let minDiff = Infinity;

      for (const p of points) {
        const diff = Math.abs(p.ts - hoverTs);
        if (diff < minDiff) {
          minDiff = diff;
          closestPoint = p;
        }
      }

      if (!closestPoint) return;

      const ptX = getX(closestPoint.ts);
      const ptY = getY(closestPoint.error_rate);

      // Draw crosshair line
      ctx.save();
      ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);

      ctx.beginPath();
      ctx.moveTo(ptX, pad.top);
      ctx.lineTo(ptX, pad.top + plotH);
      ctx.stroke();

      // Highlight target point
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(ptX, ptY, 4, 0, Math.PI * 2);
      ctx.fillStyle = this.options.colors.line;
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.fill();
      ctx.restore();

      // Check if hovering near an anomaly marker (within 14px)
      let nearbyAnomaly = null;
      for (const a of this.anomalyMarkers) {
        if (Math.abs(getX(a.ts) - ptX) <= 14) {
          nearbyAnomaly = a;
          break;
        }
      }

      if (typeof this.options.onTooltipUpdate === 'function') {
        this.options.onTooltipUpdate({
          point: closestPoint,
          anomaly: nearbyAnomaly,
          x: ptX,
          y: ptY
        });
      }
    }
  }

  // Export to global scope
  global.InsightChart = InsightChart;

})(typeof window !== 'undefined' ? window : this);
