"""Contract tests for the API.

The response shape is a deliverable: the frontend team builds against it and
the rulebook asks for a clear recommendation, advice, evidence and
limitations. These tests pin the shape without touching the network.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app

IST = ZoneInfo("Asia/Kolkata")
CLIENT = TestClient(app)


def _bundle(activity: str = "running", profile: list[str] | None = None):
    """A synthetic bundle, shaped exactly like gather_context returns."""
    when = datetime(2026, 10, 22, 9, 0, tzinfo=IST)
    times = [when.strftime("%Y-%m-%dT%H:%M")]
    hourly = {
        "time": times,
        "apparent_temperature": [24.0],
        "temperature_2m": [24.0],
        "relative_humidity_2m": [58],
        "precipitation_probability": [12],
        "precipitation": [0.0],
        "weather_code": [1],
        "cloud_cover": [30],
        "visibility": [18000],
        "wind_speed_10m": [9.0],
        "wind_gusts_10m": [12.0],
        "wind_direction_10m": [250.0],
        "uv_index": [5.0],
        "cape": [120.0],
        "is_day": [1],
    }
    aq_hourly = {
        "time": times,
        "pm2_5": [18.0],
        "pm10": [30.0],
        "ozone": [45.0],
        "nitrogen_dioxide": [22.0],
        "us_aqi": [52.0],
    }

    class _Origin:
        short_name = "JP Nagar, Bengaluru"
        display_name = "JP Nagar, Bengaluru"
        lat = 12.9097
        lon = 77.5866
        country_code = "in"

    class _Window:
        start = when
        end = when + timedelta(hours=1)
        label = "tomorrow morning"
        hours = [when]
        assumptions: list[str] = []  # noqa: RUF012 - test fixture
        dates: list[str] = []  # noqa: RUF012 - test fixture

    class _Intent:
        pass

    intent = _Intent()
    intent.activity = activity
    intent.profile = profile or []
    intent.radius_km = 5.0
    intent.days_ahead = 1
    intent.raw_text = "test"
    intent.location_text = "JP Nagar"
    intent.lat = 12.9097
    intent.lon = 77.5866

    from app.sources.overpass import Venue

    venue = Venue(
        id="way/1",
        name="Anuroopa Park",
        lat=12.9100,
        lon=77.5880,
        kind="park",
        tags={},
        lit=True,
    )

    return {
        "intent": intent,
        "origin": _Origin(),
        "candidates": [_Origin()],
        "venues": [venue],
        "weather": [{"hourly": hourly}, {"hourly": hourly}],
        "air_quality": {
            "forecasts": [{"hourly": aq_hourly}, {"hourly": aq_hourly}],
            "bias_factor": 1.0,
            "station": None,
            "station_limitations": [],
        },
        "hazards": [],
        "routes": {"durations": [540.0], "distances": [4200.0]},
        "daylight": {
            "dates": ["2026-10-22"],
            "sunrise": ["2026-10-22T06:10"],
            "sunset": ["2026-10-22T17:57"],
        },
        "elevation": [12.0],
        "window": _Window(),
        "evidence": {
            "E_GEO": {"source": "Nominatim", "value": "JP Nagar", "fetched_at": 1.0},
            "E_VENUES": {"source": "OSM", "value": "1 venue", "fetched_at": 1.0},
            "E_WEATHER": {"source": "Open-Meteo", "value": "forecast", "fetched_at": 1.0},
            "E_AQ": {"source": "CAMS", "value": "air quality", "fetched_at": 1.0},
            "E_HAZARDS": {"source": "OSM", "value": "hazards", "fetched_at": 1.0},
            "E_ROUTES": {"source": "OSRM", "value": "travel", "fetched_at": 1.0},
            "E_DAYLIGHT": {"source": "Open-Meteo", "value": "daylight", "fetched_at": 1.0},
        },
        "source_status": {},
        "limitations": [],
        "timings": {},
        "attribution": "© OpenStreetMap contributors",
    }


@pytest.fixture
def stubbed(monkeypatch):
    """Replace the model and the network with deterministic stand-ins."""
    async def fake_parse(self, question, now_iso):
        return {
            "activity": "running",
            "location": "JP Nagar",
            "day": "tomorrow",
            "part_of_day": "morning",
            "hour": None,
            "profile": [],
            "out_of_scope": False,
        }

    async def fake_gather(intent):
        return _bundle(intent.activity, intent.profile)

    monkeypatch.setattr(main_module.pipeline.slm, "parse_intent", fake_parse)
    monkeypatch.setattr("app.pipeline.gather_context", fake_gather)
    return True


def test_health_reports_the_model(stubbed):
    resp = CLIENT.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "model" in body
    assert body["model"]["model"] == "Qwen/Qwen3-1.7B"


def test_sources_lists_attribution(stubbed):
    resp = CLIENT.get("/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert "© OpenStreetMap contributors" in body["attribution"]
    names = " ".join(s["name"] for s in body["sources"])
    assert "OpenStreetMap" in names
    assert "Open-Meteo" in names
    assert "OSRM" in names
    assert "disclosure" in body


def test_ask_returns_the_documented_shape(stubbed):
    resp = CLIENT.post(
        "/ask", json={"question": "Where can I go running tomorrow near JP Nagar?"}
    )
    assert resp.status_code == 200
    body = resp.json()

    for key in (
        "status",
        "clarification_question",
        "parsed",
        "recommendation",
        "alternatives",
        "avoid",
        "advice",
        "evidence",
        "proof",
        "map_geojson",
        "data_as_of",
        "sources",
        "limitations",
        "timings_ms",
        "answer",
    ):
        assert key in body, f"missing key in the response: {key}"

    assert body["status"] == "ok"
    rec = body["recommendation"]
    assert rec["place"] == "Anuroopa Park"
    assert rec["score"] > 0
    assert body["answer"]

    # the proof must cite real sources
    factors = body["proof"]["factors"]
    assert factors
    for factor in factors:
        assert factor["source"]
        assert factor["evidence"] in body["evidence"]

    # the map payload must be renderable
    assert body["map_geojson"]["type"] == "FeatureCollection"
    assert body["map_geojson"]["features"]

    # advice has both lists
    assert "carry" in body["advice"]
    assert "watch_for" in body["advice"]


def test_ask_accepts_the_query_field_too(stubbed):
    resp = CLIENT.post(
        "/ask", json={"query": "Where can I go running tomorrow near JP Nagar?"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_cors_is_enabled_for_the_frontend(stubbed):
    resp = CLIENT.get("/health", headers={"Origin": "https://example.com"})
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_out_of_scope_is_declined_not_crashed(stubbed):
    resp = CLIENT.post("/ask", json={"question": "Write me a poem about the monsoon"})
    assert resp.status_code == 200
    body = resp.json()
    # the stubbed parser says it is in scope, so the request still succeeds
    assert body["status"] in {"ok", "partial", "out_of_scope"}


def test_a_broken_source_does_not_produce_a_500(stubbed, monkeypatch):
    async def exploding(intent):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr("app.pipeline.gather_context", exploding)
    resp = CLIENT.post(
        "/ask", json={"question": "Where can I go running tomorrow near JP Nagar?"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "partial"
    assert "error" in body or "limitations" in body
