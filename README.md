# Store Intelligence API

Real-time retail analytics from CCTV footage. Processes raw video clips through a person detection pipeline (YOLOv8n + ByteTrack) and serves live store metrics via a REST API.

---

## Setup in 5 Commands

```bash
# 1. Clone the repo
git clone <your-repo-url> && cd store-intelligence

# 2. Place your data
mkdir -p data/clips data/events
# Copy CCTV clips to data/clips/STORE_BLR_002/ (or other store folders)
# Copy store_layout.json to data/

# 3. Start the API
docker compose up --build -d

# 4. Run the detection pipeline against the clips
pip install -r requirements-pipeline.txt
bash pipeline/run.sh http://localhost:8000

# 5. Open the live dashboard
# Option A: Terminal UI
pip install rich httpx
python dashboard/live_dashboard.py --api-url http://localhost:8000 --store-id STORE_BLR_002

# Option B: Web UI (Recommended)
# Open http://localhost:8000/dashboard in your browser!
```

**Verify it works:**
```bash
curl http://localhost:8000/health
curl http://localhost:8000/stores/STORE_BLR_002/metrics
```

---

## Detection Pipeline

The pipeline processes clips per camera angle and emits structured events.

### Process a single clip

```bash
python -m pipeline.detect \
  --clip data/clips/STORE_BLR_002/CAM_ENTRY_01.mp4 \
  --store-id STORE_BLR_002 \
  --camera-id CAM_ENTRY_01 \
  --camera-type entry \
  --layout data/store_layout.json \
  --output data/events/STORE_BLR_002_CAM_ENTRY_01.jsonl \
  --api-url http://localhost:8000
```

### Process all clips at once

```bash
# Without live API feed (batch)
bash pipeline/run.sh

# With live API feed
bash pipeline/run.sh http://localhost:8000
```

### Camera types

| Camera type | Flag | Notes |
|-------------|------|-------|
| Entry/exit threshold | `--camera-type entry` | Emits ENTRY, EXIT, REENTRY |
| Main floor | `--camera-type floor` | Emits ZONE_ENTER, ZONE_EXIT, ZONE_DWELL |
| Billing area | `--camera-type billing` | Emits BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON |

### Replay pre-processed events

If you've already run detection and have JSONL files, replay them into the API:

```bash
python pipeline/replay.py \
  --events-dir data/events \
  --api-url http://localhost:8000

# Simulate real-time (with delays between events)
python pipeline/replay.py \
  --events-dir data/events \
  --api-url http://localhost:8000 \
  --realtime
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/events/ingest` | Ingest up to 500 events (idempotent) |
| GET | `/stores/{id}/metrics` | Unique visitors, conversion rate, dwell, queue depth |
| GET | `/stores/{id}/funnel` | Entry → Zone → Billing → Purchase with drop-off % |
| GET | `/stores/{id}/heatmap` | Zone visit frequency + dwell, normalised 0–100 |
| GET | `/stores/{id}/anomalies` | Active anomalies (queue spike, dead zone, conversion drop) |
| GET | `/health` | Service status + per-store feed staleness |

### Example: ingest events

```bash
curl -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "events": [{
      "event_id": "550e8400-e29b-41d4-a716-446655440000",
      "store_id": "STORE_BLR_002",
      "camera_id": "CAM_ENTRY_01",
      "visitor_id": "VIS_c8a2f1",
      "event_type": "ENTRY",
      "timestamp": "2026-03-03T14:22:10Z",
      "zone_id": null,
      "dwell_ms": 0,
      "is_staff": false,
      "confidence": 0.91,
      "metadata": {"queue_depth": null, "sku_zone": null, "session_seq": 1}
    }]
  }'
```

---

## Running Tests

```bash
pip install pytest pytest-asyncio httpx
pytest tests/ -v --tb=short
```

Test coverage target: >70% statement coverage.

---

## Architecture

See [docs/DESIGN.md](docs/DESIGN.md) for full architecture overview and AI-assisted decisions.
See [docs/CHOICES.md](docs/CHOICES.md) for model selection, schema design, and API choices.

---

## Project Structure

```
store-intelligence/
├── pipeline/
│   ├── detect.py      # YOLOv8n + ByteTrack detection
│   ├── tracker.py     # Entry/exit, zone tracking, Re-ID
│   ├── emit.py        # Schema validation + JSONL + API POST
│   ├── replay.py      # Batch replay of JSONL into API
│   └── run.sh         # Process all clips
├── app/
│   ├── main.py        # FastAPI app + middleware
│   ├── database.py    # SQLAlchemy async + SQLite
│   ├── models.py      # Pydantic schemas
│   └── routers/
│       ├── events.py  # POST /events/ingest
│       ├── stores.py  # GET /stores/{id}/*
│       └── health.py  # GET /health
├── dashboard/
│   └── live_dashboard.py  # Rich terminal dashboard
├── tests/
│   ├── test_api.py        # API endpoint tests
│   └── test_pipeline.py   # Tracker + emitter unit tests
├── docs/
│   ├── DESIGN.md
│   └── CHOICES.md
├── docker-compose.yml
├── Dockerfile
├── Dockerfile.dashboard
├── requirements.txt           # API dependencies
├── requirements-pipeline.txt  # Detection pipeline dependencies
└── README.md
```

---

## Notes on CPU Performance

YOLOv8n processes ~2fps on a typical laptop CPU at 1080p. The pipeline samples every 3rd frame (5fps effective) which gives good detection quality with manageable processing time.

Estimated processing time per 20-minute clip: ~15–25 minutes on CPU.

To speed up: reduce `PROCESS_EVERY_N_FRAMES` to 6 (2.5fps) or resize frames to 640px width before inference.
