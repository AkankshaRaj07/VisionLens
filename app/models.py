from __future__ import annotations
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator
import uuid

# ── Event Types ──────────────────────────────────────────────────────────────
VALID_EVENT_TYPES = {
    "ENTRY",
    "EXIT",
    "ZONE_ENTER",
    "ZONE_EXIT",
    "ZONE_DWELL",
    "BILLING_QUEUE_JOIN",
    "BILLING_QUEUE_ABANDON",
    "REENTRY",
}

ANOMALY_SEVERITIES = {"INFO", "WARN", "CRITICAL"}


# ── Inbound Event Schema (matches challenge spec exactly) ────────────────────
class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone: Optional[str] = None
    session_seq: int = 0


class StoreEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: datetime
    zone_id: Optional[str] = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = Field(..., ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        if v not in VALID_EVENT_TYPES:
            raise ValueError(f"Invalid event_type: {v}. Must be one of {VALID_EVENT_TYPES}")
        return v

    @field_validator("event_id")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        try:
            uuid.UUID(v)
        except ValueError:
            raise ValueError("event_id must be a valid UUID v4")
        return v


# ── Ingest Request / Response ────────────────────────────────────────────────
class IngestRequest(BaseModel):
    events: List[dict] = Field(..., max_length=500)


class IngestResult(BaseModel):
    accepted: int
    rejected: int
    duplicate: int
    errors: List[dict] = []


# ── API Response Models ──────────────────────────────────────────────────────
class ZoneDwell(BaseModel):
    zone_id: str
    avg_dwell_ms: float
    visit_count: int


class StoreMetrics(BaseModel):
    store_id: str
    unique_visitors: int
    conversion_rate: float
    avg_dwell_per_zone: List[ZoneDwell]
    queue_depth: int
    abandonment_rate: float
    window_start: datetime
    window_end: datetime


class FunnelStage(BaseModel):
    stage: str
    count: int
    drop_off_pct: float


class StoreFunnel(BaseModel):
    store_id: str
    stages: List[FunnelStage]
    window_start: datetime
    window_end: datetime


class HeatmapZone(BaseModel):
    zone_id: str
    visit_frequency: int
    avg_dwell_ms: float
    normalised_score: float  # 0–100
    data_confidence: bool  # False if < 20 sessions


class StoreHeatmap(BaseModel):
    store_id: str
    zones: List[HeatmapZone]
    generated_at: datetime


class Anomaly(BaseModel):
    anomaly_id: str
    anomaly_type: str
    severity: str
    description: str
    suggested_action: str
    detected_at: datetime
    metadata: dict = {}


class StoreAnomalies(BaseModel):
    store_id: str
    anomalies: List[Anomaly]
    checked_at: datetime


class StoreHealth(BaseModel):
    store_id: str
    status: str
    last_event_timestamp: Optional[datetime]
    feed_status: str  # OK | STALE_FEED
    event_count_24h: int


class HealthResponse(BaseModel):
    service: str = "store-intelligence-api"
    status: str
    stores: List[StoreHealth]
    checked_at: datetime
