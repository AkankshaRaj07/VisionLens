import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text
from datetime import datetime, timezone

async def main():
    engine = create_async_engine('sqlite+aiosqlite:///./data/intelligence.db', echo=False)
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        res = await session.execute(text("SELECT COUNT(*) FROM events"))
        print(f"Total events: {res.scalar()}")
        
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        print(f"today_start: {today_start}")
        
        res = await session.execute(
            text("SELECT COUNT(*) FROM events WHERE timestamp >= :ts"),
            {"ts": today_start.isoformat()}
        )
        print(f"Events today (str check): {res.scalar()}")
        
        res = await session.execute(
            text("SELECT COUNT(*) FROM events WHERE timestamp >= :ts"),
            {"ts": today_start}
        )
        print(f"Events today (obj check): {res.scalar()}")
        
        res = await session.execute(text("SELECT timestamp FROM events LIMIT 1"))
        row = res.fetchone()
        if row:
            print(f"First timestamp: {row[0]} (Type: {type(row[0])})")

asyncio.run(main())
