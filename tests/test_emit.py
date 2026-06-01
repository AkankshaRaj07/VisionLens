# PROMPT: Write unit tests directly testing the pipeline.emit.py EventEmitter class to push statement coverage significantly higher. Test both stdout emitting and the background API uploading thread.
# CHANGES MADE: Added explicit tests for event emitting, background flushing, and shutdown behavior.

import pytest
import time
import tempfile
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from pipeline.emit import EventEmitter

def test_emitter_stdout(tmp_path):
    output_path = tmp_path / "events.jsonl"
    emitter = EventEmitter(str(output_path), api_url=None)
    
    event = {
        "event_id": "12345678-1234-5678-1234-567812345678",
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_01",
        "visitor_id": "VIS_1",
        "event_type": "ENTRY",
        "timestamp": "2026-06-01T10:00:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.9,
        "metadata": {"session_seq": 1}
    }
    
    emitter.emit(event)
    emitter.close()
    
    with open(output_path, "r") as f:
        lines = f.readlines()
        assert len(lines) == 1
        written_event = json.loads(lines[0])
        assert written_event["event_id"] == "12345678-1234-5678-1234-567812345678"

@patch("pipeline.emit.httpx.post")
def test_emitter_api_upload(mock_post, tmp_path):
    mock_resp = MagicMock()
    mock_post.return_value = mock_resp
    
    output_path = tmp_path / "events2.jsonl"
    emitter = EventEmitter(str(output_path), api_url="http://test")
    
    event = {
        "event_id": "e2",
        "store_id": "STORE_BLR_002",
        "camera_id": "CAM_01",
        "visitor_id": "VIS_1",
        "event_type": "ENTRY",
        "timestamp": "2026-06-01T10:00:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.9,
        "metadata": {"session_seq": 1}
    }
    
    emitter.emit(event)
    # The background thread runs every 1 second, but close() triggers an immediate flush
    emitter.close()
    
    assert mock_post.called
