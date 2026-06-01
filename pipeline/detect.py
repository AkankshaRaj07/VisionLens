#!/usr/bin/env python3
"""
detect.py — Main detection + tracking script.
Processes CCTV clips using YOLOv8n + ByteTrack (built into ultralytics).
Emits structured events to a JSONL file and optionally POSTs to the API.

Usage:
    python pipeline/detect.py \
        --clip data/clips/STORE_BLR_002/CAM_ENTRY_01.mp4 \
        --store-id STORE_BLR_002 \
        --camera-id CAM_ENTRY_01 \
        --camera-type entry \
        --layout data/store_layout.json \
        --output data/events/STORE_BLR_002_CAM_ENTRY_01.jsonl \
        --api-url http://localhost:8000

Camera types: entry | floor | billing
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import cv2
import numpy as np

from pipeline.tracker import VisitorTracker
from pipeline.emit import EventEmitter

# YOLOv8 — lazy import so API container doesn't need it
try:
    from ultralytics import YOLO
    import torch
    
    # PyTorch 2.6 compatibility workaround for YOLOv8
    original_load = torch.load
    def safe_load(*args, **kwargs):
        kwargs['weights_only'] = False
        return original_load(*args, **kwargs)
    torch.load = safe_load

    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False


# ── Constants ────────────────────────────────────────────────────────────────
PROCESS_EVERY_N_FRAMES = 3      # sample at ~5fps from 15fps source
PERSON_CLASS_ID = 0             # COCO class 0 = person
CONFIDENCE_THRESHOLD = 0.35     # emit low-conf events but flag them
ENTRY_LINE_FRACTION = 0.5       # horizontal line at 50% of frame height
DWELL_EMIT_INTERVAL_SEC = 30    # emit ZONE_DWELL every 30s of continuous dwell

# Staff uniform HSV ranges (blue/grey typical retail uniform)
STAFF_HSV_LOWER = np.array([90, 50, 50])
STAFF_HSV_UPPER = np.array([130, 255, 255])
STAFF_CONFIDENCE_THRESHOLD = 0.6


def load_layout(layout_path: str, store_id: str) -> dict:
    with open(layout_path) as f:
        layout = json.load(f)
    for store in layout.get("stores", []):
        if store["store_id"] == store_id:
            return store
    raise ValueError(f"Store {store_id} not found in layout")


def classify_staff(frame: np.ndarray, bbox: tuple[int, int, int, int]) -> tuple[bool, float]:
    """
    Rule-based staff detection using uniform color in upper-body region.
    Returns (is_staff, confidence).
    Upper body = top 40% of bounding box.
    """
    x1, y1, x2, y2 = bbox
    body_h = y2 - y1
    upper_y2 = y1 + int(body_h * 0.4)
    upper_body = frame[y1:upper_y2, x1:x2]

    if upper_body.size == 0:
        return False, 0.0

    hsv = cv2.cvtColor(upper_body, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, STAFF_HSV_LOWER, STAFF_HSV_UPPER)
    ratio = mask.sum() / (mask.size * 255)

    is_staff = bool(ratio > STAFF_CONFIDENCE_THRESHOLD)
    return is_staff, float(ratio)


def get_zone_for_position(
    cx: int, cy: int, frame_w: int, frame_h: int,
    camera_type: str, layout: dict
) -> str | None:
    """
    Map pixel position to zone using camera_type + layout zones.
    For floor cameras, divides frame into equal grid segments per zone.
    For billing cameras, returns BILLING_COUNTER.
    """
    if camera_type == "entry":
        return None  # entry camera → ENTRY/EXIT events, no zone

    if camera_type == "billing":
        return "BILLING_COUNTER"

    # Floor camera: map x-position to zones in order
    zones = layout.get("zones", [])
    if not zones:
        return "MAIN_FLOOR"

    zone_width = frame_w / len(zones)
    zone_idx = min(int(cx / zone_width), len(zones) - 1)
    return zones[zone_idx].get("zone_id", "MAIN_FLOOR")


def process_clip(
    clip_path: str,
    store_id: str,
    camera_id: str,
    camera_type: str,
    layout: dict,
    output_path: str,
    api_url: str | None,
    clip_start_time: datetime,
) -> int:
    if not YOLO_AVAILABLE:
        print("ERROR: ultralytics not installed. Run: pip install -r requirements-pipeline.txt")
        sys.exit(1)

    model = YOLO("yolov8n.pt")  # downloads automatically on first run
    tracker = VisitorTracker(camera_type=camera_type, store_id=store_id)
    emitter = EventEmitter(output_path=output_path, api_url=api_url)

    cap = cv2.VideoCapture(clip_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open clip: {clip_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print(f"Processing {clip_path}: {total_frames} frames at {fps}fps")
    entry_line_y = int(frame_h * ENTRY_LINE_FRACTION)

    frame_idx = 0
    events_emitted = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1
        if frame_idx % PROCESS_EVERY_N_FRAMES != 0:
            continue

        # Timestamp for this frame
        elapsed_sec = frame_idx / fps
        frame_time = clip_start_time + timedelta(seconds=elapsed_sec)

        # Run YOLOv8 with ByteTrack
        results = model.track(
            frame,
            persist=True,
            classes=[PERSON_CLASS_ID],
            conf=CONFIDENCE_THRESHOLD,
            tracker="bytetrack.yaml",
            verbose=False,
        )

        detections = []
        if results[0].boxes is not None and results[0].boxes.id is not None:
            boxes = results[0].boxes
            for i, box in enumerate(boxes):
                track_id = int(box.id[0])
                conf = float(box.conf[0])
                x1, y1, x2, y2 = [int(v) for v in box.xyxy[0]]
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2

                is_staff, staff_conf = classify_staff(frame, (x1, y1, x2, y2))
                zone = get_zone_for_position(cx, cy, frame_w, frame_h, camera_type, layout)

                detections.append({
                    "track_id": track_id,
                    "bbox": (x1, y1, x2, y2),
                    "cx": cx,
                    "cy": cy,
                    "conf": conf,
                    "is_staff": is_staff,
                    "zone": zone,
                })

        # Calculate queue depth for billing camera
        if camera_type == "billing":
            queue_depth = sum(1 for d in detections if not d["is_staff"] and d["zone"] == "BILLING_COUNTER")
            for d in detections:
                if d["zone"] == "BILLING_COUNTER":
                    d["queue_depth"] = queue_depth

        # Update tracker and get events
        new_events = tracker.update(
            detections=detections,
            frame_time=frame_time,
            frame_h=frame_h,
            entry_line_y=entry_line_y,
            camera_id=camera_id,
            store_id=store_id,
        )

        for event in new_events:
            emitter.emit(event)
            events_emitted += 1

        if frame_idx % 300 == 0:
            progress = (frame_idx / total_frames) * 100
            print(f"  {progress:.1f}% — {events_emitted} events emitted")

    cap.release()

    # Flush any remaining dwell events
    final_events = tracker.flush(frame_time=frame_time, camera_id=camera_id, store_id=store_id)
    for event in final_events:
        emitter.emit(event)
        events_emitted += 1

    emitter.close()
    print(f"Done. {events_emitted} events written to {output_path}")
    return events_emitted


def main():
    parser = argparse.ArgumentParser(description="Store Intelligence Detection Pipeline")
    parser.add_argument("--clip", required=True, help="Path to CCTV clip")
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--camera-id", required=True)
    parser.add_argument("--camera-type", required=True, choices=["entry", "floor", "billing"])
    parser.add_argument("--layout", default="data/store_layout.json")
    parser.add_argument("--output", required=True, help="Output JSONL path")
    parser.add_argument("--api-url", default=None, help="POST events to API (optional)")
    parser.add_argument(
        "--clip-start-time",
        default=None,
        help="ISO-8601 UTC start time of clip. Defaults to now.",
    )
    args = parser.parse_args()

    clip_start = (
        datetime.fromisoformat(args.clip_start_time.replace("Z", "+00:00"))
        if args.clip_start_time
        else datetime.now(timezone.utc)
    )

    layout = load_layout(args.layout, args.store_id)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)

    process_clip(
        clip_path=args.clip,
        store_id=args.store_id,
        camera_id=args.camera_id,
        camera_type=args.camera_type,
        layout=layout,
        output_path=args.output,
        api_url=args.api_url,
        clip_start_time=clip_start,
    )


if __name__ == "__main__":
    main()
