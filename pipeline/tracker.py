"""
tracker.py — Re-ID and tracking logic.

Assigns stable visitor_ids using track_id + trajectory similarity.
Handles:
- Re-entry detection (same person returning after EXIT)
- Group entry (individual track IDs per person from ByteTrack)
- Entry/exit direction (crossing the entry line)
- Dwell tracking per zone
"""

from __future__ import annotations
import uuid
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional


REENTRY_WINDOW_SECONDS = 300       # 5 min — re-entry within this = REENTRY event
REENTRY_POSITION_TOLERANCE = 0.15  # 15% of frame width for position matching
DWELL_EMIT_INTERVAL_SEC = 30
ZONE_DWELL_MIN_SEC = 5             # must be in zone this long to count as dwell


@dataclass
class VisitorSession:
    visitor_id: str
    track_id: int
    store_id: str
    entered_at: Optional[datetime] = None
    exited_at: Optional[datetime] = None
    current_zone: Optional[str] = None
    zone_entered_at: Optional[datetime] = None
    last_dwell_emitted_at: Optional[datetime] = None
    session_seq: int = 0
    is_staff: bool = False
    last_cx: int = 0                # last known centroid x (for re-entry matching)
    last_seen: Optional[datetime] = None
    active: bool = True

    def next_seq(self) -> int:
        self.session_seq += 1
        return self.session_seq


def make_visitor_id(track_id: int, store_id: str) -> str:
    """Deterministic visitor ID from track+store (stable within a clip)."""
    raw = f"{store_id}_{track_id}"
    h = hashlib.md5(raw.encode()).hexdigest()[:6]
    return f"VIS_{h}"


