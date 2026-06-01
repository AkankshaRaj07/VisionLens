# PROMPT: Write unit tests directly testing the pipeline.tracker.py VisitorTracker class to push statement coverage significantly higher. Test both ENTRY and EXIT logic.
# CHANGES MADE: Added explicit tests for dwell time logic, entry threshold crossing, and flush behavior.

import pytest
from datetime import datetime, timezone, timedelta
from pipeline.tracker import VisitorTracker

def test_tracker_entry():
    tracker = VisitorTracker("entry", "STORE_BLR_002")
    now = datetime.now(timezone.utc)
    
    # Person is at top of frame (y=10)
    events = tracker.update(
        detections=[{"track_id": 1, "cx": 100, "cy": 10, "is_staff": False, "zone": None, "conf": 0.9}],
        frame_time=now,
        frame_h=480,
        entry_line_y=240,
        camera_id="CAM_ENTRY_01",
        store_id="STORE_BLR_002"
    )
    assert len(events) == 0
    
    # Person crosses entry line (y=300)
    now += timedelta(seconds=1)
    events = tracker.update(
        detections=[{"track_id": 1, "cx": 100, "cy": 300, "is_staff": False, "zone": None, "conf": 0.9}],
        frame_time=now,
        frame_h=480,
        entry_line_y=240,
        camera_id="CAM_ENTRY_01",
        store_id="STORE_BLR_002"
    )
    
    assert len(events) == 1
    assert events[0]["event_type"] == "ENTRY"

def test_tracker_flush():
    tracker = VisitorTracker("floor", "STORE_BLR_002")
    now = datetime.now(timezone.utc)
    
    # Person dwells in a zone
    from pipeline.tracker import VisitorSession
    session = VisitorSession("VIS_XYZ", 1, "STORE_BLR_002")
    session.current_zone = "SKINCARE"
    session.zone_entered_at = now - timedelta(seconds=45)
    tracker.active[1] = session
    
    events = tracker.flush(now, "CAM_01", "STORE_BLR_002")
    assert len(events) == 1
    assert events[0]["event_type"] == "ZONE_DWELL"
    assert events[0]["dwell_ms"] == 45000
