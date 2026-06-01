# Detailed Architectural Choices & Trade-offs

This document outlines the profound engineering decisions, AI-assisted overrides, and architectural trade-offs made while designing the Apex Retail Store Intelligence system. It addresses exactly why certain paths were chosen over others across the Detection Pipeline, the Event Schema, and the API Architecture.

---

## 1. Detection Model Selection: YOLOv8n + ByteTrack vs. Alternatives

### The Business Constraint
Apex Retail operates physical stores requiring real-time insights (15fps feeds) from standard CCTV infrastructure. The solution must run on realistic edge hardware (CPUs or lightweight VPUs), ruling out massive server-side GPU dependencies.

### Options Considered
1. **YOLOv8/YOLOv9 + DeepSORT:** Heavyweight detection with a ResNet-based appearance embedding (Re-ID) for tracking.
2. **RT-DETR (Real-Time DEtection TRansformer):** State-of-the-art accuracy, but significantly higher memory footprint and slower CPU inference.
3. **YOLOv8n + ByteTrack:** Lightweight nano model paired with an IoU/confidence-based tracker.
4. **VLM (Vision Language Model):** Sending frames to an LLM (GPT-4V) for analysis.

### What the AI Suggested
During initial prototyping, the AI suggested using **YOLOv8n** for detection, but recommended integrating a **VLM (like GPT-4V)** specifically to handle the edge case of Staff Detection (e.g., prompting the VLM with `"Classify if the person in this cropped image is wearing the blue store uniform"`).

### What We Chose and Why (The Override)
We chose **YOLOv8n + ByteTrack** for detection and tracking, but completely **overrode** the AI's suggestion to use a VLM for staff detection. 

**Reasoning for YOLOv8n + ByteTrack:**
- *Latency vs. Accuracy:* YOLOv8n inferences at ~30-40ms on a standard CPU. At 15fps, we have ~66ms per frame. YOLOv8n leaves enough headroom for tracking and I/O.
- *ByteTrack's Superiority:* DeepSORT extracts a 128D feature vector for every bounding box to perform tracking. This adds ~10-20ms per person. In a crowded billing queue (10+ people), DeepSORT would crash the CPU framerate. ByteTrack associates tracklets using pure bounding box overlap (IoU) and detection confidence scores (utilizing both high and low-confidence detections). This makes tracking effectively $O(1)$ regarding deep-learning overhead.

**Reasoning for Overriding the VLM for Staff Detection:**
- *Network & Cost:* Calling a VLM API for every tracked person across 40 stores at 15fps is architecturally impossible due to network latency, API rate limits, and catastrophic cloud costs.
- *Our Solution (HSV Masking):* Store uniforms are deterministic (blue). We wrote a custom deterministic pipeline that crops the upper 40% of the bounding box (isolating the torso), converts it to the HSV color space, and applies a strict `inRange` mask. If the active pixels exceed `STAFF_CONFIDENCE_THRESHOLD`, we flag `is_staff = True`. This executes in <1ms per frame locally. We traded the generic flexibility of a VLM for deterministic, sub-millisecond edge compute.

---

## 2. Event Schema Design Rationale

### The Business Constraint
The detection layer must emit events that natively support complex stage-gated funnel queries (Entry → Zone Visit → Billing Queue → Purchase) without double-counting.

### Options Considered
1. **Raw Frame Emission (Stateless Pipeline):** The pipeline emits `{"track_id": 1, "cx": 400, "cy": 500, "timestamp": "..."}` continuously. The API handles all spatial geometry.
2. **Stateful Behavioral Events (Chosen):** The pipeline handles spatial geometry and only emits discrete state changes (`ENTRY`, `ZONE_DWELL`, `BILLING_QUEUE_JOIN`).

### What the AI Suggested
The AI originally suggested a **Stateless Pipeline** model, advising to stream raw bounding box coordinates to a Kafka/Redis queue and letting a Python worker service process the dwells and entries in real-time.

### What We Chose and Why (The Override)
We explicitly **overrode** the AI and built a **Stateful Behavioral Event Schema**.

**Reasoning:**
- *Network Saturation:* Emitting raw coordinates at 15fps for 20 people results in 300 events *per second* per camera. Across 40 stores * 3 cameras = 36,000 requests per second (RPS) of pure noise.
- *Decoupling & Cohesion:* By encapsulating the spatial logic (`store_layout.json`) inside the `VisitorTracker` state machine within the pipeline, the API remains blissfully unaware of camera angles, pixel coordinates, or framerates. 
- *Schema Effectiveness:* Our schema emits highly semantic events:
  ```json
  {
    "event_id": "uuid-v4",
    "event_type": "ZONE_DWELL",
    "dwell_ms": 45000
  }
  ```
  This reduces network traffic by 99.9% and allows the API to compute `AVG(dwell_ms)` natively in SQLite without doing complex temporal math on the fly.

---

## 3. Intelligence API Architecture

### The Business Constraint
The API must be production-ready, meaning it must handle unreliable network connections from edge stores (duplicate payloads, dropped packets) and never crash when asked for metrics during an empty store period.

### Options Considered
1. **Redis-backed Deduplication:** Using an external Redis instance to cache `event_id`s with a 24-hour TTL to prevent double ingestion.
2. **Database Primary Key Idempotency (Chosen):** Relying strictly on ACID properties of the SQL database.

### What the AI Suggested
The AI strongly advocated for adding **Redis** to the `docker-compose.yml` to manage idempotency, funnel session states, and caching.

### What We Chose and Why (The Override)
We **overrode** the AI to keep the stack purely built on **SQLite + FastAPI**, utilizing SQL Primary Keys for idempotency.

**Reasoning:**
- *Operational Complexity:* Adding Redis introduces a new failure domain, requires more RAM on the deployment server, and mandates complex cache-invalidation logic.
- *Idempotent Ingestion Design:* We defined `event_id` as the primary key in our `EventRecord` SQLAlchemy model. In the `POST /events/ingest` endpoint, we accept batches of 500 events. We iterate through the batch, flushing each to the DB. If an `IntegrityError` is thrown, we `db.rollback()` that specific row, increment the `duplicate` counter, and proceed.
- *Partial Success Capability:* Standard FastAPI rejects an entire payload with a `422 Unprocessable Entity` if a single item fails validation. We architected the `IngestRequest` to accept `List[dict]` and manually invoke `StoreEvent.model_validate(ev)` inside a `try/except` block. This guarantees that if a camera glitches and corrupts 1 JSON object out of 500, the API stores the 499 valid events and returns a `200 OK` with a detailed error map for the corrupted item. This maximizes data retention, directly satisfying the highest standards of production readiness.
