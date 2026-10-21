"""FastAPI application.

Endpoints
---------
POST /ask       ask a free-form question, get a grounded recommendation
GET  /health    liveness, model status, per-source status
GET  /context   debug: the evidence bundle without the SLM in the loop
GET  /sources   data sources, versions and attributions
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.config import settings
from app.context import Intent, gather_context
from app.exposure_engine import ExposureEngine
from app.pipeline import pipeline
from app.slm import slm
from app.sources.base import app_cache

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger("geoai.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the model at start-up so the first request of the judging window
    # does not pay for it (PRD success criterion S6).
    if settings.SLM_EAGER_LOAD:
        ok = await __import__("asyncio").get_running_loop().run_in_executor(
            None, slm.load
        )
        logger.info("Model preloaded: %s", ok)
    yield


app = FastAPI(
    title="GeoAI Outdoor Assistant",
    version="1.0.0",
    description=(
        "Recommends where and when to head outdoors, grounded in live weather, "
        "air-quality and OpenStreetMap data. © OpenStreetMap contributors."
    ),
    lifespan=lifespan,
)

# The frontend team calls this API from a browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=2, max_length=2000)
    query: str | None = None
    lat: float | None = None
    lon: float | None = None
    debug: bool = False

    def text(self) -> str:
        return (self.query or self.question).strip()


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model": slm.status(),
        "cache_entries": len(app_cache),
        "timestamp": time.time(),
        "timezone": settings.TIMEZONE,
    }


@app.get("/sources")
async def sources() -> dict[str, Any]:
    return {
        "sources": pipeline._sources(),
        "attribution": (
            "© OpenStreetMap contributors (Overpass API, Nominatim) | Open-Meteo | "
            "CAMS ENSEMBLE data (Copernicus/ECMWF) | WAQI | OSRM | Astral"
        ),
        "model": slm.status(),
        "disclosure": (
            "The running assistant uses only a locally hosted open-weights model "
            f"({settings.SLM_BASE_MODEL}, 2.03B parameters, Apache-2.0). No hosted "
            "large language model is called at request time. AI coding assistants "
            "were used to write the code."
        ),
    }


@app.post("/ask")
async def ask(request: AskRequest) -> dict[str, Any]:
    question = request.text()
    if not question:
        return {"status": "needs_clarification", "answer": "Please ask a question."}
    try:
        result = await pipeline.run(question, lat=request.lat, lon=request.lon)
    except Exception as exc:
        logger.exception("Unhandled error in /ask")
        return {
            "status": "partial",
            "answer": (
                "I could not complete that request because of an internal error. "
                "The live data services may be busy - please try again in a moment."
            ),
            "error": str(exc),
            "limitations": [f"internal error: {exc}"],
        }
    if not request.debug:
        result.pop("debug", None)
    return result


@app.get("/context")
async def get_context(
    location: str = Query(..., description="Location text to geocode"),
    activity: str = Query("running", description="Activity"),
    radius_km: float = Query(5.0, description="Search radius in km"),
    profile: str = Query("", description="Comma separated profiles"),
) -> dict[str, Any]:
    intent = Intent(
        activity=activity,
        location_text=location,
        radius_km=radius_km,
        profile=[p for p in profile.split(",") if p],
    )
    bundle = await gather_context(intent)
    if "error" in bundle:
        return bundle
    engine = ExposureEngine()
    result = engine.score(bundle)
    return {
        "origin": bundle["origin"].model_dump(),
        "venues": [v.model_dump() for v in bundle["venues"]],
        "hazards": len(bundle.get("hazards") or []),
        "window": {
            "start": bundle["window"].start.isoformat(),
            "end": bundle["window"].end.isoformat(),
            "label": bundle["window"].label,
        },
        "evidence": bundle["evidence"],
        "source_status": bundle["source_status"],
        "limitations": bundle["limitations"],
        "timings_ms": bundle["timings"],
        "top_slots": [s.model_dump() for s in result.slots[:8]],
        "avoid": result.avoid[:10],
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=7860)
