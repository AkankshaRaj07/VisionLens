#!/usr/bin/env python3
"""
replay.py — Replay events from JSONL files into the API.
Useful for testing the API with pre-processed events or for Part E real-time simulation.

Usage:
    python pipeline/replay.py \
        --events-dir data/events \
        --api-url http://localhost:8000 \
        [--realtime]  # simulate real-time playback with delays
"""

import argparse
import json
import time
import sys
from pathlib import Path
from datetime import datetime

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)

BATCH_SIZE = 100


def load_all_events(events_dir: str) -> list[dict]:
    events = []
    for path in sorted(Path(events_dir).glob("*.jsonl")):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
    events.sort(key=lambda e: e.get("timestamp", ""))
    return events


def post_batch(client: httpx.Client, api_url: str, batch: list[dict]) -> dict:
    resp = client.post(f"{api_url}/events/ingest", json={"events": batch}, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


def replay(events_dir: str, api_url: str, realtime: bool = False):
    events = load_all_events(events_dir)
    if not events:
        print(f"No events found in {events_dir}")
        return

    print(f"Loaded {len(events)} events from {events_dir}")
    total_accepted = 0
    batch = []
    prev_ts = None

    for i, event in enumerate(events):
        if realtime and prev_ts:
            curr_ts = event.get("timestamp", "")
            if curr_ts and prev_ts:
                try:
                    delta = (
                        datetime.fromisoformat(curr_ts.replace("Z", "+00:00")) -
                        datetime.fromisoformat(prev_ts.replace("Z", "+00:00"))
                    ).total_seconds()
                    # Cap delay at 2 seconds for simulation
                    if 0 < delta < 2:
                        time.sleep(delta)
                except Exception:
                    pass

        batch.append(event)
        prev_ts = event.get("timestamp")

        if len(batch) >= BATCH_SIZE or i == len(events) - 1:
            with httpx.Client() as client:
                try:
                    result = post_batch(client, api_url, batch)
                    total_accepted += result.get("accepted", 0)
                    print(
                        f"  Batch {i // BATCH_SIZE + 1}: "
                        f"accepted={result.get('accepted', 0)} "
                        f"dup={result.get('duplicate', 0)}"
                    )
                except Exception as exc:
                    print(f"  ERROR posting batch: {exc}")
            batch = []

    print(f"\n✅ Replay complete. {total_accepted}/{len(events)} events accepted.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--events-dir", default="data/events")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--realtime", action="store_true", help="Simulate real-time playback")
    args = parser.parse_args()

    replay(args.events_dir, args.api_url, args.realtime)


if __name__ == "__main__":
    main()
