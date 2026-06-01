"""
emit.py — Event schema validation + emission.
Writes events to a JSONL file and optionally POSTs batches to the API.
"""

from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Optional HTTP posting
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

BATCH_SIZE = 100  # POST to API every N events


class EventEmitter:
    def __init__(self, output_path: str, api_url: Optional[str] = None):
        self.output_path = output_path
        self.api_url = api_url.rstrip("/") if api_url else None
        self._file = open(output_path, "w", encoding="utf-8")
        self._batch: list[dict] = []
        self._total = 0

    def emit(self, event: dict) -> None:
        """Validate, write to JSONL, buffer for API POST."""
        validated = self._validate(event)
        self._file.write(json.dumps(validated) + "\n")
        self._file.flush()
        self._batch.append(validated)
        self._total += 1

        if len(self._batch) >= BATCH_SIZE:
            self._flush_to_api()

    def close(self) -> None:
        self._flush_to_api()
        self._file.close()
        print(f"EventEmitter: {self._total} total events written")

    def _flush_to_api(self) -> None:
        if not self._batch or not self.api_url or not HTTPX_AVAILABLE:
            self._batch = []
            return

        try:
            resp = httpx.post(
                f"{self.api_url}/events/ingest",
                json={"events": self._batch},
                timeout=10.0,
            )
            resp.raise_for_status()
            result = resp.json()
            print(
                f"API ingest: accepted={result.get('accepted', 0)} "
                f"duplicate={result.get('duplicate', 0)} "
                f"rejected={result.get('rejected', 0)}"
            )
        except Exception as exc:
            print(f"WARNING: Failed to POST events to API: {exc}")

        self._batch = []

    @staticmethod
    def _validate(event: dict) -> dict:
        """Ensure required fields are present and types are correct."""
        required = [
            "event_id", "store_id", "camera_id", "visitor_id",
            "event_type", "timestamp", "confidence",
        ]
        for field in required:
            if field not in event:
                raise ValueError(f"Missing required field: {field}")

        valid_types = {
            "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
            "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY",
        }
        if event["event_type"] not in valid_types:
            raise ValueError(f"Invalid event_type: {event['event_type']}")

        # Ensure event_id is UUID
        try:
            uuid.UUID(str(event["event_id"]))
        except ValueError:
            event["event_id"] = str(uuid.uuid4())

        # Ensure timestamp is ISO-8601 string
        ts = event.get("timestamp")
        if isinstance(ts, datetime):
            event["timestamp"] = ts.isoformat()

        # Ensure confidence is float in [0, 1]
        event["confidence"] = max(0.0, min(1.0, float(event.get("confidence", 0.5))))

        # Ensure metadata structure
        if "metadata" not in event:
            event["metadata"] = {"queue_depth": None, "sku_zone": None, "session_seq": 0}

        return event


def load_events_from_jsonl(path: str) -> list[dict]:
    """Load events from a JSONL file (for replay / testing)."""
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events
