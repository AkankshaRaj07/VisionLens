from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text

from app.database import get_db, EventRecord
from app.models import HealthResponse, StoreHealth

router = APIRouter(tags=["health"])

STALE_THRESHOLD_MINUTES = 10


@router.get("/health", response_model=HealthResponse)
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Service health + per-store feed staleness check.
    Returns STALE_FEED if no events received in last 10 minutes for a store.
    """
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(minutes=STALE_THRESHOLD_MINUTES)

    try:
        # Get distinct stores and their last event timestamps
        result = await db.execute(
            select(
                EventRecord.store_id,
                func.max(EventRecord.timestamp).label("last_ts"),
                func.count(EventRecord.event_id).label("count_24h"),
            )
            .where(
                EventRecord.timestamp >= now - timedelta(hours=24)
            )
            .group_by(EventRecord.store_id)
        )
        rows = result.fetchall()

        stores = []
        for row in rows:
            last_ts = row.last_ts
            if last_ts and last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)

            feed_status = (
                "STALE_FEED"
                if last_ts is None or last_ts < stale_cutoff
                else "OK"
            )
            stores.append(
                StoreHealth(
                    store_id=row.store_id,
                    status="healthy",
                    last_event_timestamp=last_ts,
                    feed_status=feed_status,
                    event_count_24h=row.count_24h,
                )
            )

        return HealthResponse(
            status="healthy",
            stores=stores,
            checked_at=now,
        )

    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={
                "service": "store-intelligence-api",
                "status": "degraded",
                "error": "database_unavailable",
                "detail": str(exc),
                "checked_at": now.isoformat(),
            },
        )
