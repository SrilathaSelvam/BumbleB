# Insight — Real-Time Log Anomaly Detector

## Install (Python 3.10+)
    python -m venv .venv
    # Windows: .venv\Scripts\activate    |  macOS/Linux: source .venv/bin/activate
    pip install -r requirements.txt

## Run backend (terminal 1, from this folder)
    uvicorn backend.main:app --host 0.0.0.0 --port 8000

## Run frontend (terminal 2, from this folder)
    python -m http.server 5500 --directory frontend
Open http://localhost:5500  (opening frontend/index.html directly also works; no build, no CDN, fully offline).

## URLs
- Dashboard: http://localhost:5500
- API: http://localhost:8000  (GET /alerts, POST /inject/{blip|ramp|sustained}, POST /reset, GET /health)
- WebSocket: ws://localhost:8000/ws

## Demo
Wait ~45s for "Monitoring", then Demo Controls -> Ramp / Sustained / Blip / Reset.

## Tests
    pytest

## Optional: real generator + AWS sink (env vars)
    GENERATOR=backend.generator:Generator SINK=backend.aws_sync:AwsSink AWS_DRY_RUN=1
Real AWS: set AWS_REGION, CW_LOG_GROUP, CW_LOG_STREAM, SNS_TOPIC_ARN and standard AWS credentials in your environment (never commit keys).
Defaults: detector=backend.detection.detector:Detector, generator=mock (built-in traffic), sink=none.
Other settings: see backend/config.py. Contract: CONTRACT.md (frozen v1.0).
