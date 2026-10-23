"""Tests for the production hardening.

These are the behaviours that keep the service alive during a judging
window: a deadline on the request, a liveness probe that does not take the
container out of the pool, coordinates that are actually used, and a token
that never leaks.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.sources.base import redact

CLIENT = TestClient(app)


@pytest.fixture(autouse=True)
def ready_model(monkeypatch):
    monkeypatch.setattr(main_module.slm, "is_ready", lambda: True)


# ----------------------------------------------------------------- health
def test_live_is_always_green(monkeypatch):
    """A container without its weights is degraded, not dead."""
    monkeypatch.setattr(main_module.slm, "is_ready", lambda: False)
    resp = CLIENT.get("/live")
    assert resp.status_code == 200


def test_ready_is_503_without_the_model(monkeypatch):
    monkeypatch.setattr(main_module.slm, "is_ready", lambda: False)
    resp = CLIENT.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "degraded"


def test_health_reports_the_model():
    resp = CLIENT.get("/health")
    assert resp.status_code == 200
    assert resp.json()["model"]["model"] == "Qwen/Qwen3-1.7B"


# ----------------------------------------------------------------- limits
def test_the_coordinates_skip_the_geocoder(monkeypatch):
    """A GPS-only question used to fail, because the geocoder ran anyway."""
    import respx
    from httpx import Response

    from app.context import Intent, gather_context

    async def go():
        intent = Intent(
            activity="running",
            location_text="",
            radius_km=5.0,
            lat=12.9696,
            lon=77.7497,
            raw_text="where can I run tomorrow morning?",
        )
        return await gather_context(intent)

    with respx.mock:
        # The venue search also uses Nominatim, so the check is on the
        # evidence: the starting point must come from the coordinates.
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=Response(200, json=[])
        )
        respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
            return_value=Response(200, json={"results": []})
        )
        respx.get("https://api.open-meteo.com/v1/forecast").mock(
            return_value=Response(200, json={"hourly": {"time": [], "temperature_2m": []}})
        )
        respx.get("https://air-quality-api.open-meteo.com/v1/air-quality").mock(
            return_value=Response(200, json={"hourly": {"time": []}})
        )
        respx.get("https://api.open-meteo.com/v1/elevation").mock(
            return_value=Response(200, json={"elevation": []})
        )
        respx.post("https://overpass.openstreetmap.fr/api/interpreter").mock(
            return_value=Response(200, json={"elements": []})
        )
        bundle = asyncio_run(go())

    assert bundle["origin"].lat == 12.9696
    assert bundle["origin"].lon == 77.7497
    assert bundle["evidence"]["E_GEO"]["source"] == "the coordinates in the request"


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def test_out_of_range_coordinates_are_rejected():
    resp = CLIENT.post(
        "/ask",
        json={"question": "where can I run?", "lat": 999, "lon": 0},
    )
    assert resp.status_code == 422


def test_the_radius_is_bounded():
    from app.pipeline import Pipeline

    assert Pipeline._extract_radius("within 999 km of Pune") == 25.0
    assert Pipeline._extract_radius("within 0 km of Pune") == 0.5
    assert Pipeline._extract_radius("no radius here") == 0.0


def test_a_huge_question_is_rejected():
    resp = CLIENT.post("/ask", json={"question": "x" * 5000})
    assert resp.status_code == 422


# ---------------------------------------------------------------- secrets
def test_a_token_is_redacted():
    message = (
        "Client error '403 Forbidden' for url "
        "'https://api.waqi.info/feed/geo:19.07;72.87/?token=SECRET123456'"
    )
    safe = redact(message)
    assert "SECRET123456" not in safe
    assert "token=***" in safe


def test_the_configured_token_is_redacted_wherever_it_appears(monkeypatch):
    """The token travels in a query string, so it can end up anywhere."""
    monkeypatch.setattr(main_module.settings, "WAQI_TOKEN", "SECRET123456")
    for message in (
        "the token SECRET123456 was rejected",
        "url https://api.waqi.info/feed/?token=SECRET123456",
    ):
        assert "SECRET123456" not in redact(message)


def test_the_client_never_sees_a_raw_exception():
    resp = CLIENT.get("/context?location=Pune&activity=running&radius_km=5")
    body = resp.text
    assert "Traceback" not in body
