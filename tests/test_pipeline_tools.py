# PROMPT: Write pytest unit tests for the pipeline/replay.py and pipeline/correlate_pos.py scripts to push test coverage above 70%. Ensure we mock httpx.Client and requests so the tests run cleanly in CI without requiring real video files or a live API.
# CHANGES MADE: Added fixtures for temporary JSONL and CSV files to test file parsing logic. Patched sys.argv to test the main() function of correlate_pos.py.

import pytest
import json
import uuid
import tempfile
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from pipeline.replay import replay, load_all_events
from pipeline.correlate_pos import main as correlate_main


@pytest.fixture
def temp_events_dir():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        event = {
            "event_id": str(uuid.uuid4()),
            "store_id": "STORE_001",
            "camera_id": "CAM_01",
            "visitor_id": "VIS_01",
            "event_type": "BILLING_QUEUE_JOIN",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "zone_id": "BILLING_COUNTER",
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.9,
            "metadata": {"queue_depth": 1, "sku_zone": None, "session_seq": 1}
        }
        with open(p / "events1.jsonl", "w") as f:
            f.write(json.dumps(event) + "\n")
        yield str(p)


@pytest.fixture
def temp_pos_file():
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as f:
        f.write("store_id,transaction_id,timestamp,basket_value_inr,a,b,date,time,c,store\n")
        f.write(f"STORE_001,TXN_001,2026-03-03T14:38:12Z,100.0,a,b,03-03-2026,14:38:12,c,STORE_001\n")
        yield f.name


def test_load_all_events(temp_events_dir):
    events = load_all_events(temp_events_dir)
    assert len(events) == 1
    assert events[0]["event_type"] == "BILLING_QUEUE_JOIN"


@patch("pipeline.replay.httpx.Client")
def test_replay_events(mock_client_cls, temp_events_dir):
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    
    # Mock post response
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"accepted": 1, "rejected": 0, "duplicate": 0}
    mock_client.post.return_value = mock_resp

    # Should not crash
    replay(temp_events_dir, "http://localhost:8000", realtime=False)
    assert mock_client.post.called


@patch("pipeline.correlate_pos.requests.post")
def test_correlate_main(mock_post, temp_events_dir, temp_pos_file):
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_post.return_value = mock_resp
    
    test_args = ["correlate_pos.py", "--events-dir", temp_events_dir, "--pos-file", temp_pos_file, "--api-url", "http://localhost:8000"]
    with patch.object(sys, 'argv', test_args):
        # Process events (should not crash)
        correlate_main()
        
        # It should generate a BILLING_QUEUE_ABANDON event because the POS transaction timestamp
        # does not match the timestamp in our temp_events_dir (which uses datetime.now()).
        assert mock_post.called
