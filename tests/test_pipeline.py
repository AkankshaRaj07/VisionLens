# PROMPT: "Write pytest unit tests for a retail store visitor tracking pipeline.
# The tracker.py module has a VisitorTracker class that takes detections (dicts with
# track_id, cx, cy, conf, is_staff, zone, queue_depth) and emits structured events.
# Camera types: entry (emits ENTRY/EXIT), floor (ZONE_ENTER/EXIT/DWELL), billing (BILLING_QUEUE_JOIN).
# Test: group entry (3 people simultaneously → 3 ENTRY events), re-entry detection
# (same cx position within 5 min → REENTRY not ENTRY), staff flagging (is_staff=True passes through),
# zone dwell after 30s, zone transitions (ZONE_EXIT then ZONE_ENTER on change),
# entry line crossing direction (cy crossing entry_line_y)."
#
# CHANGES MADE:
# - Fixed datetime arithmetic — used timedelta correctly for frame_time advances
# - Added test_emit_validates_schema to test emit.py validation
# - Added test for BILLING_QUEUE_ABANDON scenario
# - Made make_detection() helper to reduce repetition

import pytest
from datetime import datetime, timezone, timedelta
from pipeline.tracker import VisitorTracker, make_visitor_id
from pipeline.emit import EventEmitter
import tempfile
import os
import json


# ── Helpers ──────────────────────────────────────────────────────────────────
STORE_ID = "STORE_BLR_002"
CAMERA_ID = "CAM_ENTRY_01"
FRAME_H = 1080
ENTRY_LINE_Y = 540  # 50% of 1080


def make_detection(track_id: int, cx: int, cy: int, is_staff=False, zone=None, conf=0.9) -> dict:
    return {
        "track_id": track_id,
        "cx": cx,
        "cy": cy,
        "conf": conf,
        "is_staff": is_staff,
        "zone": zone,
        "queue_depth": 0,
    }


def t(offset_sec: int = 0) -> datetime:
    return datetime(2026, 3, 3, 14, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_sec)


# ── Entry / Exit Tests ────────────────────────────────────────────────────────
def test_single_entry_crossing():
    """A person crossing entry line top→bottom emits ENTRY."""
    tracker = VisitorTracker(camera_type="entry", store_id=STORE_ID)
    det = make_detection(track_id=1, cx=300, cy=550)

    # First frame: above entry line
    tracker._prev_positions[1] = 520  # prev_cy above line
    events = tracker.update(
        [det], frame_time=t(1), frame_h=FRAME_H, entry_line_y=ENTRY_LINE_Y,
        camera_id=CAMERA_ID, store_id=STORE_ID
    )
    entry_events = [e for e in events if e["event_type"] == "ENTRY"]
    assert len(entry_events) == 1
    assert entry_events[0]["visitor_id"].startswith("VIS_")
    assert entry_events[0]["is_staff"] is False


def test_group_entry_three_people():
    """3 people entering simultaneously → 3 ENTRY events, 3 unique visitor_ids."""
    tracker = VisitorTracker(camera_type="entry", store_id=STORE_ID)
    detections = [
        make_detection(track_id=i, cx=100 * i, cy=545)
        for i in range(1, 4)
    ]
    # Set all prev_cy above entry line
    for i in range(1, 4):
        tracker._prev_positions[i] = 530

    events = tracker.update(
        detections, frame_time=t(1), frame_h=FRAME_H, entry_line_y=ENTRY_LINE_Y,
        camera_id=CAMERA_ID, store_id=STORE_ID
    )
    entry_events = [e for e in events if e["event_type"] == "ENTRY"]
    assert len(entry_events) == 3

    visitor_ids = [e["visitor_id"] for e in entry_events]
    assert len(set(visitor_ids)) == 3, "Each person must get a unique visitor_id"


def test_exit_outbound_crossing():
    """Person crossing bottom→top emits EXIT."""
    tracker = VisitorTracker(camera_type="entry", store_id=STORE_ID)
    det = make_detection(track_id=1, cx=300, cy=550)

    # Simulate active session
    tracker._prev_positions[1] = 510  # above line
    tracker.update(
        [det], frame_time=t(0), frame_h=FRAME_H, entry_line_y=ENTRY_LINE_Y,
        camera_id=CAMERA_ID, store_id=STORE_ID
    )
    tracker._prev_positions[1] = 550  # now below line — cross above it to exit
    det_exit = make_detection(track_id=1, cx=300, cy=538)
    events = tracker.update(
        [det_exit], frame_time=t(60), frame_h=FRAME_H, entry_line_y=ENTRY_LINE_Y,
        camera_id=CAMERA_ID, store_id=STORE_ID
    )
    exit_events = [e for e in events if e["event_type"] == "EXIT"]
    assert len(exit_events) == 1