class VisitorTracker:
    def __init__(self, camera_type: str, store_id: str):
        self.camera_type = camera_type
        self.store_id = store_id

        # Active sessions keyed by track_id
        self.active: dict[int, VisitorSession] = {}

        # Recently exited sessions (for re-entry detection)
        self.exited: list[VisitorSession] = []

        # Tracks that crossed entry line going IN (cy < entry_line_y last frame)
        self._prev_positions: dict[int, int] = {}  # track_id → prev cy

    def _find_reentry(self, cx: int, frame_w: int, now: datetime) -> Optional[VisitorSession]:
        """
        Check if a new detection at cx matches a recently exited visitor.
        Match criteria: exited within REENTRY_WINDOW_SECONDS AND position similar.
        """
        cutoff = now - timedelta(seconds=REENTRY_WINDOW_SECONDS)
        for session in reversed(self.exited):
            if session.exited_at and session.exited_at < cutoff:
                continue
            position_diff = abs(session.last_cx - cx) / max(frame_w, 1)
            if position_diff < REENTRY_POSITION_TOLERANCE:
                return session
        return None

    def update(
        self,
        detections: list[dict],
        frame_time: datetime,
        frame_h: int,
        entry_line_y: int,
        camera_id: str,
        store_id: str,
    ) -> list[dict]:
        events = []
        current_track_ids = set()

        for det in detections:
            track_id = det["track_id"]
            cx, cy = det["cx"], det["cy"]
            conf = det["conf"]
            is_staff = det["is_staff"]
            zone = det["zone"]
            current_track_ids.add(track_id)

            prev_cy = self._prev_positions.get(track_id)

            if track_id not in self.active:
                # New track — determine if ENTRY or REENTRY
                if self.camera_type == "entry":
                    # Only emit ENTRY when person crosses line inbound (top→bottom)
                    if prev_cy is not None and prev_cy < entry_line_y <= cy:
                        reentry_session = self._find_reentry(cx, frame_h, frame_time)
                        if reentry_session:
                            # Resume session with REENTRY event
                            visitor_id = reentry_session.visitor_id
                            session = VisitorSession(
                                visitor_id=visitor_id,
                                track_id=track_id,
                                store_id=store_id,
                                entered_at=frame_time,
                                is_staff=is_staff,
                                last_cx=cx,
                                last_seen=frame_time,
                                active=True,
                            )
                            self.active[track_id] = session
                            events.append(self._make_event(
                                session, "REENTRY", frame_time, camera_id, conf, zone
                            ))
                        else:
                            visitor_id = make_visitor_id(track_id, store_id)
                            session = VisitorSession(
                                visitor_id=visitor_id,
                                track_id=track_id,
                                store_id=store_id,
                                entered_at=frame_time,
                                is_staff=is_staff,
                                last_cx=cx,
                                last_seen=frame_time,
                                active=True,
                            )
                            self.active[track_id] = session
                            events.append(self._make_event(
                                session, "ENTRY", frame_time, camera_id, conf, zone
                            ))
                else:
                    # Floor / billing camera — just start tracking, no ENTRY event
                    visitor_id = make_visitor_id(track_id, store_id)
                    session = VisitorSession(
                        visitor_id=visitor_id,
                        track_id=track_id,
                        store_id=store_id,
                        entered_at=frame_time,
                        is_staff=is_staff,
                        last_cx=cx,
                        last_seen=frame_time,
                    )
                    if zone:
                        session.current_zone = zone
                        session.zone_entered_at = frame_time
                        events.append(self._make_event(session, "ZONE_ENTER", frame_time, camera_id, conf, zone))
                    self.active[track_id] = session

            else:
                session = self.active[track_id]
                session.last_seen = frame_time
                session.last_cx = cx

                # EXIT: outbound crossing (bottom→top on entry camera)
                if self.camera_type == "entry":
                    if prev_cy is not None and prev_cy > entry_line_y >= cy:
                        session.exited_at = frame_time
                        session.active = False
                        events.append(self._make_event(
                            session, "EXIT", frame_time, camera_id, conf, zone
                        ))
                        self.exited.append(session)
                        del self.active[track_id]
                        self._prev_positions.pop(track_id, None)
                        continue

                # Zone tracking (floor/billing cameras)
                if self.camera_type != "entry" and zone:
                    if session.current_zone != zone:
                        # Zone exit
                        if session.current_zone:
                            events.append(self._make_event(
                                session, "ZONE_EXIT", frame_time, camera_id, conf,
                                session.current_zone
                            ))
                        # Zone enter
                        session.current_zone = zone
                        session.zone_entered_at = frame_time
                        session.last_dwell_emitted_at = None
                        events.append(self._make_event(
                            session, "ZONE_ENTER", frame_time, camera_id, conf, zone
                        ))
                    else:
                        # Continuous dwell
                        if session.zone_entered_at:
                            dwell_sec = (frame_time - session.zone_entered_at).total_seconds()
                            last_emit = session.last_dwell_emitted_at or session.zone_entered_at
                            since_last = (frame_time - last_emit).total_seconds()

                            if dwell_sec >= ZONE_DWELL_MIN_SEC and since_last >= DWELL_EMIT_INTERVAL_SEC:
                                dwell_ms = int(dwell_sec * 1000)
                                session.last_dwell_emitted_at = frame_time
                                ev = self._make_event(
                                    session, "ZONE_DWELL", frame_time, camera_id, conf, zone
                                )
                                ev["dwell_ms"] = dwell_ms
                                events.append(ev)

                # Billing queue logic
                if self.camera_type == "billing" and zone == "BILLING_COUNTER":
                    if det.get("queue_depth", 0) and det["queue_depth"] > 0:
                        ev = self._make_event(
                            session, "BILLING_QUEUE_JOIN", frame_time, camera_id, conf, zone
                        )
                        ev["metadata"]["queue_depth"] = det["queue_depth"]
                        events.append(ev)

            self._prev_positions[track_id] = cy

        # Detect tracks that disappeared (potential EXIT for non-entry cameras)
        disappeared = set(self.active.keys()) - current_track_ids
        for track_id in disappeared:
            session = self.active[track_id]
            if session.last_seen:
                gone_sec = (frame_time - session.last_seen).total_seconds()
                if gone_sec > 5:  # 5s grace period
                    if session.current_zone and self.camera_type != "entry":
                        events.append(self._make_event(
                            session, "ZONE_EXIT", frame_time, camera_id,
                            0.5, session.current_zone
                        ))
                    del self.active[track_id]

        return events

    def flush(self, frame_time: datetime, camera_id: str, store_id: str) -> list[dict]:
        """Emit final dwell events for all active sessions at end of clip."""
        events = []
        for session in list(self.active.values()):
            if session.current_zone and session.zone_entered_at:
                dwell_ms = int((frame_time - session.zone_entered_at).total_seconds() * 1000)
                if dwell_ms >= ZONE_DWELL_MIN_SEC * 1000:
                    ev = self._make_event(session, "ZONE_DWELL", frame_time, camera_id, 0.9, session.current_zone)
                    ev["dwell_ms"] = dwell_ms
                    events.append(ev)
        return events

    def _make_event(
        self,
        session: VisitorSession,
        event_type: str,
        frame_time: datetime,
        camera_id: str,
        conf: float,
        zone: Optional[str],
    ) -> dict:
        return {
            "event_id": str(uuid.uuid4()),
            "store_id": session.store_id,
            "camera_id": camera_id,
            "visitor_id": session.visitor_id,
            "event_type": event_type,
            "timestamp": frame_time.isoformat(),
            "zone_id": zone,
            "dwell_ms": 0,
            "is_staff": session.is_staff,
            "confidence": round(conf, 4),
            "metadata": {
                "queue_depth": None,
                "sku_zone": zone,
                "session_seq": session.next_seq(),
            },
        }
