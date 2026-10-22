"""Shared fixtures for the test suite."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import main as main_module

IST = ZoneInfo("Asia/Kolkata")

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
        assumptions: list[str] = []
        dates: list[str] = []

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
    monkeypatch.setattr(main_module.slm, "is_ready", lambda: True)
    def fake_parse(self, question, now_iso):
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

    def fake_choose(self, question, options):
        # A well formed answer that only uses the values from the table.
        top = options[0]
        return {
            "slot_id": top["slot_id"],
            "headline": f"Go to {top['place']} around {top['time'][-5:]}.",
            "carry": ["Water bottle", "Light layer"],
            "watch_for": ["Air quality is moderate"],
            "avoid_note": "Avoid the afternoon.",
        }

    monkeypatch.setattr(type(main_module.pipeline.slm), "parse_intent", fake_parse)
    monkeypatch.setattr(type(main_module.pipeline.slm), "choose", fake_choose)
    monkeypatch.setattr("app.pipeline.gather_context", fake_gather)
    return True


