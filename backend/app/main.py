"""AgriN node application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import (
    advisory, auth, basemap, diagnoses, federation, feedback, fields, readiness,
)
from app.config import get_settings
from app.db.session import SessionLocal, engine
from app.schemas import HealthResponse

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


settings = get_settings()

app = FastAPI(
    title="AgriN Node",
    version=VERSION,
    description=(
        "Interoperable digital agriculture node. Delivers localised agro-advisories "
        "from satellite, soil and weather data. Every recommended quantity is "
        "computed deterministically; language models are used only to translate "
        "and explain those numbers, never to produce them."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.agrin_env == "dev" else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(fields.router)
app.include_router(diagnoses.router)
app.include_router(advisory.router)
app.include_router(federation.router)
# RFC 8615: discovery lives at the origin root, not behind this node's prefix.
app.include_router(federation.well_known)
app.include_router(feedback.router)
app.include_router(basemap.router)
app.include_router(readiness.router)


@app.get("/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    database = "down"
    try:
        async with SessionLocal() as db:
            await db.execute(text("SELECT 1"))
            database = "up"
    except Exception as exc:  # noqa: BLE001 - health must report, not raise
        database = f"down: {type(exc).__name__}"

    return HealthResponse(
        status="ok" if database == "up" else "degraded",
        node_id=settings.node_id,
        country=settings.node_country,
        database=database,
        version=VERSION,
    )
