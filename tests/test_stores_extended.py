# PROMPT: Write additional pytest tests for the stores.py router to push statement coverage above 70%. Focus on the /heatmap endpoint and the DEAD_ZONE and CONVERSION_DROP anomalies which were missing from the previous test suite.
# CHANGES MADE: Added fixtures to backdate events in the SQLite database to trigger the 7-day conversion drop logic and the 30-minute dead zone logic.

import pytest
import uuid
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
async def test_heatmap_generation(client):
    """Test the /heatmap endpoint returns valid normalized scores and dwell averages."""
    events = [
        make_event(event_type="ZONE_ENTER", zone_id="SKINCARE"),
        make_event(event_type="ZONE_DWELL", zone_id="SKINCARE", dwell_ms=10000),
        make_event(event_type="ZONE_ENTER", zone_id="MAKEUP"),
    ]
    async with client as c:
        await c.post("/events/ingest", json={"events": events})
        resp = await c.get("/stores/STORE_BLR_002/heatmap")
        
    assert resp.status_code == 200
    zones = resp.json()["zones"]
    assert len(zones) >= 2
    
    skincare = next(z for z in zones if z["zone_id"] == "SKINCARE")
    makeup = next(z for z in zones if z["zone_id"] == "MAKEUP")
    
    # SKINCARE had 2 events, MAKEUP had 1. 
    # SKINCARE freq=2, normalise=100.0
    assert skincare["visit_frequency"] == 2
    assert skincare["normalised_score"] == 100.0
    assert makeup["visit_frequency"] == 1
    assert makeup["normalised_score"] == 50.0


@pytest.mark.asyncio
async def test_anomaly_dead_zone(client):
    """Test DEAD_ZONE anomaly triggers when a zone has no visits in 30 mins."""
    now = datetime.now(timezone.utc)
    # 40 mins ago
    old_ts = now - timedelta(minutes=40)
    
    events = [
        make_event(event_type="ZONE_ENTER", zone_id="SKINCARE", timestamp=old_ts),
        make_event(event_type="ZONE_ENTER", zone_id="MAKEUP", timestamp=now),
    ]
    async with client as c:
        await c.post("/events/ingest", json={"events": events})
        resp = await c.get("/stores/STORE_BLR_002/anomalies")
        
    assert resp.status_code == 200
    anomalies = resp.json()["anomalies"]
    types = [a["anomaly_type"] for a in anomalies]
    
    # SKINCARE had a visit today, but >30 mins ago. MAKEUP just had one.
    assert "DEAD_ZONE" in types
    dead = next(a for a in anomalies if a["anomaly_type"] == "DEAD_ZONE")
    assert dead["metadata"]["zone_id"] == "SKINCARE"


@pytest.mark.asyncio
async def test_anomaly_conversion_drop(client):
    """Test CONVERSION_DROP anomaly triggers when today's rate is < 70% of 7-day average."""
    now = datetime.now(timezone.utc)
    five_days_ago = now - timedelta(days=5)
    
    # Historical: 2 entries, 2 purchases (100% conversion)
    hist_events = [
        make_event(visitor_id="VIS_H1", event_type="ENTRY", timestamp=five_days_ago),
        make_event(visitor_id="VIS_H1", event_type="BILLING_QUEUE_JOIN", timestamp=five_days_ago),
        make_event(visitor_id="VIS_H2", event_type="ENTRY", timestamp=five_days_ago),
        make_event(visitor_id="VIS_H2", event_type="BILLING_QUEUE_JOIN", timestamp=five_days_ago),
    ]
    
    # Today: 10 entries, 0 purchases (0% conversion)
    today_events = [
        make_event(visitor_id=f"VIS_T{i}", event_type="ENTRY", timestamp=now)
        for i in range(10)
    ]
    
    async with client as c:
        await c.post("/events/ingest", json={"events": hist_events + today_events})
        resp = await c.get("/stores/STORE_BLR_002/anomalies")
        
    assert resp.status_code == 200
    anomalies = resp.json()["anomalies"]
    types = [a["anomaly_type"] for a in anomalies]
    
    assert "CONVERSION_DROP" in types
    drop = next(a for a in anomalies if a["anomaly_type"] == "CONVERSION_DROP")
    assert drop["severity"] == "WARN"
