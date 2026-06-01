# Complete System Architecture & Design

The Apex Retail Store Intelligence system is engineered to solve a specific problem: transforming unstructured, noisy CCTV video feeds into structured, actionable business metrics (specifically targeting Offline Store Conversion Rate). 

The architecture is divided into three distinct bounded contexts:
1. **The Edge Detection Pipeline (Part A)**
2. **The Post-Processing Correlation Engine (Part B)**
3. **The Intelligence API Backend (Part C)**

---

## 1. The Edge Detection Pipeline

The pipeline is the foundation of the system. It processes raw `1080p, 15fps` footage. Because retail environments suffer from varied lighting, partial occlusions, and severe angle overlap, the pipeline must be highly resilient.

### 1.1 Object Detection (`YOLOv8n`)
- **Execution Strategy:** We utilize Ultralytics YOLOv8 (Nano). The nano variant is chosen to guarantee real-time execution capabilities on standard CPU hardware. 
- **Sampling Rate:** To optimize throughput, we don't necessarily need to process 15 frames per second. Processing 3-5 FPS provides sufficient temporal resolution to track movement without saturating the CPU.
- **Dependency Resilience:** Modern PyTorch (v2.6+) introduced breaking changes to `torch.load()` by defaulting `weights_only=True`, which prevents YOLO checkpoints from loading. Instead of forcing strict dependency pinning, our pipeline explicitly monkey-patches `torch.load` at runtime to safely bypass this constraint, ensuring the pipeline spins up seamlessly across diverse host environments.

### 1.2 Temporal Tracking (`ByteTrack`)
- **Tracking Algorithm:** Detected bounding boxes are passed to ByteTrack.
- **Why ByteTrack:** Unlike DeepSORT, which runs a secondary Convolutional Neural Network to extract appearance embeddings for every detected person (causing extreme CPU bottlenecks during "Group Entries" or "Billing Queue Buildups"), ByteTrack relies entirely on spatial overlap (IoU) and Kalman filtering. 
- **Occlusion Handling:** ByteTrack associates both high-confidence and low-confidence detections. If a customer is partially obscured by a product display, their detection confidence drops. ByteTrack retains their trajectory, preventing the track from fragmenting. Track fragmentation is the #1 cause of inflated visitor counts in legacy retail systems.

### 1.3 State Management (`VisitorTracker` State Machine)
The pipeline does not emit raw frames. It instantiates a `VisitorTracker` which manages spatial state over time, emitting JSONL events that comply strictly with the `Event Schema`.

- **Handling Group Entries:** Because ByteTrack assigns a unique integer ID to distinct spatial boxes, 3 people walking through a door simultaneously are tracked as 3 distinct entities, yielding 3 `ENTRY` events with 3 unique `visitor_id`s.
- **Handling Re-entry Inflation:** The tracker implements a `5-minute sliding window` memory. If `VIS_c8a2f1` generates an `EXIT` event, their identity is kept in memory. If they cross the entry threshold again within 5 minutes, the tracker emits a `REENTRY` event. This allows the API to group these behaviors without inflating the top-of-funnel unique visitor counts.
- **Dynamic Zone Mapping:** The tracker parses `store_layout.json` to dynamically slice the camera frame based on the number of defined zones. When a bounding box centroid enters a zone, a timer starts. Upon leaving, a `ZONE_DWELL` event is emitted containing the exact `dwell_ms`.

### 1.4 The VLM Alternative: Staff Detection
- **The Challenge:** Staff move through all zones constantly and must be excluded from customer conversion metrics.
- **The Solution:** We utilize a highly optimized HSV color thresholding filter. The upper 40% of every bounding box (the torso) is cropped and converted to HSV. A binary mask isolates the specific blue hues of the Apex Retail uniform. If the pixel ratio exceeds a defined threshold, the event is flagged `is_staff: true`.
- **The AI Override:** The LLM suggested passing bounding box crops to a Vision Language Model (VLM). This was overridden because VLM API latency (2-5 seconds per call) would completely destroy the real-time nature of the pipeline. Our HSV mask executes in `<1 millisecond` per frame.

---

## 2. POS Correlation Engine

To determine if a visitor actually made a purchase (calculating the ultimate Conversion Rate), we must bridge the gap between anonymous video tracking and anonymous POS receipts.

### 2.1 The Time-Window Correlation Logic
- **The Inputs:** The API ingests `BILLING_QUEUE_JOIN` events from the detection pipeline and loads `pos_transactions.csv`.
- **The Execution:** We process this in a deterministic post-processing script (`correlate_pos.py`). 
- **The Matching Algorithm:** For every `BILLING_QUEUE_JOIN` event, the system checks the timestamp. It scans the POS transaction records for any transaction occurring within a **5-minute window** following the visitor's exit from the billing zone.
- **The Abandonment Flag:** If no POS transaction matches the temporal window, the system definitively outputs a `BILLING_QUEUE_ABANDON` event. This completely closes the loop on the conversion funnel.

---

## 3. Intelligence API Backend

The backend is built with **FastAPI** and **SQLAlchemy (Async)** connected to a local SQLite database. It is designed to be purely stateless, acting as an aggregation engine.

### 3.1 Idempotent & Fault-Tolerant Ingestion (`POST /events/ingest`)
- **Idempotency:** The pipeline may transmit the same event batch multiple times due to edge network drops. The API enforces idempotency by using the `event_id` as the Database Primary Key. `IntegrityError` exceptions are caught gracefully, incrementing a `duplicate` counter rather than failing the request.
- **Partial Batch Success:** Standard REST APIs reject entire payloads with `422 Unprocessable Entity` if a single record fails Pydantic schema validation. We engineered the `IngestRequest` to accept raw dictionaries, validating them individually via `StoreEvent.model_validate()` inside the ingestion loop. Malformed events are appended to an `errors` array, while valid events are flushed to the DB, guaranteeing maximum data retention and functional correctness.

### 3.2 Complex Aggregations
The API exposes endpoints that fulfill exact business requirements:
- **Metrics (`/stores/{id}/metrics`):** Aggregates total `ENTRY` counts (filtering out `is_staff=True`), calculates average `dwell_ms`, and computes the live conversion rate. Zero-traffic periods safely return `0` rather than throwing `DivisionByZero` errors.
- **Funnel (`/stores/{id}/funnel`):** Groups events hierarchically: `ENTRY/REENTRY` → `ZONE_VISIT` → `BILLING_QUEUE` → `PURCHASE`.
- **Anomalies (`/stores/{id}/anomalies`):** A live rule engine assessing data thresholds. For example, if the latest `queue_depth` exceeds 8, it triggers a `CRITICAL` anomaly with the suggested action: *"Open additional billing counters immediately."*

### 3.3 Graceful Degradation
To satisfy production readiness, the API must not leak stack traces. We implemented a global FastAPI middleware that intercepts `sqlalchemy.exc.OperationalError` (such as SQLite database locks) and translates it into a structured HTTP `503 Service Unavailable` response. 

---

## 4. Deployment and Observability

The entire system is completely containerised, matching the Acceptance Gate constraints:
- **`docker compose up`**: Starts the `api` and `dashboard` services, mounting local volumes for `/app/data` (SQLite persistence) and `/app/logs`.
- **Structured Logging:** The API middleware injects a UUID `trace_id` into every incoming request. It logs the `trace_id`, `store_id`, `endpoint`, `latency_ms`, and `status_code` in a structured JSON string, making it fully ready for Datadog or ELK ingestion.
- **Live Dashboard:** A `rich`-powered terminal dashboard provides a real-time view of the store metrics, proving the pipeline and API are functionally connected and operational.
