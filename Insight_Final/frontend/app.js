/**
 * INSIGHT - Real-Time Log Anomaly Detector Frontend
 * Pair-programmed for Person C (SRE Frontend Dashboard)
 * 
 * SECTIONS:
 * 1. CONFIGURATION
 * 2. APPLICATION STATE
 * 3. UTILITIES & FORMATTERS
 * 4. WEBSOCKET CLIENT & RECONNECT LOGIC
 * 5. MESSAGE HANDLERS (metric, alert, alert_update)
 * 6. CHART MODULE & RANGE SELECTOR
 * 7. ALERTS TABLE MODULE (de-dupe, live status updates)
 * 8. INCIDENT DETAILS PANEL (thresholds, factual callout, timeline)
 * 9. DEMO CONTROLS MODULE (fault injection & reset)
 * 10. SYSTEM CONTROLS (theme, audio alerts, freshness watchdog)
 */

(function () {
  'use strict';

  // =========================================================================
  // SECTION 1: CONFIGURATION
  // Single configuration object at top of file.
  // Change only this object to point to the real production backend.
  // =========================================================================
  const CONFIG = {
    apiBaseUrl: 'http://localhost:8000',
    wsUrl: 'ws://localhost:8000/ws',
    scenarios: ['blip', 'ramp', 'sustained'],
    maxAlertHistory: 100,
    warmupDurationSeconds: 45,
    staleDataThresholdSeconds: 3,
    reconnect: {
      initialDelayMs: 1000,
      multiplier: 2,
      maxDelayMs: 10000
    }
  };

  // =========================================================================
  // SECTION 2: APPLICATION STATE
  // =========================================================================
  const state = {
    connected: false,
    reconnecting: false,
    reconnectAttempts: 0,
    lastMetricTime: null,
    staleTimer: null,
    
    // Detector metrics
    warmingUp: true,
    warmupStartedTs: Date.now(),
    currentMetric: null,
    overallStatus: 'Normal', // 'Normal' | 'WARNING' | 'HIGH' | 'CRITICAL'
    consecutiveCalmSeconds: 0,

    // Alerts data
    alerts: [],           // Array of alert objects, sorted newest first
    alertsById: new Map(), // O(1) alert lookup and de-duplication
    selectedAlertId: null,
    unreadAlertsCount: 0,
    lastDismissedAlertId: null,

    // Audio & UI settings
    soundEnabled: false,
    chartRangeSec: 300, // 5m default
    theme: 'dark'
  };

  // DOM Elements cache
  const dom = {
    // Pills & Header
    connectionPill: document.getElementById('connection-pill'),
    connectionLabel: document.getElementById('connection-label'),
    warmupPill: document.getElementById('warmup-pill'),
    warmupLabel: document.getElementById('warmup-label'),
    overallStatusPill: document.getElementById('overall-status-pill'),
    overallStatusLabel: document.getElementById('overall-status-label'),
    overallStatusIcon: document.getElementById('overall-status-icon'),
    freshnessIndicator: document.getElementById('freshness-indicator'),
    freshnessText: document.getElementById('freshness-text'),
    bellBtn: document.getElementById('bell-btn'),
    bellBadge: document.getElementById('bell-badge'),
    themeToggle: document.getElementById('theme-toggle'),
    themeIconDark: document.getElementById('theme-icon-dark'),
    themeIconLight: document.getElementById('theme-icon-light'),
    soundToggle: document.getElementById('sound-toggle'),
    soundIconMuted: document.getElementById('sound-icon-muted'),
    soundIconActive: document.getElementById('sound-icon-active'),
    demoControlsToggle: document.getElementById('demo-controls-toggle'),
    demoControlsPanel: document.getElementById('demo-controls-panel'),
    demoCloseBtn: document.getElementById('demo-close-btn'),
    demoFeedbackArea: document.getElementById('demo-feedback-area'),
    demoFeedbackText: document.getElementById('demo-feedback-text'),
    demoFeedbackIcon: document.getElementById('demo-feedback-icon'),

    // Alert Banner
    alertBanner: document.getElementById('alert-banner'),
    bannerHeadline: document.getElementById('banner-headline'),
    bannerSubtext: document.getElementById('banner-subtext'),
    bannerViewBtn: document.getElementById('banner-view-btn'),
    bannerDismissBtn: document.getElementById('banner-dismiss-btn'),

    // Stat Cards
    statErrorRate: document.getElementById('stat-error-rate'),
    statBaselineMean: document.getElementById('stat-baseline-mean'),
    statBaselineUpper: document.getElementById('stat-baseline-upper'),
    statWindowCounts: document.getElementById('stat-window-counts'),
    statAlerts10m: document.getElementById('stat-alerts-10m'),
    rateTrendBadge: document.getElementById('rate-trend-badge'),

    // Chart
    chartContainer: document.getElementById('chart-container'),
    insightCanvas: document.getElementById('insight-canvas'),
    chartTooltip: document.getElementById('chart-tooltip'),
    tooltipTime: document.getElementById('tooltip-time'),
    tooltipRate: document.getElementById('tooltip-rate'),
    tooltipBaseline: document.getElementById('tooltip-baseline'),
    tooltipUpper: document.getElementById('tooltip-upper'),
    tooltipIncidentRow: document.getElementById('tooltip-incident-row'),
    tooltipAnomalyLabel: document.getElementById('tooltip-anomaly-label'),
    tooltipAnomalyVal: document.getElementById('tooltip-anomaly-val'),
    chartIncidentRows: document.getElementById('chart-incident-rows'),
    incidentRowEmpty: document.getElementById('incident-row-empty'),
    rangeBtns: document.querySelectorAll('.range-btn'),

    // Details Panel
    selectedAlertIdText: document.getElementById('selected-alert-id'),
    selectedSeverityPill: document.getElementById('selected-severity-pill'),
    detailsEmptyState: document.getElementById('details-empty-state'),
    detailsActiveContent: document.getElementById('details-active-content'),
    valZWarning: document.getElementById('val-z-warning'),
    fillZWarning: document.getElementById('fill-z-warning'),
    valZHigh: document.getElementById('val-z-high'),
    fillZHigh: document.getElementById('fill-z-high'),
    valZCritical: document.getElementById('val-z-critical'),
    fillZCritical: document.getElementById('fill-z-critical'),
    factualSummaryText: document.getElementById('factual-summary-text'),
    logSamplesBox: document.getElementById('log-samples-box'),
    logSamplesPre: document.getElementById('log-samples-pre'),
    expandSamplesBtn: document.getElementById('expand-samples-btn'),
    copySamplesBtn: document.getElementById('copy-samples-btn'),
    copyBtnText: document.getElementById('copy-btn-text'),
    awsTimeline: document.getElementById('aws-timeline'),

    // Table
    alertsTableBody: document.getElementById('alerts-table-body'),
    tableCountBadge: document.getElementById('table-count-badge'),
    tableEmptyState: document.getElementById('table-empty-state'),
    alertsTableSection: document.getElementById('alerts-table-section'),

    // Toast Container
    toastContainer: document.getElementById('toast-container')
  };

  let chartInstance = null;
  let socket = null;
  let reconnectTimeoutId = null;
  let audioContext = null;

  // =========================================================================
  // SECTION 3: UTILITIES & FORMATTERS
  // =========================================================================

  /**
   * Parse timestamp defensively (accepts ISO-8601 string or epoch in seconds/ms).
   */
  function parseTimestamp(rawTs) {
    if (!rawTs) return Date.now();
    if (typeof rawTs === 'number') {
      // Epoch seconds vs milliseconds check
      return rawTs < 1e11 ? rawTs * 1000 : rawTs;
    }
    if (typeof rawTs === 'string') {
      const parsed = Date.parse(rawTs);
      return isNaN(parsed) ? Date.now() : parsed;
    }
    return Date.now();
  }

  /**
   * Format epoch to local HH:MM:SS string.
   */
  function formatLocalTime(epochMs) {
    const d = new Date(epochMs);
    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  /**
   * Format number as percentage with fixed decimals.
   */
  function formatPercent(val, decimals = 1) {
    if (typeof val !== 'number' || isNaN(val)) return '--%';
    return (val * 100).toFixed(decimals) + '%';
  }

  /**
   * Display temporary toast notification.
   */
  function showToast(message, type = 'info') {
    if (!dom.toastContainer) return;
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<span>${message}</span>`;
    dom.toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(8px)';
      setTimeout(() => toast.remove(), 250);
    }, 3200);
  }

  /**
   * Synthesize audio chime for CRITICAL alert via Web Audio API (zero external deps).
   */
  function playCriticalChime() {
    if (!state.soundEnabled) return;
    try {
      if (!audioContext) {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
      }
      if (audioContext.state === 'suspended') {
        audioContext.resume();
      }
      const osc = audioContext.createOscillator();
      const gain = audioContext.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(880, audioContext.currentTime); // A5
      osc.frequency.exponentialRampToValueAtTime(440, audioContext.currentTime + 0.35); // A4
      gain.gain.setValueAtTime(0.2, audioContext.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, audioContext.currentTime + 0.35);
      osc.connect(gain);
      gain.connect(audioContext.destination);
      osc.start();
      osc.stop(audioContext.currentTime + 0.36);
    } catch (err) {
      console.warn('Audio playback error:', err);
    }
  }

  // =========================================================================
  // SECTION 4: WEBSOCKET CLIENT & RECONNECT LOGIC
  // =========================================================================

  function initWebSocket() {
    if (reconnectTimeoutId) {
      clearTimeout(reconnectTimeoutId);
      reconnectTimeoutId = null;
    }

    updateConnectionUI('reconnecting');

    try {
      socket = new WebSocket(CONFIG.wsUrl);
    } catch (err) {
      console.error('WebSocket instantiation failed:', err);
      scheduleReconnect();
      return;
    }

    socket.onopen = () => {
      console.log('✅ WebSocket connected to', CONFIG.wsUrl);
      state.connected = true;
      state.reconnecting = false;
      state.reconnectAttempts = 0;
      updateConnectionUI('connected');

      // Fetch recent alert history on connect/reconnect and merge
      fetchAlertsHistory();
    };

    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        handleIncomingMessage(message);
      } catch (err) {
        console.error('Failed to parse WebSocket message:', err, event.data);
      }
    };

    socket.onerror = (error) => {
      console.warn('WebSocket encountered error:', error);
    };

    socket.onclose = () => {
      console.warn('WebSocket connection closed.');
      state.connected = false;
      updateConnectionUI('disconnected');
      scheduleReconnect();
    };
  }

  function scheduleReconnect() {
    state.reconnecting = true;
    updateConnectionUI('reconnecting');

    state.reconnectAttempts++;
    const delay = Math.min(
      CONFIG.reconnect.initialDelayMs * Math.pow(CONFIG.reconnect.multiplier, state.reconnectAttempts - 1),
      CONFIG.reconnect.maxDelayMs
    );

    console.log(`Scheduling reconnect attempt #${state.reconnectAttempts} in ${delay}ms...`);
    reconnectTimeoutId = setTimeout(() => {
      initWebSocket();
    }, delay);
  }

  function updateConnectionUI(status) {
    if (!dom.connectionPill || !dom.connectionLabel) return;

    dom.connectionPill.className = 'pill connection-pill';
    if (status === 'connected') {
      dom.connectionPill.classList.add('pill-connected');
      dom.connectionLabel.textContent = 'Connected';
    } else if (status === 'reconnecting') {
      dom.connectionPill.classList.add('pill-reconnecting');
      dom.connectionLabel.textContent = `Reconnecting (${state.reconnectAttempts})`;
    } else {
      dom.connectionPill.classList.add('pill-disconnected');
      dom.connectionLabel.textContent = 'Disconnected';
    }
  }

  // =========================================================================
  // SECTION 5: MESSAGE HANDLERS
  // =========================================================================

  function handleIncomingMessage(msg) {
    if (!msg || !msg.type) return;

    switch (msg.type) {
      case 'metric':
        handleMetric(msg);
        break;
      case 'alert':
        handleAlert(msg);
        break;
      case 'alert_update':
        handleAlertUpdate(msg);
        break;
      default:
        // Ignore unknown message types safely
        break;
    }
  }

  /**
   * Handle 1-second metric stream.
   */
  function handleMetric(m) {
    state.lastMetricTime = Date.now();
    state.currentMetric = m;
    state.warmingUp = Boolean(m.warming_up);

    // Feed point to canvas chart
    if (chartInstance) {
      chartInstance.addMetric(m);
    }

    // Update Top bar Warmup Pill
    updateWarmupPill(m.warming_up);

    // Update Stat Cards Row
    updateStatCards(m);

    // Check rate against baseline upper bound for overall status calculation
    evaluateOverallStatus(m);

    // Refresh freshness indicator
    resetFreshnessWatchdog();
  }

  /**
   * Handle new anomaly alert.
   */
  function handleAlert(rawAlert, backfill = false) {
    if (!rawAlert || !rawAlert.id) return;

    // Normalize alert
    const alert = {
      id: String(rawAlert.id),
      ts: parseTimestamp(rawAlert.ts),
      severity: rawAlert.severity || 'WARNING',
      error_rate: Number(rawAlert.error_rate || 0),
      baseline_mean: Number(rawAlert.baseline_mean || 0),
      baseline_std: Number(rawAlert.baseline_std || 0),
      z_score: Number(rawAlert.z_score || 0),
      total_lines: parseInt(rawAlert.total_lines || 0, 10),
      error_lines: parseInt(rawAlert.error_lines || 0, 10),
      sample_messages: Array.isArray(rawAlert.sample_messages) ? rawAlert.sample_messages : [],
      aws_status: rawAlert.aws_status || 'pending',
      // Delivery status history array for the timeline
      status_history: [{
        status: rawAlert.aws_status || 'pending',
        ts: parseTimestamp(rawAlert.ts)
      }]
    };

    // De-duplication: check if alert already recorded
    const isNew = !state.alertsById.has(alert.id);
    if (!isNew) {
      // Merge updates if exists
      const existing = state.alertsById.get(alert.id);
      // Keep live aws_status/history; never let a stale backfill downgrade it
      const keepStatus = existing.aws_status;
      const keepHist = existing.status_history;
      Object.assign(existing, alert);
      existing.aws_status = keepStatus;
      existing.status_history = keepHist;
      renderAlertsTable();
      if (state.selectedAlertId === alert.id) {
        renderIncidentDetails(existing);
      }
      return;
    }

    // Insert new alert
    state.alertsById.set(alert.id, alert);
    state.alerts.unshift(alert);

    // Cap history length at 100
    if (state.alerts.length > CONFIG.maxAlertHistory) {
      const removed = state.alerts.pop();
      state.alertsById.delete(removed.id);
    }

    // Register marker on chart
    if (chartInstance) {
      chartInstance.addAnomaly(alert);
    }

    if (backfill) return; // history load: no bell/sound/banner side effects

    state.unreadAlertsCount++;
    updateUnreadBell();

    // Trigger visual/sound alert for CRITICAL
    if (alert.severity === 'CRITICAL') {
      playCriticalChime();
      document.body.classList.add('flash-critical');
      setTimeout(() => document.body.classList.remove('flash-critical'), 1400);
    }

    // Update overall status immediately from alert severity
    setOverallStatus(alert.severity);

    // Update alert banner
    updateAlertBanner();

    // Render table with subtle highlight on new row
    renderAlertsTable(alert.id);

    // Update chart incident rows below chart
    updateChartIncidentRows();

    // Auto-select latest alert in details panel if none selected or if user is on latest
    if (!state.selectedAlertId || state.selectedAlertId === state.alerts[1]?.id) {
      selectAlert(alert.id);
    }
  }

  /**
   * Handle alert_update message (AWS delivery status transitions).
   */
  function handleAlertUpdate(update) {
    if (!update || !update.id) return;
    const alert = state.alertsById.get(update.id);
    if (!alert) {
      // Ignore update for unknown alert id safely
      return;
    }

    const newStatus = update.aws_status;
    if (alert.aws_status !== newStatus) {
      alert.aws_status = newStatus;
      if (!alert.status_history) alert.status_history = [];
      alert.status_history.push({
        status: newStatus,
        ts: Date.now()
      });

      // Update table row in place
      updateTableRowStatus(alert.id, newStatus);

      // If this alert is currently selected in details panel, update timeline
      if (state.selectedAlertId === alert.id) {
        renderIncidentDetails(alert);
      }
    }
  }

  // =========================================================================
  // SECTION 6: CHART MODULE & RANGE SELECTOR
  // =========================================================================

  function initChart() {
    if (!dom.insightCanvas) return;

    chartInstance = new window.InsightChart(dom.insightCanvas, {
      visibleWindowSec: state.chartRangeSec,
      onTooltipUpdate: (info) => {
        handleChartTooltip(info);
      }
    });

    // Wire segmented range control buttons
    dom.rangeBtns.forEach((btn) => {
      btn.addEventListener('click', (e) => {
        dom.rangeBtns.forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        const range = parseInt(btn.getAttribute('data-range'), 10) || 300;
        state.chartRangeSec = range;
        if (chartInstance) {
          chartInstance.setVisibleWindow(range);
        }
      });
    });
  }

  function handleChartTooltip(info) {
    if (!dom.chartTooltip) return;

    if (!info || !info.point) {
      dom.chartTooltip.classList.add('hidden');
      return;
    }

    const { point, anomaly, x, y } = info;
    dom.chartTooltip.classList.remove('hidden');
    dom.chartTooltip.style.left = `${x}px`;
    dom.chartTooltip.style.top = `${y}px`;

    dom.tooltipTime.textContent = formatLocalTime(point.ts);
    dom.tooltipRate.textContent = formatPercent(point.error_rate);
    dom.tooltipBaseline.textContent = point.baseline_mean !== null ? formatPercent(point.baseline_mean) : 'Learning...';
    dom.tooltipUpper.textContent = point.baseline_upper !== null ? formatPercent(point.baseline_upper) : 'Learning...';

    if (anomaly) {
      dom.tooltipIncidentRow.classList.remove('hidden');
      dom.tooltipAnomalyLabel.textContent = `${anomaly.severity}:`;
      dom.tooltipAnomalyVal.textContent = `z=${anomaly.z_score || '3+'}`;
    } else {
      dom.tooltipIncidentRow.classList.add('hidden');
    }
  }

  /**
   * Update the 2 compact incident rows inside the chart card (Section 4.4).
   */
  function updateChartIncidentRows() {
    if (!dom.chartIncidentRows) return;

    if (state.alerts.length === 0) {
      dom.chartIncidentRows.innerHTML = `
        <div class="incident-row-empty" id="incident-row-empty">
          <span class="muted-icon">✓</span>
          <span>No active incidents. System operating within normal baseline boundaries.</span>
        </div>
      `;
      return;
    }

    // Take latest 2 alerts
    const recent2 = state.alerts.slice(0, 2);
    let html = '';

    recent2.forEach((alert) => {
      const icon = alert.severity === 'CRITICAL' ? '●' : (alert.severity === 'HIGH' ? '⯄' : '▲');
      const iconClass = `icon-${alert.severity.toLowerCase()}`;
      const summary = `Error rate ${formatPercent(alert.error_rate)} vs ${formatPercent(alert.baseline_mean)} baseline`;
      const timeStr = formatLocalTime(alert.ts);

      html += `
        <div class="incident-row-item" data-alert-id="${alert.id}">
          <div class="incident-row-left">
            <span class="incident-badge-icon ${iconClass}">${icon}</span>
            <span class="incident-summary-text">${summary}</span>
          </div>
          <div class="incident-row-right">
            <span class="incident-time-text">${timeStr}</span>
            <button class="incident-inspect-link" data-inspect-id="${alert.id}">Inspect &rarr;</button>
          </div>
        </div>
      `;
    });

    dom.chartIncidentRows.innerHTML = html;

    // Attach click listener to select alert
    dom.chartIncidentRows.querySelectorAll('.incident-row-item').forEach((row) => {
      row.addEventListener('click', () => {
        const id = row.getAttribute('data-alert-id');
        if (id) selectAlert(id);
      });
    });
  }

  // =========================================================================
  // SECTION 7: ALERTS TABLE MODULE
  // =========================================================================

  function renderAlertsTable(newRowId = null) {
    if (!dom.alertsTableBody) return;

    if (state.alerts.length === 0) {
      dom.alertsTableBody.innerHTML = '';
      if (dom.tableEmptyState) dom.tableEmptyState.classList.remove('hidden');
      if (dom.tableCountBadge) dom.tableCountBadge.textContent = '0 total';
      return;
    }

    if (dom.tableEmptyState) dom.tableEmptyState.classList.add('hidden');
    if (dom.tableCountBadge) dom.tableCountBadge.textContent = `${state.alerts.length} total`;

    let html = '';
    state.alerts.forEach((alert) => {
      const isSelected = alert.id === state.selectedAlertId;
      const isNew = alert.id === newRowId;
      const diffPts = ((alert.error_rate - alert.baseline_mean) * 100).toFixed(1);
      const desc = `${formatPercent(alert.error_rate)} vs ${formatPercent(alert.baseline_mean)} baseline (+${diffPts} pts)`;
      const timeStr = formatLocalTime(alert.ts);
      const zStr = typeof alert.z_score === 'number' ? alert.z_score.toFixed(1) : '--';

      const sevClass = `severity-${alert.severity.toLowerCase()}`;
      const sevIcon = alert.severity === 'CRITICAL' ? '●' : (alert.severity === 'HIGH' ? '⯄' : '▲');

      const awsHtml = renderAwsStatusPill(alert.aws_status);

      html += `
        <tr class="${isSelected ? 'selected' : ''} ${isNew ? 'new-alert-row' : ''}" data-row-id="${alert.id}">
          <td class="tabular-num"><strong>Error rate</strong></td>
          <td>${desc}</td>
          <td>
            <span class="severity-pill ${sevClass}">
              <span>${sevIcon}</span>
              <span>${alert.severity}</span>
            </span>
          </td>
          <td class="tabular-num">${timeStr}</td>
          <td id="aws-cell-${alert.id}">${awsHtml}</td>
          <td class="tabular-num"><strong>${zStr}</strong></td>
          <td style="text-align: right;">
            <button class="btn-table-action" data-action="toggle-drawer" data-id="${alert.id}">View</button>
          </td>
        </tr>
        <tr class="sample-drawer-row hidden" id="drawer-${alert.id}">
          <td colspan="7">
            <div class="drawer-content">
              <span class="drawer-title">Sample Error Messages (${alert.sample_messages.length}):</span>
              <pre class="drawer-pre">${formatSampleMessages(alert.sample_messages)}</pre>
            </div>
          </td>
        </tr>
      `;
    });

    dom.alertsTableBody.innerHTML = html;

    // Attach row select & view button listeners
    dom.alertsTableBody.querySelectorAll('tr[data-row-id]').forEach((row) => {
      row.addEventListener('click', (e) => {
        const id = row.getAttribute('data-row-id');
        // If clicking the view button, toggle drawer
        if (e.target.matches('[data-action="toggle-drawer"]')) {
          e.stopPropagation();
          toggleTableDrawer(id);
          selectAlert(id);
          return;
        }
        selectAlert(id);
      });
    });
  }

  function renderAwsStatusPill(status) {
    if (status === 'sent') {
      return `<span class="aws-pill aws-sent"><span>✓</span><span>Sent to CloudWatch</span></span>`;
    }
    if (status === 'failed') {
      return `<span class="aws-pill aws-failed"><span>✕</span><span>Delivery Failed</span></span>`;
    }
    if (status === 'queued') {
      return `<span class="aws-pill aws-queued"><span>⧗</span><span>Queued - not yet delivered</span></span>`;
    }
    // pending or fallback
    return `<span class="aws-pill aws-pending"><span class="warmup-spinner" style="width:8px;height:8px;border-width:1.5px;"></span><span>Pending</span></span>`;
  }

  function updateTableRowStatus(alertId, newStatus) {
    const cell = document.getElementById(`aws-cell-${alertId}`);
    if (cell) {
      cell.innerHTML = renderAwsStatusPill(newStatus);
    }
  }

  function toggleTableDrawer(alertId) {
    const drawer = document.getElementById(`drawer-${alertId}`);
    if (drawer) {
      drawer.classList.toggle('hidden');
    }
  }

  function formatSampleMessages(samples) {
    if (!samples || samples.length === 0) return 'No sample messages attached.';
    return samples.map((s, idx) => `[${idx + 1}] ${s}`).join('\n');
  }

  // =========================================================================
  // SECTION 8: INCIDENT DETAILS PANEL MODULE
  // =========================================================================

  function selectAlert(alertId) {
    state.selectedAlertId = alertId;
    const alert = state.alertsById.get(alertId);

    // Update table row highlighting
    if (dom.alertsTableBody) {
      dom.alertsTableBody.querySelectorAll('tr[data-row-id]').forEach((r) => {
        if (r.getAttribute('data-row-id') === alertId) {
          r.classList.add('selected');
        } else {
          r.classList.remove('selected');
        }
      });
    }

    if (!alert) {
      renderIncidentDetails(null);
      return;
    }

    renderIncidentDetails(alert);
  }

  function renderIncidentDetails(alert) {
    if (!alert) {
      if (dom.detailsEmptyState) dom.detailsEmptyState.classList.remove('hidden');
      if (dom.detailsActiveContent) dom.detailsActiveContent.classList.add('hidden');
      if (dom.selectedAlertIdText) dom.selectedAlertIdText.textContent = 'No alert selected';
      if (dom.selectedSeverityPill) {
        dom.selectedSeverityPill.className = 'pill pill-muted';
        dom.selectedSeverityPill.textContent = 'Idle';
      }
      return;
    }

    if (dom.detailsEmptyState) dom.detailsEmptyState.classList.add('hidden');
    if (dom.detailsActiveContent) dom.detailsActiveContent.classList.remove('hidden');

    // Header ID and Severity badge
    if (dom.selectedAlertIdText) dom.selectedAlertIdText.textContent = `${alert.id} (${formatLocalTime(alert.ts)})`;
    if (dom.selectedSeverityPill) {
      dom.selectedSeverityPill.className = `pill severity-${alert.severity.toLowerCase()}`;
      const icon = alert.severity === 'CRITICAL' ? '●' : (alert.severity === 'HIGH' ? '⯄' : '▲');
      dom.selectedSeverityPill.textContent = `${icon} ${alert.severity}`;
    }

    // Z-Score Progress Bars (z 3, z 5, z 8)
    const z = typeof alert.z_score === 'number' ? alert.z_score : 0;

    // Warning bar (threshold 3)
    const pctW = Math.min(100, Math.round((z / 3) * 100));
    dom.fillZWarning.style.width = `${pctW}%`;
    dom.valZWarning.textContent = z >= 3 ? 'Reached (100%)' : `${pctW}%`;

    // High bar (threshold 5)
    const pctH = Math.min(100, Math.round((z / 5) * 100));
    dom.fillZHigh.style.width = `${pctH}%`;
    dom.valZHigh.textContent = z >= 5 ? 'Reached (100%)' : `${pctH}%`;

    // Critical bar (threshold 8)
    const pctC = Math.min(100, Math.round((z / 8) * 100));
    dom.fillZCritical.style.width = `${pctC}%`;
    dom.valZCritical.textContent = z >= 8 ? 'Reached (100%)' : `${pctC}%`;

    // Factual Callout Summary (Contract fields only, no AI text)
    const upperLimit = alert.baseline_mean + (4.0 * alert.baseline_std);
    const summaryText = `Error rate ${formatPercent(alert.error_rate)} against a baseline of ${formatPercent(alert.baseline_mean)} (upper band ${formatPercent(upperLimit)}), ${alert.error_lines} errors in ${alert.total_lines} lines over the last 60s.`;
    dom.factualSummaryText.textContent = summaryText;

    // Recent Error Samples
    const samplesRaw = formatSampleMessages(alert.sample_messages);
    dom.logSamplesPre.textContent = samplesRaw;

    // Expand samples toggle if more than 3 samples
    if (alert.sample_messages.length > 2) {
      dom.expandSamplesBtn.classList.remove('hidden');
      dom.expandSamplesBtn.textContent = 'Show More';
      dom.logSamplesBox.classList.remove('expanded');
    } else {
      dom.expandSamplesBtn.classList.add('hidden');
    }

    // AWS Delivery Timeline
    renderAwsTimeline(alert);
  }

  function renderAwsTimeline(alert) {
    if (!dom.awsTimeline) return;

    const history = alert.status_history || [{
      status: alert.aws_status || 'pending',
      ts: alert.ts
    }];

    let html = `
      <div class="timeline-step completed">
        <span class="timeline-dot"></span>
        <div class="timeline-title">Anomaly Detected</div>
        <div class="timeline-time">${formatLocalTime(alert.ts)} &bull; Triggered by detector z-score threshold</div>
      </div>
    `;

    history.forEach((step) => {
      let stepClass = 'completed';
      let title = 'CloudWatch Delivery Successful';
      if (step.status === 'failed') {
        stepClass = 'failed';
        title = 'CloudWatch Push Failed';
      } else if (step.status === 'queued') {
        stepClass = 'queued';
        title = 'AWS Dispatch Queued (Dry-run mode)';
      } else if (step.status === 'pending') {
        stepClass = 'pending';
        title = 'AWS Dispatch Pending';
      }

      html += `
        <div class="timeline-step ${stepClass}">
          <span class="timeline-dot"></span>
          <div class="timeline-title">${title}</div>
          <div class="timeline-time">${formatLocalTime(step.ts)} &bull; Status: ${step.status}</div>
        </div>
      `;
    });

    dom.awsTimeline.innerHTML = html;
  }

  // Setup sample copying and expansion
  dom.copySamplesBtn?.addEventListener('click', () => {
    const text = dom.logSamplesPre.textContent;
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
      dom.copyBtnText.textContent = 'Copied!';
      showToast('Copied log samples to clipboard', 'success');
      setTimeout(() => {
        dom.copyBtnText.textContent = 'Copy';
      }, 2000);
    }).catch(() => {
      showToast('Failed to copy to clipboard', 'error');
    });
  });

  dom.expandSamplesBtn?.addEventListener('click', () => {
    const isExpanded = dom.logSamplesBox.classList.toggle('expanded');
    dom.expandSamplesBtn.textContent = isExpanded ? 'Show Less' : 'Show More';
  });

  // =========================================================================
  // SECTION 9: DEMO CONTROLS MODULE
  // =========================================================================

  function initDemoControls() {
    // Check ?controls=0 in URL to hide controls if requested
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('controls') === '0') {
      if (dom.demoControlsToggle) dom.demoControlsToggle.classList.add('hidden');
      if (dom.demoControlsPanel) dom.demoControlsPanel.classList.add('hidden');
      return;
    }

    // Toggle panel
    dom.demoControlsToggle?.addEventListener('click', () => {
      dom.demoControlsPanel.classList.toggle('hidden');
    });

    dom.demoCloseBtn?.addEventListener('click', () => {
      dom.demoControlsPanel.classList.add('hidden');
    });

    // Wire Scenario buttons
    document.querySelectorAll('.btn-scenario').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const scenario = btn.getAttribute('data-scenario');
        if (!scenario) return;
        await triggerScenario(scenario, btn);
      });
    });

    // Wire Reset button
    document.getElementById('btn-inject-reset')?.addEventListener('click', async (e) => {
      await triggerReset(e.currentTarget);
    });
  }

  async function triggerScenario(scenario, btnElement) {
    setButtonLoading(btnElement, true);
    setDemoFeedback(null);

    try {
      const response = await fetch(`${CONFIG.apiBaseUrl}/inject/${scenario}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const res = await response.json();
      setDemoFeedback(`Triggered '${scenario}' successfully.`, 'success');
      showToast(`Injected scenario: ${scenario}`, 'success');
    } catch (err) {
      console.error(`Failed to inject scenario '${scenario}':`, err);
      setDemoFeedback(`Failed: ${err.message}`, 'error');
      showToast(`Error: ${err.message}`, 'error');
    } finally {
      setButtonLoading(btnElement, false);
    }
  }

  async function triggerReset(btnElement) {
    setButtonLoading(btnElement, true);
    setDemoFeedback(null);

    try {
      const response = await fetch(`${CONFIG.apiBaseUrl}/reset`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      resetUiState();
      setDemoFeedback('Detector reset to baseline state.', 'success');
      showToast('Detector reset to normal baseline', 'success');
    } catch (err) {
      console.error('Failed to reset detector:', err);
      setDemoFeedback(`Reset failed: ${err.message}`, 'error');
      showToast(`Reset failed: ${err.message}`, 'error');
    } finally {
      setButtonLoading(btnElement, false);
    }
  }

  function resetUiState() {
    state.alerts = [];
    state.alertsById.clear();
    state.selectedAlertId = null;
    state.unreadAlertsCount = 0;
    state.lastDismissedAlertId = null;
    state.consecutiveCalmSeconds = 0;
    state.warmingUp = true;
    state.warmupStartedTs = Date.now();
    if (chartInstance) chartInstance.clear();
    updateUnreadBell();
    renderAlertsTable();
    renderIncidentDetails(null);
    updateChartIncidentRows();
    setOverallStatus('Normal');
  }

  function setButtonLoading(btn, isLoading) {
    if (!btn) return;
    btn.disabled = isLoading;
    if (isLoading) {
      btn.dataset.prevOpacity = btn.style.opacity;
      btn.style.opacity = '0.5';
    } else {
      btn.style.opacity = btn.dataset.prevOpacity || '1';
    }
  }

  function setDemoFeedback(message, type = 'info') {
    if (!dom.demoFeedbackArea || !dom.demoFeedbackText) return;
    if (!message) {
      dom.demoFeedbackArea.classList.add('hidden');
      return;
    }
    dom.demoFeedbackArea.className = `demo-feedback-area demo-feedback-${type}`;
    dom.demoFeedbackText.textContent = message;
    dom.demoFeedbackIcon.textContent = type === 'success' ? '✓' : (type === 'error' ? '✕' : 'ℹ');
    dom.demoFeedbackArea.classList.remove('hidden');
  }

  // =========================================================================
  // SECTION 10: SYSTEM CONTROLS & WATCHDOG
  // =========================================================================

  /**
   * Backfill recent alert history from GET /alerts on load and reconnect.
   */
  async function fetchAlertsHistory() {
    try {
      const res = await fetch(`${CONFIG.apiBaseUrl}/alerts`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const history = await res.json();

      if (Array.isArray(history)) {
        // Defensive sorting: sort by ts ascending then add
        history.sort((a, b) => parseTimestamp(a.ts) - parseTimestamp(b.ts));
        history.forEach((a) => {
          handleAlert(a, true);
        });
        renderAlertsTable();
        updateChartIncidentRows();
        if (!state.selectedAlertId && state.alerts[0]) selectAlert(state.alerts[0].id);
      }
    } catch (err) {
      console.warn('Could not fetch alert history from /alerts:', err.message);
    }
  }

  function evaluateOverallStatus(metric) {
    if (!metric) return;
    const isAboveBand = metric.baseline_upper !== null && metric.error_rate > metric.baseline_upper;

    if (isAboveBand) {
      state.consecutiveCalmSeconds = 0;
      // If error rate is high and active alerts exist, keep status high
      if (metric.error_rate >= 0.12) setOverallStatus('CRITICAL');
      else if (metric.error_rate >= 0.07) setOverallStatus('HIGH');
      else setOverallStatus('WARNING');
    } else {
      state.consecutiveCalmSeconds++;
      // Return to Normal after rate has been back inside band for a few seconds (>4s)
      if (state.consecutiveCalmSeconds >= 4) {
        setOverallStatus('Normal');
      }
    }
  }

  function setOverallStatus(status) {
    state.overallStatus = status;
    if (!dom.overallStatusPill) return;

    dom.overallStatusPill.className = 'pill overall-pill';
    if (status === 'CRITICAL') {
      dom.overallStatusPill.classList.add('pill-critical');
      dom.overallStatusIcon.textContent = '●';
      dom.overallStatusLabel.textContent = 'CRITICAL';
    } else if (status === 'HIGH') {
      dom.overallStatusPill.classList.add('pill-high');
      dom.overallStatusIcon.textContent = '⯄';
      dom.overallStatusLabel.textContent = 'HIGH';
    } else if (status === 'WARNING') {
      dom.overallStatusPill.classList.add('pill-warning');
      dom.overallStatusIcon.textContent = '▲';
      dom.overallStatusLabel.textContent = 'WARNING';
    } else {
      dom.overallStatusPill.classList.add('pill-normal');
      dom.overallStatusIcon.textContent = '●';
      dom.overallStatusLabel.textContent = 'Normal';
    }

    updateAlertBanner();
  }

  function updateAlertBanner() {
    if (!dom.alertBanner) return;

    // Show banner only while overall status is NOT Normal
    if (state.overallStatus === 'Normal') {
      dom.alertBanner.classList.add('hidden');
      return;
    }

    // If user explicitly dismissed the banner for this incident, keep hidden
    const latestAlert = state.alerts[0];
    if (latestAlert && state.lastDismissedAlertId === latestAlert.id) {
      dom.alertBanner.classList.add('hidden');
      return;
    }

    dom.alertBanner.className = `alert-banner banner-${state.overallStatus.toLowerCase()}`;
    dom.bannerHeadline.textContent = `${state.alerts.length} Anomalies Detected • Current severity: ${state.overallStatus}`;
    dom.bannerSubtext.textContent = 'Rolling error rate breached learned EWMA boundaries. AWS delivery active.';
    dom.alertBanner.classList.remove('hidden');
  }

  dom.bannerDismissBtn?.addEventListener('click', () => {
    const latestAlert = state.alerts[0];
    if (latestAlert) {
      state.lastDismissedAlertId = latestAlert.id;
    }
    dom.alertBanner?.classList.add('hidden');
  });

  dom.bannerViewBtn?.addEventListener('click', () => {
    dom.alertsTableSection?.scrollIntoView({ behavior: 'smooth' });
  });

  function updateWarmupPill(isWarming) {
    if (!dom.warmupPill || !dom.warmupLabel) return;

    if (isWarming) {
      dom.warmupPill.className = 'pill warmup-pill pill-active';
      const elapsed = Math.floor((Date.now() - state.warmupStartedTs) / 1000);
      const remaining = Math.max(0, CONFIG.warmupDurationSeconds - elapsed);
      dom.warmupLabel.textContent = `Learning baseline... (${remaining}s)`;
    } else {
      dom.warmupPill.className = 'pill warmup-pill pill-monitoring';
      dom.warmupLabel.textContent = '✓ Monitoring';
    }
  }

  function updateStatCards(metric) {
    if (!metric) return;

    // Current Error Rate
    dom.statErrorRate.textContent = formatPercent(metric.error_rate);

    // Baseline Mean
    dom.statBaselineMean.textContent = metric.baseline_mean !== null
      ? formatPercent(metric.baseline_mean)
      : 'Learning...';

    // Baseline Upper Limit
    dom.statBaselineUpper.textContent = metric.baseline_upper !== null
      ? formatPercent(metric.baseline_upper)
      : 'Learning...';

    // Window line counts (e.g. 24 / 1,200)
    dom.statWindowCounts.textContent = `${metric.error_lines} / ${metric.total_lines.toLocaleString()}`;

    // Count alerts in last 10 minutes
    const tenMinAgo = Date.now() - (10 * 60 * 1000);
    const alerts10m = state.alerts.filter(a => a.ts >= tenMinAgo).length;
    dom.statAlerts10m.textContent = alerts10m;
  }

  function updateUnreadBell() {
    if (!dom.bellBadge) return;
    if (state.unreadAlertsCount > 0) {
      dom.bellBadge.textContent = state.unreadAlertsCount > 99 ? '99+' : state.unreadAlertsCount;
      dom.bellBadge.classList.remove('hidden');
    } else {
      dom.bellBadge.classList.add('hidden');
    }
  }

  dom.bellBtn?.addEventListener('click', () => {
    state.unreadAlertsCount = 0;
    updateUnreadBell();
    dom.alertsTableSection?.scrollIntoView({ behavior: 'smooth' });
  });

  // Watchdog: check for stale data (>3s without metric)
  function resetFreshnessWatchdog() {
    if (state.staleTimer) clearInterval(state.staleTimer);

    updateFreshnessUI(0);
    state.staleTimer = setInterval(() => {
      if (!state.lastMetricTime) {
        updateFreshnessUI(null);
        return;
      }
      const diffSec = Math.floor((Date.now() - state.lastMetricTime) / 1000);
      updateFreshnessUI(diffSec);
    }, 1000);
  }

  function updateFreshnessUI(secondsAgo) {
    if (!dom.freshnessIndicator || !dom.freshnessText) return;

    if (secondsAgo === null) {
      dom.freshnessIndicator.className = 'freshness-indicator';
      dom.freshnessText.textContent = 'Waiting for data...';
      return;
    }

    if (secondsAgo > CONFIG.staleDataThresholdSeconds) {
      dom.freshnessIndicator.className = 'freshness-indicator freshness-stale';
      dom.freshnessText.textContent = `STALE DATA: No metrics for ${secondsAgo}s`;
    } else {
      dom.freshnessIndicator.className = 'freshness-indicator';
      dom.freshnessText.textContent = `Last data ${secondsAgo}s ago`;
    }
  }

  // Audio Toggle
  dom.soundToggle?.addEventListener('click', () => {
    state.soundEnabled = !state.soundEnabled;
    if (state.soundEnabled) {
      dom.soundIconMuted?.classList.add('hidden');
      dom.soundIconActive?.classList.remove('hidden');
      showToast('Sound enabled for CRITICAL alerts', 'info');
      playCriticalChime();
    } else {
      dom.soundIconMuted?.classList.remove('hidden');
      dom.soundIconActive?.classList.add('hidden');
      showToast('Sound muted', 'info');
    }
  });

  // Theme Toggle (Dark / Light)
  dom.themeToggle?.addEventListener('click', () => {
    state.theme = state.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', state.theme);

    if (state.theme === 'light') {
      dom.themeIconDark?.classList.add('hidden');
      dom.themeIconLight?.classList.remove('hidden');
    } else {
      dom.themeIconDark?.classList.remove('hidden');
      dom.themeIconLight?.classList.add('hidden');
    }

    // Refresh canvas colors on theme change
    if (chartInstance) {
      const isLight = state.theme === 'light';
      chartInstance.options.colors.grid = isLight ? 'rgba(0, 0, 0, 0.06)' : 'rgba(255, 255, 255, 0.05)';
      chartInstance.options.colors.text = isLight ? '#52525b' : '#9a9aa6';
      chartInstance.options.colors.line = isLight ? '#6366f1' : '#a5b4fc';
      chartInstance.render();
    }
  });

  // =========================================================================
  // BOOTSTRAP INITIALIZATION
  // =========================================================================
  function init() {
    initChart();
    initDemoControls();
    resetFreshnessWatchdog();
    initWebSocket();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
