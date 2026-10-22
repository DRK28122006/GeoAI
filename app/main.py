"""FastAPI application.

Endpoints
---------
POST /ask       ask a free-form question, get a grounded recommendation
GET  /health    liveness, model status, per-source status
GET  /context   debug: the evidence bundle without the SLM in the loop
GET  /sources   data sources, versions and attributions
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, slm.load)
        logger.info("Model preloaded: %s", ok)
        if not ok:
            # The service still starts and still answers, using the rule
            # based parser and the template, but /health reports it.
            logger.error("the model is not available: %s", slm.status().get("error"))
    try:
        yield
    finally:
        # Free the model before the process exits so the container stops
        # cleanly instead of being killed with the weights still mapped.
        slm.unload()
        try:
            app_cache.close()
        except Exception as exc:  # noqa: BLE001 - never block the shutdown
            logger.warning("the cache did not close cleanly: %s", exc)
        logger.info("Model released, shutting down")


app = FastAPI(
    title="GeoAI Outdoor Assistant",
    version="1.0.0",
    description=(
        "Recommends where and when to head outdoors, grounded in live weather, "
        "air-quality and OpenStreetMap data. © OpenStreetMap contributors."
    ),
    lifespan=lifespan,
)

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Tag every request so two concurrent ones can be told apart."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request %s failed", request_id)
        raise
    took = (time.perf_counter() - started) * 1000
    response.headers["x-request-id"] = request_id
    logger.info(
        "%s %s -> %s in %.0f ms (%s)",
        request.method,
        request.url.path,
        response.status_code,
        took,
        request_id,
    )
    return response


# The frontend team calls this API from a browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    """Either `question` or `query` may carry the text; `question` wins."""

    question: str | None = Field(default=None, min_length=2, max_length=2000)
    query: str | None = Field(default=None, min_length=2, max_length=2000)
    # An optional starting point. When it is given the geocoder is skipped.
    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lon: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    debug: bool = False

    def text(self) -> str:
        return (self.question or self.query or "").strip()


@app.get("/live")
async def live() -> dict[str, Any]:
    """Liveness. The process is up.

    This is what the container health check uses. It stays green even when
    the model is missing, because the assistant can still answer with the
    rule based parser and the templates, and taking the container out of
    the pool would be worse than a degraded answer.
    """
    return {"status": "ok", "timestamp": time.time()}


@app.get("/ready")
async def ready() -> Any:
    """Readiness. 503 when the model is not loaded.

    A load balancer wants this one, so a container without its weights is
    not sent traffic.
    """
    model = slm.status()
    is_ready = slm.is_ready()
    body = {
        "status": "ok" if is_ready else "degraded",
        "model": model,
        "cache_entries": len(app_cache),
        "timestamp": time.time(),
        "timezone": settings.TIMEZONE,
    }
    if not is_ready:
        return JSONResponse(status_code=503, content=body)
    return body


@app.get("/health")
async def health() -> Any:
    """Both, for convenience. See /live and /ready."""
    return await ready()


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


# The model runs on one thread, so a burst of questions has to queue. A
# request that would wait too long is turned away with a clear message
# rather than timing out in front of a judge.
IN_FLIGHT = 0
MAX_IN_FLIGHT = settings.MAX_IN_FLIGHT


@app.post("/ask")
async def ask(request: AskRequest, http_request: Request) -> dict[str, Any]:
    global IN_FLIGHT
    question = request.text()
    if not question:
        return {"status": "needs_clarification", "answer": "Please ask a question."}
    if IN_FLIGHT >= MAX_IN_FLIGHT:
        return {
            "status": "busy",
            "answer": (
                "The assistant is answering other questions right now. Please try "
                "again in a few seconds."
            ),
            "retry_after_seconds": 5,
        }
    IN_FLIGHT += 1
    try:
        try:
            result = await asyncio.wait_for(
                _run_ask(request, question), timeout=settings.REQUEST_DEADLINE_S
            )
        except TimeoutError:
            # Better a clear answer than a client that times out with nothing.
            return {
                "status": "busy",
                "answer": (
                    "The live data services are slow right now and I could not finish "
                    "in time. Please try again in a few seconds."
                ),
                "retry_after_seconds": 5,
                "request_id": getattr(
                    getattr(http_request, "state", None), "request_id", None
                ),
            }
        result["request_id"] = getattr(
            getattr(http_request, "state", None), "request_id", None
        )
        return result
    finally:
        IN_FLIGHT -= 1


async def _run_ask(request: AskRequest, question: str) -> dict[str, Any]:
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
        location_text=location[:200],
        radius_km=max(0.5, min(radius_km, 50.0)),
        profile=[p for p in profile.split(",") if p][:4],
    )
    try:
        bundle = await asyncio.wait_for(
            gather_context(intent), timeout=settings.REQUEST_DEADLINE_S
        )
    except TimeoutError:
        return {
            "error": "The lookup was too slow. Please try again in a few seconds.",
            "source_status": {},
            "limitations": ["the context lookup timed out"],
        }
    except Exception as exc:
        logger.exception("the context lookup failed")
        return {
            "error": "The lookup failed. Please try again in a few seconds.",
            "source_status": {},
            "limitations": [f"the context lookup failed: {type(exc).__name__}"],
        }
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
