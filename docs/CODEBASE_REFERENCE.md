# VisionLens Store Intelligence: Comprehensive Codebase Reference

This document provides a detailed, step-by-step breakdown of the entire VisionLens Store Intelligence system. It documents the end-to-end pipeline execution, the purpose of every critical file, and a complete reference for all API endpoints.

---

## 1. End-to-End Pipeline Execution (Step-by-Step)

The core purpose of this system is to ingest raw CCTV video footage, use an AI model to detect and track human movement, filter out store staff, map those movements to physical store zones, and serve that data via a RESTful API to a live dashboard.

**Step 1: Inference & Object Detection (`pipeline/detect.py`)**
* **Input:** Raw MP4 video files representing different camera angles (Entry, Floor, Billing).
* **Process:** Uses `ultralytics` YOLOv8n to analyze frames. Bounding boxes are drawn around people (Class 0). It dynamically filters out staff by analyzing the upper-body bounding box for standard store uniform colors (HSV thresholds).
* **Output:** Feeds raw tracks and bounding box coordinates to the Tracker.

**Step 2: Tracking & Re-Identification (`pipeline/tracker.py`)**
* **Input:** Bounding boxes from Step 1.
* **Process:** Uses a custom `VisitorTracker` state machine. It geometrically determines if a person crossed the threshold line (Top-to-Bottom = `ENTRY`, Bottom-to-Top = `EXIT`). For floor cameras, it maps bounding box coordinates to logical zones defined in `store_layout.json`. It handles complex state logic, such as ensuring someone disappearing for a few seconds doesn't break the session (`_find_reentry`).
* **Output:** Emits high-level logical events (e.g., `ENTRY`, `ZONE_ENTER`, `ZONE_DWELL`).

**Step 3: Event Emission & Ingestion (`pipeline/emit.py` & `app/routers/events.py`)**
* **Input:** High-level logical events.
* **Process:** The `EventEmitter` validates events against the standard schema. It then simultaneously appends them to a fast local `.jsonl` file (for archiving/replay) and POSTs them in batches to the backend API (`/events/ingest`).
* **Output:** Validated events are stored in the backend SQLite Database (`EventRecord`).

**Step 4: POS Correlation (`pipeline/correlate_pos.py`)**
* **Input:** External POS transaction CSV files and the current SQLite database.
* **Process:** Cross-references timestamps of when people joined the Billing Queue with timestamps of actual POS terminal purchases. It uses proximity matching to accurately link a tracking session to a physical sale.
* **Output:** Marks specific `BILLING_QUEUE_JOIN` events in the database with a successful `PURCHASE` metadata tag.

**Step 5: Business Logic & Dashboard (`app/routers/stores.py`)**
* **Input:** The raw SQL database of tracked events.
* **Process:** Uses complex SQL aggregation (e.g., `func.count(distinct(EventRecord.visitor_id))`) to calculate Unique Visitors, conversion rates, and funnel drop-offs. It natively handles anomalies (e.g., conversion drops) using sliding time-windows.
* **Output:** Exposes data via `/stores/{id}/metrics` and `/stores/{id}/funnel` to power the frontend dashboard.

---

## 2. API Endpoints Reference

The FastAPI backend is served on port `8000`.

### **POST** `/events/ingest`
* **Description:** Ingests raw structured events from the pipeline. Supports batching (up to 500 events) and is fully idempotent (safely rejects duplicates using `event_id`).
* **Request Body:** `{"events": [EventSchema, ...]}`
* **Returns:** `{"accepted": int, "duplicate": int}`

### **GET** `/stores/{store_id}/metrics`
* **Description:** Fetches top-level KPIs for the dashboard widgets.
* **Returns:** 
  * `unique_visitors`: Count of unique tracking sessions.
  * `conversion_rate`: Percentage of visitors who completed a POS transaction.
  * `avg_dwell_time`: Average seconds spent dwelling in zones.
  * `queue_depth`: Live count of people standing in the Billing zone.

### **GET** `/stores/{store_id}/funnel`
* **Description:** Calculates the conversion funnel drop-off percentages.
* **Returns:** Ordered steps (`Store Entry`, `Zone Visit`, `Billing Queue`, `Purchase`) with strict percentage drop-offs mapped mathematically between steps.

### **GET** `/stores/{store_id}/heatmap`
* **Description:** Aggregates zone dwell times to generate a spatial heatmap of store layout performance.
* **Returns:** A dictionary of `{zone_id: normalized_score_0_to_100}`.

### **GET** `/stores/{store_id}/anomalies`
* **Description:** Analyzes recent data to flag active business anomalies.
* **Returns:** List of strings (e.g., `["QUEUE_SPIKE"]`).

### **GET** `/health`
* **Description:** General system status check. Ensures the database is reachable and checks if the event feed has gone stale (no events in last 5 minutes).

---

## 3. Complete File-by-File Codebase Documentation

### The Pipeline (`/pipeline`)
* **`detect.py`**: The entrypoint for video processing. Handles video loading, YOLOv8 object detection, and staff uniform filtering.
* **`tracker.py`**: The geometric state engine. Handles Line-Crossing algorithms for Entry/Exit cameras, spatial zone mapping for Floor cameras, and session tracking across occlusions.
* **`emit.py`**: Ensures data integrity. Validates pipeline outputs against schemas before formatting to JSONL or POSTing to the API.
* **`correlate_pos.py`**: The Business Intelligence layer script. Merges physical POS transaction CSV logs with the AI-generated visual tracking database.
* **`replay.py`**: A high-performance simulation tool. Bypasses the slow AI detection layer by streaming pre-calculated `.jsonl` events directly to the API, heavily utilized for fast Live Demos.
* **`run.ps1` / `run.sh`**: OS-specific execution wrappers to fully automate spinning up the pipeline against all sample video clips.

### The Backend Application (`/app`)
* **`main.py`**: Initializes the FastAPI application, mounts CORS middleware, connects the SQLite engine, and registers routers.
* **`database.py`**: Contains SQLAlchemy engine setups, declarative bases, and the core `EventRecord` ORM class that maps to the SQLite database.
* **`models.py`**: Uses Pydantic to strictly define the `EventSchema`. Ensures that every event has a UUID, timestamp, camera_id, etc.
* **`routers/events.py`**: Handles incoming HTTP POST requests from the `EventEmitter`, writing valid records to the database.
* **`routers/stores.py`**: The core data science router. Contains all complex SQL aggregations to calculate KPIs, Funnels, and Heatmaps.
* **`routers/health.py`**: A simple endpoint returning system status.

### Testing (`/tests`)
* **`test_api.py`**: Over 15 unit tests covering the API endpoints (validating JSON schemas, handling 404s, testing idempotency).
* **`test_pipeline.py` & `test_tracker.py`**: Heavily tests the tracking math. Simulates coordinate bounding boxes crossing imaginary lines to ensure `ENTRY` and `EXIT` events fire under mathematically precise conditions.
* **`test_stores_extended.py`**: Asserts that the Funnel and Anomaly logic calculates exactly as expected given static mock datasets.

### Documentation & Ops
* **`docker-compose.yml` & `Dockerfile`**: Containerizes the FastAPI application and SQLite database for single-command seamless deployments.
* **`pytest.ini`**: Configuration ensuring tests run asynchronously and enforce high coverage.
* **`docs/DESIGN.md`**: Outlines the high-level system architecture and system components.
* **`docs/CHOICES.md`**: Explains engineering decisions (e.g., choosing YOLOv8n over Heavy models for CPU performance constraints).
