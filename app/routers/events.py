import logging
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import get_db, EventRecord
from app.models import IngestRequest, IngestResult, StoreEvent

logger = logging.getLogger("store_intelligence")
router = APIRouter(prefix="/events", tags=["events"])


def _to_record(ev: StoreEvent) -> EventRecord:
    return EventRecord(
        event_id=ev.event_id,
        store_id=ev.store_id,
        camera_id=ev.camera_id,
        visitor_id=ev.visitor_id,
        event_type=ev.event_type,
        timestamp=ev.timestamp,
        zone_id=ev.zone_id,
        dwell_ms=ev.dwell_ms,
        is_staff=ev.is_staff,
        confidence=ev.confidence,
        queue_depth=ev.metadata.queue_depth,
        sku_zone=ev.metadata.sku_zone,
        session_seq=ev.metadata.session_seq,
    )


@router.post("/ingest", response_model=IngestResult)
async def ingest_events(
    payload: IngestRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Idempotent batch ingest — safe to call twice with the same payload.
    Deduplication is by event_id (primary key constraint).
    Partial success: malformed events are rejected, valid ones are stored.
    """
    accepted = 0
    rejected = 0
    duplicate = 0
    errors = []

    trace_id = getattr(request.state, "trace_id", "unknown")

    from pydantic import ValidationError
    for ev_dict in payload.events:
        try:
            ev = StoreEvent.model_validate(ev_dict)
            record = _to_record(ev)
            db.add(record)
            await db.flush()  # flush individually to catch per-row errors
            accepted += 1
        except ValidationError as exc:
            rejected += 1
            errors.append({"event_id": ev_dict.get("event_id", "unknown"), "error": "validation_error"})
        except IntegrityError:
            await db.rollback()
            duplicate += 1
        except Exception as exc:
            await db.rollback()
            rejected += 1
            errors.append({"event_id": ev_dict.get("event_id", "unknown"), "error": str(exc)})

    await db.commit()

    logger.info(
        "ingest_complete",
        extra={
            "trace_id": trace_id,
            "accepted": accepted,
            "rejected": rejected,
            "duplicate": duplicate,
            "event_count": len(payload.events),
        },
    )

    return IngestResult(
        accepted=accepted,
        rejected=rejected,
        duplicate=duplicate,
        errors=errors,
    )
