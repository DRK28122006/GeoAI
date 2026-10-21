"""Unit tests for the geo-exposure engine and the grounding verifier.

These are the two pieces that make the answer defensible: the engine decides
which (venue, hour) wins, and the verifier decides whether the model's
wording is allowed to stand.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.exposure_engine import (
    ExposureEngine,
    bearing_deg,
    haversine_km,
    wind_dir_label,
)
from app.sources.overpass import Hazard, Venue
from app.verifier import Verifier

IST = ZoneInfo("Asia/Kolkata")


def make_bundle(
    *,
    venues,
    wind_dir=270.0,
    wind_kmh=8.0,
    precip_prob=10,
    temp=24.0,
    us_aqi=45,
    pm25=22.0,
    hazards=None,
    hours=None,
    travel=600.0,
    lit=True,
):
    when_list = hours or [datetime(2026, 10, 22, 8, 0, tzinfo=IST)]
    times = [h.strftime("%Y-%m-%dT%H:%M") for h in when_list]

    weather = {
        "hourly": {
            "time": times,
            "apparent_temperature": [temp] * len(times),
            "temperature_2m": [temp] * len(times),
            "relative_humidity_2m": [60] * len(times),
            "precipitation_probability": [precip_prob] * len(times),
            "precipitation": [0.0] * len(times),
            "weather_code": [1] * len(times),
            "cloud_cover": [20] * len(times),
            "visibility": [20000] * len(times),
            "wind_speed_10m": [wind_kmh] * len(times),
            "wind_gusts_10m": [wind_kmh * 1.1] * len(times),
            "wind_direction_10m": [wind_dir] * len(times),
            "uv_index": [4.0] * len(times),
            "cape": [100.0] * len(times),
            "is_day": [1] * len(times),
        }
    }
    aq = {
        "hourly": {
            "time": times,
            "pm2_5": [pm25] * len(times),
            "pm10": [40.0] * len(times),
            "ozone": [40.0] * len(times),
            "nitrogen_dioxide": [20.0] * len(times),
            "us_aqi": [us_aqi] * len(times),
        }
    }

    class _Origin:
        short_name = "Test Area"
        display_name = "Test Area"

    class _Window:
        start = when_list[0]
        end = when_list[-1] + timedelta(hours=1)
        label = "test"
        assumptions: list[str] = []  # noqa: RUF012 - test fixture
        hours = when_list

    class _Intent:
        activity = "running"
        profile: list[str] = []  # noqa: RUF012 - test fixture
        radius_km = 5.0
        days_ahead = 1
        raw_text = "test"

    return {
        "intent": _Intent(),
        "origin": _Origin(),
        "venues": venues,
        "weather": [weather] * (len(venues) + 1),
        "air_quality": {"forecasts": [aq] * (len(venues) + 1), "bias_factor": 1.0},
        "hazards": hazards or [],
        "routes": {"durations": [travel] * len(venues), "distances": [4000.0] * len(venues)},
        "daylight": {
            "dates": [h.strftime("%Y-%m-%d") for h in when_list],
            "sunrise": [h.strftime("%Y-%m-%dT06:10") for h in when_list],
            "sunset": [h.strftime("%Y-%m-%dT18:10") for h in when_list],
        },
        "window": _Window(),
        "evidence": {},
        "limitations": [],
    }


@pytest.fixture
def venues():
    return [
        Venue(
            id="way/1",
            name="East Park",
            lat=12.9000,
            lon=77.5900,
            kind="park",
            tags={},
            lit=True,
        ),
        Venue(
            id="way/2",
            name="West Park",
            lat=12.9000,
            lon=77.5700,
            kind="park",
            tags={},
            lit=True,
        ),
    ]


# ---------------------------------------------------------------- geometry
def test_haversine():
    d = haversine_km((12.9716, 77.5946), (12.9716, 77.5946))
    assert d == pytest.approx(0, abs=1e-6)
    d = haversine_km((13.0, 77.0), (13.0, 77.0))
    assert d == pytest.approx(0, abs=1e-6)


def test_bearing_north_and_east():
    # due north
    assert bearing_deg((12.0, 77.0), (13.0, 77.0)) == pytest.approx(0, abs=1)
    # due east
    assert bearing_deg((12.0, 77.0), (12.0, 78.0)) == pytest.approx(90, abs=1)


def test_wind_labels():
    assert wind_dir_label(0) == "N"
    assert wind_dir_label(90) == "E"
    assert wind_dir_label(270) == "W"
    assert wind_dir_label(None) == "unknown"


# ------------------------------------------------------------------ engine
def test_engine_scores_slots(venues):
    engine = ExposureEngine()
    result = engine.score(make_bundle(venues=venues))
    assert result.slots
    assert result.slots[0].score > 0
    assert result.slots[0].breakdown["comfort"] > 0


def test_downwind_venue_is_penalised(venues):
    """A venue downwind of a road must score worse than one upwind.

    Geometry: the road sits at longitude 77.5714, West Park at 77.5700 (about
    150 m west of it) and East Park at 77.5900 (far to the east). An easterly
    wind blows from the road towards West Park, so West Park is downwind.
    """
    engine = ExposureEngine()
    hazard = Hazard(
        id="way/9",
        kind="road",
        road_class="primary",
        name="Test Expressway",
        points=[(12.9000, 77.5714)],
    )
    bundle = make_bundle(venues=venues, hazards=[hazard], wind_dir=90.0)
    result = engine.score(bundle)
    scores = {s.place: s.score for s in result.slots}
    assert scores["West Park"] < scores["East Park"]


def test_wind_direction_changes_the_ranking(venues):
    """The same place must score differently when the wind shifts."""
    engine = ExposureEngine()
    hazard = Hazard(
        id="way/9",
        kind="road",
        road_class="primary",
        name="Test Expressway",
        points=[(12.9000, 77.5714)],
    )
    easterly = engine.score(
        make_bundle(venues=venues, hazards=[hazard], wind_dir=90.0)
    )
    westerly = engine.score(
        make_bundle(venues=venues, hazards=[hazard], wind_dir=270.0)
    )
    e_scores = {s.place: s.score for s in easterly.slots}
    w_scores = {s.place: s.score for s in westerly.slots}
    # downwind of the road in the easterly case, upwind in the westerly case
    assert e_scores["West Park"] < w_scores["West Park"]
    assert e_scores["West Park"] != w_scores["West Park"]


def test_thunderstorm_disqualifies_the_slot(venues):
    engine = ExposureEngine()
    bundle = make_bundle(venues=venues)
    bundle["weather"][0]["hourly"]["weather_code"] = [95]
    result = engine.score(bundle)
    assert not result.slots
    assert any("thunderstorm" in a["reason"] for a in result.avoid)


def test_heavy_rain_disqualifies_the_slot(venues):
    engine = ExposureEngine()
    result = engine.score(make_bundle(venues=venues, precip_prob=90))
    assert not result.slots
    assert any("chance of rain" in a["reason"] for a in result.avoid)


def test_heat_disqualifies_the_slot(venues):
    engine = ExposureEngine()
    result = engine.score(make_bundle(venues=venues, temp=42.0))
    assert not result.slots
    assert any("temperature" in a["reason"] for a in result.avoid)


def test_profile_tightens_the_aqi_limit(venues):
    engine = ExposureEngine()
    clean = make_bundle(venues=venues, pm25=70.0, us_aqi=80)
    result = engine.score(clean)
    assert result.slots  # 70 is fine for a general user

    clean["intent"].profile = ["asthma"]
    strict = engine.score(clean)
    assert not strict.slots  # 70 is above the 60 limit for asthma
    assert any("PM2.5" in a["reason"] for a in strict.avoid)


def test_darkness_lowers_the_score_for_unlit_venues(venues):
    engine = ExposureEngine()
    day_hours = [datetime(2026, 10, 22, 12, 0, tzinfo=IST)]
    night_hours = [datetime(2026, 10, 22, 21, 0, tzinfo=IST)]

    day = engine.score(make_bundle(venues=venues, hours=day_hours))
    night_bundle = make_bundle(venues=venues, hours=night_hours)
    night_bundle["weather"][0]["hourly"]["is_day"] = [0]
    for v in venues:
        v.lit = False
    night = engine.score(night_bundle)

    assert day.slots[0].breakdown["daylight"] > night.slots[0].breakdown["daylight"]


def test_darkness_disqualifies_daylight_only_activities(venues):
    """Golf needs daylight, so an unlit venue at night is out."""
    engine = ExposureEngine()
    hours = [datetime(2026, 10, 22, 21, 0, tzinfo=IST)]
    bundle = make_bundle(venues=venues, hours=hours)
    bundle["intent"].activity = "golf"
    bundle["weather"][0]["hourly"]["is_day"] = [0]
    for v in venues:
        v.lit = False
    result = engine.score(bundle)
    assert not result.slots
    assert any("dark" in a["reason"] for a in result.avoid)


def test_travel_time_penalty_and_cap(venues):
    engine = ExposureEngine()
    near = engine.score(make_bundle(venues=venues, travel=300.0))
    far = engine.score(make_bundle(venues=venues, travel=2400.0))
    assert near.slots[0].breakdown["travel"] > far.slots[0].breakdown["travel"]

    # beyond the cap the slot is dropped entirely
    too_far = engine.score(make_bundle(venues=venues, travel=3000.0))
    assert not too_far.slots
    assert any("travel time" in a["reason"] for a in too_far.avoid)


# ---------------------------------------------------------------- verifier
def test_verifier_accepts_numbers_from_the_data(venues):
    engine = ExposureEngine()
    bundle = make_bundle(venues=venues)
    result = engine.score(bundle)
    facts = Verifier.build_facts(result, bundle)
    slot = result.slots[0]
    text = f"Go to {slot.place} at {slot.when.strftime('%H:%M')} feels like {slot.weather['apparent_temp_c']:.0f}C with aqi {slot.air['us_aqi']:.0f}"
    check = Verifier().verify(text, facts)
    assert check.verified, check.violations


def test_verifier_rejects_an_invented_number(venues):
    engine = ExposureEngine()
    bundle = make_bundle(venues=venues)
    result = engine.score(bundle)
    facts = Verifier.build_facts(result, bundle)
    check = Verifier().verify("the temperature will be 47C today", facts)
    assert not check.verified
    assert any("47" in v for v in check.violations)


def test_verifier_rejects_an_invented_place(venues):
    engine = ExposureEngine()
    bundle = make_bundle(venues=venues)
    result = engine.score(bundle)
    facts = Verifier.build_facts(result, bundle)
    check = Verifier().verify("Go to Silver Lake Park tomorrow", facts)
    assert not check.verified
    assert any("Silver Lake" in v for v in check.violations)


def test_verifier_rejects_a_time_outside_the_window(venues):
    engine = ExposureEngine()
    bundle = make_bundle(venues=venues)
    result = engine.score(bundle)
    facts = Verifier.build_facts(result, bundle)
    check = Verifier().verify("meet at 03:00 tomorrow", facts)
    assert not check.verified
