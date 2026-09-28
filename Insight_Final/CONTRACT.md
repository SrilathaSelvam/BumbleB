# Insight — Backend / Frontend Integration Contract (v1.0 Frozen)

This specification defines the communication contract between the **Insight Backend** and the **Insight Frontend Dashboard**. Teammates building the backend should adhere strictly to this contract.

---

## 1. Connection & Ports

- **Base HTTP URL**: `http://localhost:8000`
- **WebSocket URL**: `ws://localhost:8000/ws`
- **CORS**: Backend **must** enable CORS headers (`Access-Control-Allow-Origin: *`) on all HTTP endpoints to support local development and browser origin testing.

---

## 2. WebSocket Protocol (`/ws`)

The WebSocket connection streams real-time metrics and alerts from the backend detector to the dashboard. All messages are JSON objects containing a `type` discriminator.

### 2.1 `metric` (Sent every 1.0 second)

Emitted at a steady 1 Hz frequency containing the rolling 60-second sliding window metrics.

```json
{
  "type": "metric",
  "ts": "2026-09-28T14:00:00.000Z",
  "error_rate": 0.0215,
  "baseline_mean": 0.0210,
  "baseline_upper": 0.0490,
  "total_lines": 1240,
  "error_lines": 26,
  "warming_up": false
}
```

| Field | Type | Description |
| :--- | :--- | :--- |
| `type` | `"metric"` | Message type discriminator (constant). |
| `ts` | `string` \| `number` | Timestamp. Either ISO 8601 string or epoch number (seconds or milliseconds). Parsed defensively. |
| `error_rate` | `number` (0.0 to 1.0) | Rolling error rate over the 60-second sliding window (`error_lines / total_lines`). |
| `baseline_mean`| `number` \| `null` | EWMA learned baseline mean. **Must be `null` during warm-up**. |
| `baseline_upper`| `number` \| `null` | Upper guard band limit (`mean + k * std`). **Must be `null` during warm-up**. |
| `total_lines` | `integer` | Total log lines parsed in the current 60s window. |
| `error_lines` | `integer` | Total error log lines detected in the 60s window. |
| `warming_up` | `boolean` | `true` during the initial learning period (default 45s); `false` during active monitoring. |

---

### 2.2 `alert` (Sent immediately upon anomaly detection)

Emitted whenever an anomaly is detected that breaches the baseline upper limit and z-score threshold.

```json
{
  "type": "alert",
  "id": "alt-1774859000-88a1f4",
  "ts": "2026-09-28T14:02:15.120Z",
  "severity": "CRITICAL",
  "error_rate": 0.1480,
  "baseline_mean": 0.0210,
  "baseline_std": 0.0070,
  "z_score": 12.14,
  "total_lines": 1250,
  "error_lines": 185,
  "sample_messages": [
    "ERROR [PaymentGateway] Connection timed out after 3000ms",
    "CRITICAL [CheckoutController] NullPointerException in calculateTaxes()",
    "ERROR [OrderProcessingService] DB Deadlock on table 'orders'"
  ],
  "aws_status": "pending"
}
```

| Field | Type | Description |
| :--- | :--- | :--- |
| `type` | `"alert"` | Message type discriminator (constant). |
| `id` | `string` | Globally unique alert identifier. Used for de-duplication and updates. |
| `ts` | `string` \| `number` | Anomaly timestamp. |
| `severity` | `"WARNING"` \| `"HIGH"` \| `"CRITICAL"` | Severity category based on detector z-score guards: <br>&bull; `WARNING`: $z \ge 3.0$<br>&bull; `HIGH`: $z \ge 5.0$<br>&bull; `CRITICAL`: $z \ge 8.0$ |
| `error_rate` | `number` | The error rate at trigger time. |
| `baseline_mean`| `number` | Learned EWMA baseline mean at trigger time. |
| `baseline_std` | `number` | Rolling standard deviation at trigger time. |
| `z_score` | `number` | Statistical deviation score: $(error\_rate - baseline\_mean) / baseline\_std$. |
| `total_lines` | `integer` | Window total lines. |
| `error_lines` | `integer` | Window error count. |
| `sample_messages` | `string[]` | Array of raw recent error log lines illustrating the failure context. |
| `aws_status` | `"pending"` \| `"sent"` \| `"failed"` | Initial AWS delivery status (typically starts at `"pending"`). |

---

### 2.3 `alert_update` (Sent when AWS delivery state changes)

Emitted asynchronously when CloudWatch Logs / SNS delivery completes or fails.

```json
{
  "type": "alert_update",
  "id": "alt-1774859000-88a1f4",
  "aws_status": "sent"
}
```

| Field | Type | Description |
| :--- | :--- | :--- |
| `type` | `"alert_update"` | Message type discriminator (constant). |
| `id` | `string` | Corresponds to an existing alert's `id`. |
| `aws_status` | `"pending"` \| `"sent"` \| `"failed"` \| `"queued"` | Updated delivery status. `"queued"` represents AWS dry-run mode. The frontend will never display "sent" until this status is received. |

---

## 3. HTTP REST Endpoints

### 3.1 `GET /alerts`
Called on frontend load and upon reconnection to backfill historical alerts.
- **Method**: `GET`
- **Response**: `200 OK` with JSON array of alert objects (last ~100).
- **Ordering**: Unspecified (frontend will sort defensively by `ts`).

### 3.2 `POST /inject/{scenario}`
Used during live demos to trigger simulated failure scenarios.
- **Method**: `POST`
- **Path Parameter**: `{scenario}`:
  - `"blip"`: Temporary error spike below upper threshold; returns to calm; no alert emitted.
  - `"ramp"`: Gradual climb escalating `WARNING` $\rightarrow$ `HIGH` $\rightarrow$ `CRITICAL` with AWS delivery transitions, then returning to normal baseline.
  - `"sustained"`: Persistent severe outage with ongoing `CRITICAL` alerts.
- **Response**: `200 OK`
```json
{
  "status": "ok",
  "scenario": "ramp",
  "message": "Scenario triggered successfully."
}
```

### 3.3 `POST /reset`
Resets detector and simulation state back to calm baseline.
- **Method**: `POST`
- **Response**: `200 OK`
```json
{
  "status": "ok",
  "message": "Detector state reset."
}
```

---

## 4. Error Handling & Resilience Rules

1. **Unknown Fields / Types**: The frontend safely ignores unknown fields and message types.
2. **Missing Band**: When `warming_up` is true, `baseline_mean` and `baseline_upper` may be `null`. The frontend will not draw the band until values become non-null.
3. **De-duplication**: Reconnection calls `GET /alerts` and merges with active WebSocket alerts without duplicate rows.
4. **Stale Watchdog**: If no `metric` message arrives for $> 3$ seconds, the frontend enters a visible `STALE DATA` warning state.
