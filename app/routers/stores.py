from datetime import datetime, timezone, timedelta
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct, and_

from app.database import get_db, EventRecord
from app.models import (
    StoreMetrics, ZoneDwell,
    StoreFunnel, FunnelStage,
    StoreHeatmap, HeatmapZone,
    StoreAnomalies, Anomaly,
)

router = APIRouter(prefix="/stores", tags=["stores"])

CUSTOMER_FILTER = EventRecord.is_staff == False  # noqa: E712
CONVERSION_WINDOW_MINUTES = 5


# ── Helper: validate store exists (has any events) ───────────────────────────
async def _require_store(store_id: str, db: AsyncSession):
    result = await db.execute(
        select(func.count()).where(EventRecord.store_id == store_id)
    )
    if result.scalar() == 0:
        raise HTTPException(status_code=404, detail=f"Store {store_id} not found")


# ── GET /stores/{id}/metrics ─────────────────────────────────────────────────
@router.get("/{store_id}/metrics", response_model=StoreMetrics)
async def get_metrics(store_id: str, db: AsyncSession = Depends(get_db)):
    await _require_store(store_id, db)

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    base = and_(
        EventRecord.store_id == store_id,
        CUSTOMER_FILTER,
        EventRecord.timestamp >= today_start,
    )

    # Unique visitors (unique visitor_ids with ENTRY event today)
    uv_result = await db.execute(
        select(func.count(distinct(EventRecord.visitor_id)))
        .where(and_(base, EventRecord.event_type == "ENTRY"))
    )
    unique_visitors = uv_result.scalar() or 0

    # Converted visitors: those who had a BILLING_QUEUE_JOIN without ABANDON
    bq_result = await db.execute(
        select(distinct(EventRecord.visitor_id))
        .where(and_(base, EventRecord.event_type == "BILLING_QUEUE_JOIN"))
    )
    billing_visitors = set(r[0] for r in bq_result.fetchall())

    ab_result = await db.execute(
        select(distinct(EventRecord.visitor_id))
        .where(and_(base, EventRecord.event_type == "BILLING_QUEUE_ABANDON"))
    )
    abandoned_visitors = set(r[0] for r in ab_result.fetchall())

    converted = len(billing_visitors - abandoned_visitors)
    conversion_rate = round(converted / unique_visitors, 4) if unique_visitors > 0 else 0.0

    # Avg dwell per zone
    dwell_result = await db.execute(
        select(
            EventRecord.zone_id,
            func.avg(EventRecord.dwell_ms).label("avg_dwell"),
            func.count(EventRecord.event_id).label("visits"),
        )
        .where(and_(base, EventRecord.event_type == "ZONE_DWELL", EventRecord.zone_id != None))
        .group_by(EventRecord.zone_id)
    )
    zone_dwells = [
        ZoneDwell(zone_id=row.zone_id, avg_dwell_ms=round(row.avg_dwell, 2), visit_count=row.visits)
        for row in dwell_result.fetchall()
    ]

    # Current queue depth (latest BILLING_QUEUE_JOIN queue_depth value)
    queue_result = await db.execute(
        select(EventRecord.queue_depth)
        .where(and_(
            EventRecord.store_id == store_id,
            EventRecord.event_type == "BILLING_QUEUE_JOIN",
            EventRecord.queue_depth != None,
        ))
        .order_by(EventRecord.timestamp.desc())
        .limit(1)
    )
    queue_row = queue_result.fetchone()
    queue_depth = queue_row[0] if queue_row else 0

    # Abandonment rate
    abandonment_rate = (
        round(len(abandoned_visitors) / len(billing_visitors), 4)
        if billing_visitors else 0.0
    )

    return StoreMetrics(
        store_id=store_id,
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_per_zone=zone_dwells,
        queue_depth=queue_depth,
        abandonment_rate=abandonment_rate,
        window_start=today_start,
        window_end=now,
    )


