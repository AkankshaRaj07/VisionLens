# PROMPT: Write pytest tests for the health.py router to test the health endpoint and specifically the STALE_FEED edge case.
# CHANGES MADE: Added tests to ensure a store with no events recently triggers STALE_FEED.

import pytest
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport

from app.main import app
from tests.test_api import make_event


from app.database import engine, Base

@pytest.fixture(autouse=True)
async def setup_db():
    import os
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_health_ok(client):
    """Test health endpoint with recent events."""
    now = datetime.now(timezone.utc)
    events = [make_event(timestamp=now)]
    
    async with client as c:
        await c.post("/events/ingest", json={"events": events})
        resp = await c.get("/health")
        
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    
    # Store should be OK
    store = next(s for s in body["stores"] if s["store_id"] == "STORE_BLR_002")
    assert store["feed_status"] == "OK"


@pytest.mark.asyncio
async def test_health_stale_feed(client):
    """Test health endpoint triggers STALE_FEED if last event > 10 mins ago."""
    old_ts = datetime.now(timezone.utc) - timedelta(minutes=15)
    events = [make_event(timestamp=old_ts)]
    
    async with client as c:
        await c.post("/events/ingest", json={"events": events})
        resp = await c.get("/health")
        
    assert resp.status_code == 200
    body = resp.json()
    
    # Store should be STALE_FEED
    store = next(s for s in body["stores"] if s["store_id"] == "STORE_BLR_002")
    assert store["feed_status"] == "STALE_FEED"
