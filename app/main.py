import time
import uuid
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, HTMLResponse
from pythonjsonlogger import jsonlogger

from app.database import init_db
from app.routers import events, stores, health

# ── Structured JSON logger ──────────────────────────────────────────────────
logger = logging.getLogger("store_intelligence")
handler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter(
    fmt="%(asctime)s %(levelname)s %(name)s %(message)s"
)
handler.setFormatter(formatter)
logger.addHandler(handler)
logger.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("Database initialised")
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="Store Intelligence API",
    version="1.0.0",
    description="Real-time retail analytics from CCTV event streams",
    lifespan=lifespan,
)


# ── Request logging middleware ───────────────────────────────────────────────
from sqlalchemy.exc import OperationalError

@app.middleware("http")
async def log_requests(request: Request, call_next):
    trace_id = str(uuid.uuid4())
    request.state.trace_id = trace_id
    start = time.perf_counter()

    try:
        response: Response = await call_next(request)
    except OperationalError as exc:
        logger.error(
            "Database unavailable",
            extra={
                "trace_id": trace_id,
                "path": request.url.path,
                "error": str(exc),
            },
        )
        return JSONResponse(
            status_code=503,
            content={"error": "service_unavailable", "message": "Database connection failed", "trace_id": trace_id},
        )
    except Exception as exc:
        logger.error(
            "Unhandled exception",
            extra={
                "trace_id": trace_id,
                "path": request.url.path,
                "error": str(exc),
            },
        )
        return JSONResponse(
            status_code=500,
            content={"error": "internal_server_error", "trace_id": trace_id},
        )

    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    store_id = request.path_params.get("store_id", None)

    logger.info(
        "request",
        extra={
            "trace_id": trace_id,
            "store_id": store_id,
            "endpoint": request.url.path,
            "method": request.method,
            "latency_ms": latency_ms,
            "status_code": response.status_code,
        },
    )
    response.headers["X-Trace-Id"] = trace_id
    return response


# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(events.router)
app.include_router(stores.router)

import os
@app.get("/dashboard", response_class=HTMLResponse, tags=["ui"])
async def serve_dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
    try:
        with open(dashboard_path, "r") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>Dashboard UI not found</h1>", status_code=404)