# ── GET /stores/{id}/funnel ──────────────────────────────────────────────────
@router.get("/{store_id}/funnel", response_model=StoreFunnel)
async def get_funnel(store_id: str, db: AsyncSession = Depends(get_db)):
    await _require_store(store_id, db)

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    base = and_(
        EventRecord.store_id == store_id,
        CUSTOMER_FILTER,
        EventRecord.timestamp >= today_start,
    )

    async def count_unique_visitors(event_types: list[str]) -> int:
        result = await db.execute(
            select(func.count(distinct(EventRecord.visitor_id)))
            .where(and_(base, EventRecord.event_type.in_(event_types)))
        )
        return result.scalar() or 0

    entries = await count_unique_visitors(["ENTRY", "REENTRY"])
    zone_visits = await count_unique_visitors(["ZONE_ENTER", "ZONE_DWELL"])
    billing_joins = await count_unique_visitors(["BILLING_QUEUE_JOIN"])
    # Purchases = billing joins who did NOT abandon
    ab_result = await db.execute(
        select(distinct(EventRecord.visitor_id))
        .where(and_(base, EventRecord.event_type == "BILLING_QUEUE_ABANDON"))
    )
    abandoned = set(r[0] for r in ab_result.fetchall())
    bq_result = await db.execute(
        select(distinct(EventRecord.visitor_id))
        .where(and_(base, EventRecord.event_type == "BILLING_QUEUE_JOIN"))
    )
    billing_set = set(r[0] for r in bq_result.fetchall())
    purchases = len(billing_set - abandoned)

    def drop_off(current: int, previous: int) -> float:
        if previous == 0:
            return 0.0
        return round((1 - current / previous) * 100, 2)

    stages = [
        FunnelStage(stage="Entry", count=entries, drop_off_pct=0.0),
        FunnelStage(stage="Zone Visit", count=zone_visits, drop_off_pct=drop_off(zone_visits, entries)),
        FunnelStage(stage="Billing Queue", count=billing_joins, drop_off_pct=drop_off(billing_joins, zone_visits)),
        FunnelStage(stage="Purchase", count=purchases, drop_off_pct=drop_off(purchases, billing_joins)),
    ]

    return StoreFunnel(store_id=store_id, stages=stages, window_start=today_start, window_end=now)


# ── GET /stores/{id}/heatmap ─────────────────────────────────────────────────
@router.get("/{store_id}/heatmap", response_model=StoreHeatmap)
async def get_heatmap(store_id: str, db: AsyncSession = Depends(get_db)):
    await _require_store(store_id, db)

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    result = await db.execute(
        select(
            EventRecord.zone_id,
            func.count(EventRecord.event_id).label("freq"),
            func.avg(EventRecord.dwell_ms).label("avg_dwell"),
            func.count(distinct(EventRecord.visitor_id)).label("unique_sessions"),
        )
        .where(and_(
            EventRecord.store_id == store_id,
            CUSTOMER_FILTER,
            EventRecord.timestamp >= today_start,
            EventRecord.zone_id != None,
            EventRecord.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
        ))
        .group_by(EventRecord.zone_id)
    )
    rows = result.fetchall()

    if not rows:
        return StoreHeatmap(store_id=store_id, zones=[], generated_at=now)

    max_freq = max(r.freq for r in rows) or 1

    zones = [
        HeatmapZone(
            zone_id=row.zone_id,
            visit_frequency=row.freq,
            avg_dwell_ms=round(row.avg_dwell or 0, 2),
            normalised_score=round((row.freq / max_freq) * 100, 2),
            data_confidence=row.unique_sessions >= 20,
        )
        for row in rows
    ]

    return StoreHeatmap(store_id=store_id, zones=zones, generated_at=now)


