# VisionLens: Store Intelligence API

Welcome to **VisionLens**! This project was built to solve a highly ambiguous, real-world retail problem: **How do we extract meaningful, business-critical metrics from raw, unstructured CCTV footage?**

Rather than treating this as a pure model-building exercise, we designed an **end-to-end intelligence system**. VisionLens ingests raw video feeds, tracks human movement using YOLOv8n + ByteTrack, maps coordinate data to physical store zones, correlates visual tracking with external Point-of-Sale (POS) transactions, and serves the finalized metrics via a high-performance REST API.

### 🌟 Key Capabilities Built:
* **True Conversion Funnels:** Tracks users strictly from `Store Entry` → `Zone Visit` → `Billing Queue` → `Purchase` without double-counting.
* **POS Correlation Engine:** Merges external CSV transaction logs with AI visual data to map physical purchases to specific tracked sessions.
* **Smart Edge-Case Handling:** Uses geometric math to properly handle Entry/Exit line crossings, dynamically filters out store staff based on uniform colors (HSV detection), and maintains session continuity during occlusions or brief camera exits (Re-entry handling).
* **Live Anomaly Detection:** Automatically flags business anomalies like unexpected queue spikes, dead zones, and sudden conversion rate drops.

---

## 🚀 Quick Setup in 5 Steps

---

**1️⃣ Clone the Repository**
```bash
git clone https://github.com/AkankshaRaj07/VisionLens.git && cd VisionLens
```

**2️⃣ Place Your Custom Videos**
*Note: Layout & POS Data are already included in the repo!*
```bash
mkdir -p data/clips/STORE_BLR_002
```
> 💡 Drop your CCTV `.mp4` clips directly into `data/clips/STORE_BLR_002/`. Ensure they are named appropriately (e.g. `entry.mp4`, `billing.mp4`).

**3️⃣ Start the API Services**
```bash
docker compose up --build -d
```

**4️⃣ Run the Detection Pipeline**
First, install the pipeline requirements:
```bash
pip install -r requirements-pipeline.txt
```
Then run the pipeline against your videos:

> 🍎 **Mac / 🐧 Linux:**
> ```bash
> bash pipeline/run.sh http://localhost:8000
> ```
> 
> 🪟 **Windows (PowerShell):**
> ```powershell
> .\pipeline\run.ps1 -ApiUrl http://localhost:8000
> ```

**5️⃣ Open the Live Dashboard**

> 💻 **Option A: Terminal UI**
> ```bash
> pip install rich httpx
> python dashboard/live_dashboard.py --api-url http://localhost:8000 --store-id STORE_BLR_002
> ```
> 
> 🌐 **Option B: Web UI (Recommended)**
> Open `http://localhost:8000/dashboard` in your browser!

---

**Verify it works:**
**Verify API health:**
```bash
curl http://localhost:8000/health
```

**Verify store metrics:**
```bash
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

#### Without live API feed (batch)
**Mac/Linux:**
```bash
bash pipeline/run.sh
```
**Windows:**
```powershell
.\pipeline\run.ps1
```

#### With live API feed
**Mac/Linux:**
```bash
bash pipeline/run.sh http://localhost:8000
```
**Windows:**
```powershell
.\pipeline\run.ps1 -ApiUrl http://localhost:8000
```

### Camera types

| Camera type | Flag | Notes |
|-------------|------|-------|
| Entry/exit threshold | `--camera-type entry` | Emits ENTRY, EXIT, REENTRY |
| Main floor | `--camera-type floor` | Emits ZONE_ENTER, ZONE_EXIT, ZONE_DWELL |
| Billing area | `--camera-type billing` | Emits BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON |

### Replay pre-processed events

If you've already run detection and have JSONL files, replay them into the API:

**Batch Replay:**
```bash
python pipeline/replay.py \
  --events-dir data/events \
  --api-url http://localhost:8000
```

**Simulate real-time (with delays between events):**
```bash
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
See [docs/CODEBASE_REFERENCE.md](docs/CODEBASE_REFERENCE.md) for a detailed breakdown of every file, pipeline step, and API endpoint.

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