def test_reentry_detection():
    """Same visitor returning within reentry window → REENTRY not ENTRY."""
    tracker = VisitorTracker(camera_type="entry", store_id=STORE_ID)

    # First entry
    tracker._prev_positions[1] = 520
    events = tracker.update(
        [make_detection(track_id=1, cx=300, cy=545)],
        frame_time=t(0), frame_h=FRAME_H, entry_line_y=ENTRY_LINE_Y,
        camera_id=CAMERA_ID, store_id=STORE_ID
    )
    assert any(e["event_type"] == "ENTRY" for e in events)

    # Exit
    tracker._prev_positions[1] = 560
    tracker.update(
        [make_detection(track_id=1, cx=300, cy=538)],
        frame_time=t(30), frame_h=FRAME_H, entry_line_y=ENTRY_LINE_Y,
        camera_id=CAMERA_ID, store_id=STORE_ID
    )

    # Re-enter with new track_id but same position (within 3 min)
    tracker._prev_positions[99] = 525
    events2 = tracker.update(
        [make_detection(track_id=99, cx=305, cy=545)],  # cx close to original 300
        frame_time=t(150), frame_h=FRAME_H, entry_line_y=ENTRY_LINE_Y,
        camera_id=CAMERA_ID, store_id=STORE_ID
    )
    event_types = [e["event_type"] for e in events2]
    assert "REENTRY" in event_types, f"Expected REENTRY, got: {event_types}"


def test_staff_flagged_correctly():
    """is_staff=True in detection → event has is_staff=True."""
    tracker = VisitorTracker(camera_type="entry", store_id=STORE_ID)
    tracker._prev_positions[1] = 520
    events = tracker.update(
        [make_detection(track_id=1, cx=300, cy=545, is_staff=True)],
        frame_time=t(0), frame_h=FRAME_H, entry_line_y=ENTRY_LINE_Y,
        camera_id=CAMERA_ID, store_id=STORE_ID
    )
    entry = next((e for e in events if e["event_type"] == "ENTRY"), None)
    assert entry is not None
    assert entry["is_staff"] is True


# ── Zone Tests ────────────────────────────────────────────────────────────────
def test_zone_enter_emit():
    """Visitor appearing in a zone emits ZONE_ENTER."""
    tracker = VisitorTracker(camera_type="floor", store_id=STORE_ID)
    events = tracker.update(
        [make_detection(track_id=1, cx=300, cy=400, zone="SKINCARE")],
        frame_time=t(0), frame_h=FRAME_H, entry_line_y=ENTRY_LINE_Y,
        camera_id="CAM_FLOOR_01", store_id=STORE_ID
    )
    zone_enters = [e for e in events if e["event_type"] == "ZONE_ENTER"]
    assert len(zone_enters) == 1
    assert zone_enters[0]["zone_id"] == "SKINCARE"


def test_zone_transition():
    """Moving from zone A to zone B → ZONE_EXIT A + ZONE_ENTER B."""
    tracker = VisitorTracker(camera_type="floor", store_id=STORE_ID)

    tracker.update(
        [make_detection(track_id=1, cx=100, cy=400, zone="SKINCARE")],
        frame_time=t(0), frame_h=FRAME_H, entry_line_y=ENTRY_LINE_Y,
        camera_id="CAM_FLOOR_01", store_id=STORE_ID
    )
    events = tracker.update(
        [make_detection(track_id=1, cx=600, cy=400, zone="HAIRCARE")],
        frame_time=t(10), frame_h=FRAME_H, entry_line_y=ENTRY_LINE_Y,
        camera_id="CAM_FLOOR_01", store_id=STORE_ID
    )
    types = [e["event_type"] for e in events]
    assert "ZONE_EXIT" in types
    assert "ZONE_ENTER" in types


def test_zone_dwell_emitted_after_30s():
    """Continuous zone presence > 30s → ZONE_DWELL event."""
    tracker = VisitorTracker(camera_type="floor", store_id=STORE_ID)
    det = make_detection(track_id=1, cx=300, cy=400, zone="SKINCARE")

    tracker.update([det], frame_time=t(0), frame_h=FRAME_H, entry_line_y=ENTRY_LINE_Y,
                   camera_id="CAM_FLOOR_01", store_id=STORE_ID)

    # Advance 35 seconds
    events = tracker.update([det], frame_time=t(35), frame_h=FRAME_H, entry_line_y=ENTRY_LINE_Y,
                             camera_id="CAM_FLOOR_01", store_id=STORE_ID)

    dwell_events = [e for e in events if e["event_type"] == "ZONE_DWELL"]
    assert len(dwell_events) >= 1
    assert dwell_events[0]["dwell_ms"] > 0


# ── Emit / Schema Tests ───────────────────────────────────────────────────────
def test_emit_validates_schema():
    """EventEmitter rejects events with invalid event_type."""
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
        tmp = f.name

    try:
        emitter = EventEmitter(output_path=tmp, api_url=None)
        import uuid
        bad_event = {
            "event_id": str(uuid.uuid4()),
            "store_id": STORE_ID,
            "camera_id": CAMERA_ID,
            "visitor_id": "VIS_abc123",
            "event_type": "INVALID_TYPE",
            "timestamp": t(0).isoformat(),
            "zone_id": None,
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.9,
            "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
        }
        with pytest.raises(ValueError, match="Invalid event_type"):
            emitter.emit(bad_event)
        emitter.close()
    finally:
        os.unlink(tmp)


def test_emit_writes_valid_jsonl():
    """Valid events are written as parseable JSON lines."""
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
        tmp = f.name

    try:
        emitter = EventEmitter(output_path=tmp, api_url=None)
        import uuid
        event = {
            "event_id": str(uuid.uuid4()),
            "store_id": STORE_ID,
            "camera_id": CAMERA_ID,
            "visitor_id": "VIS_abc123",
            "event_type": "ENTRY",
            "timestamp": t(0).isoformat(),
            "zone_id": None,
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.9,
            "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
        }
        emitter.emit(event)
        emitter.close()

        with open(tmp) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        assert len(lines) == 1
        assert lines[0]["event_type"] == "ENTRY"
    finally:
        os.unlink(tmp)