# ── GET /stores/{id}/anomalies ───────────────────────────────────────────────
@router.get("/{store_id}/anomalies", response_model=StoreAnomalies)
async def get_anomalies(store_id: str, db: AsyncSession = Depends(get_db)):
    await _require_store(store_id, db)

    now = datetime.now(timezone.utc)
    anomalies = []

    # 1. Queue spike: current queue depth > 5
    queue_result = await db.execute(
        select(EventRecord.queue_depth)
        .where(and_(
            EventRecord.store_id == store_id,
            EventRecord.event_type == "BILLING_QUEUE_JOIN",
            EventRecord.queue_depth != None,
        ))
        .order_by(EventRecord.timestamp.desc())
        .limit(1)
    )
    queue_row = queue_result.fetchone()
    current_queue = queue_row[0] if queue_row else 0

    if current_queue >= 8:
        anomalies.append(Anomaly(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="BILLING_QUEUE_SPIKE",
            severity="CRITICAL",
            description=f"Billing queue depth is {current_queue} — critically high",
            suggested_action="Open additional billing counters immediately",
            detected_at=now,
            metadata={"queue_depth": current_queue},
        ))
    elif current_queue >= 5:
        anomalies.append(Anomaly(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="BILLING_QUEUE_SPIKE",
            severity="WARN",
            description=f"Billing queue depth is {current_queue}",
            suggested_action="Consider opening an additional billing counter",
            detected_at=now,
            metadata={"queue_depth": current_queue},
        ))

    # 2. Dead zone: no visits in any zone in last 30 minutes
    thirty_min_ago = now - timedelta(minutes=30)
    dead_zone_result = await db.execute(
        select(EventRecord.zone_id)
        .where(and_(
            EventRecord.store_id == store_id,
            CUSTOMER_FILTER,
            EventRecord.timestamp >= thirty_min_ago,
            EventRecord.zone_id != None,
            EventRecord.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
        ))
        .distinct()
    )
    active_zones = set(r[0] for r in dead_zone_result.fetchall())

    # Get all zones that had activity today
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    all_zones_result = await db.execute(
        select(distinct(EventRecord.zone_id))
        .where(and_(
            EventRecord.store_id == store_id,
            EventRecord.timestamp >= today_start,
            EventRecord.zone_id != None,
        ))
    )
    all_zones = set(r[0] for r in all_zones_result.fetchall())
    dead_zones = all_zones - active_zones

    for zone in dead_zones:
        anomalies.append(Anomaly(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="DEAD_ZONE",
            severity="INFO",
            description=f"Zone {zone} has had no customer visits in the last 30 minutes",
            suggested_action=f"Check if zone {zone} display or signage needs attention",
            detected_at=now,
            metadata={"zone_id": zone},
        ))

    # 3. Conversion drop: today vs 7-day avg
    seven_days_ago = now - timedelta(days=7)

    async def get_conversion_rate(start: datetime, end: datetime) -> float:
        uv = await db.execute(
            select(func.count(distinct(EventRecord.visitor_id)))
            .where(and_(
                EventRecord.store_id == store_id,
                CUSTOMER_FILTER,
                EventRecord.event_type == "ENTRY",
                EventRecord.timestamp.between(start, end),
            ))
        )
        total = uv.scalar() or 0
        if total == 0:
            return 0.0
        cv = await db.execute(
            select(func.count(distinct(EventRecord.visitor_id)))
            .where(and_(
                EventRecord.store_id == store_id,
                CUSTOMER_FILTER,
                EventRecord.event_type == "BILLING_QUEUE_JOIN",
                EventRecord.timestamp.between(start, end),
            ))
        )
        converted = cv.scalar() or 0
        return converted / total

    today_rate = await get_conversion_rate(today_start, now)
    hist_rate = await get_conversion_rate(seven_days_ago, today_start)

    if hist_rate > 0 and today_rate < hist_rate * 0.7:
        drop_pct = round((1 - today_rate / hist_rate) * 100, 1)
        anomalies.append(Anomaly(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="CONVERSION_DROP",
            severity="WARN",
            description=f"Conversion rate dropped {drop_pct}% vs 7-day average ({today_rate:.1%} vs {hist_rate:.1%})",
            suggested_action="Review zone heatmap for drop-off points and check billing queue abandonment",
            detected_at=now,
            metadata={"today_rate": today_rate, "hist_rate": hist_rate, "drop_pct": drop_pct},
        ))

    return StoreAnomalies(store_id=store_id, anomalies=anomalies, checked_at=now)
