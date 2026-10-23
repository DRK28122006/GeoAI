"""Tests for the local OpenStreetMap source (the Geofabrik regional extracts).

None of these tests touches the network: the HTTP calls are mocked and the
zone boundaries are read from the file that ships with the code.

What is pinned here:

* the download URL is the file Geofabrik actually publishes,
* the right zone is picked for a point, and a point outside every zone gets
  no zone at all (the caller falls back to Overpass instead of guessing),
* a zone that is too big to load is skipped,
* a request never waits for a download, and a broken download never turns
  into a broken answer.
"""

from __future__ import annotations

import asyncio
import time

import pytest
import respx
from httpx import Response

from app.config import settings
from app.sources import geofabrik
from app.sources.base import app_cache
from app.sources.geofabrik import (
    ZONE_SIZES_MB,
    GeofabrikSource,
    bracket_filters,
    zone_for,
    zone_url,
)
from app.sources.geofabrik_zones import ZONE_POLYGONS

ZONES = ["central", "eastern", "north-eastern", "northern", "southern", "western"]


@pytest.fixture(autouse=True)
def clear_cache():
    app_cache.clear()
    yield
    app_cache.clear()


@pytest.fixture(autouse=True)
async def stop_background_downloads():
    """A background download must not outlive the test that started it."""
    yield
    tasks = [t for t in geofabrik._DOWNLOADS.values() if not t.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    geofabrik._DOWNLOADS.clear()
    geofabrik._PROGRESS.clear()


@pytest.fixture
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "CACHE_DIR", str(tmp_path))
    return tmp_path


# ------------------------------------------------------------------- the URL
@pytest.mark.parametrize("zone", ZONES)
def test_the_url_is_the_file_geofabrik_publishes(zone):
    """The extract lives at /asia/india/<zone>-zone-latest.osm.pbf."""
    assert zone_url(f"{zone}-zone") == (
        f"https://download.geofabrik.de/asia/india/{zone}-zone-latest.osm.pbf"
    )


@pytest.mark.parametrize("zone", ZONES)
def test_the_bare_zone_name_gives_the_same_url(zone):
    """The zone names in the code already carry the -zone suffix."""
    assert zone_url(zone) == zone_url(f"{zone}-zone")


def test_the_url_is_never_a_state_extract():
    """India has no state extracts: the zone is the only granularity."""
    url = zone_url("southern-zone")
    assert url.endswith("/southern-zone-latest.osm.pbf")
    assert "maharashtra" not in url
    assert "karnataka" not in url


def test_the_cached_file_is_named_after_the_remote_file(cache_dir):
    source = GeofabrikSource()
    path = source._cache_path("north-eastern-zone")
    assert path.name == "north-eastern-zone-latest.osm.pbf"
    assert path.parent.name == "geofabrik"


# ---------------------------------------------------------------- the zones
CITY_ZONES = [
    ("Bengaluru", 12.97, 77.59, "southern-zone"),
    ("Pune", 18.52, 73.85, "western-zone"),
    ("Delhi", 28.61, 77.21, "northern-zone"),
    ("Mumbai", 19.07, 72.87, "western-zone"),
    ("Chennai", 13.08, 80.27, "southern-zone"),
    ("Kolkata", 22.57, 88.36, "eastern-zone"),
    ("Hyderabad", 17.39, 78.49, "southern-zone"),
    ("Lucknow", 26.85, 80.95, "central-zone"),
    ("Ahmedabad", 23.02, 72.57, "western-zone"),
    ("Jaipur", 26.91, 75.79, "northern-zone"),
]


@pytest.mark.parametrize("name,lat,lon,expected", CITY_ZONES)
def test_the_right_zone_is_picked(name, lat, lon, expected):
    assert zone_for(lat, lon) == expected


@pytest.mark.parametrize(
    "name,lat,lon",
    [
        ("Guwahati", 26.14, 91.73),
        ("Imphal", 24.82, 93.94),
        ("Aizawl", 23.72, 92.71),
        ("Silchar", 24.83, 92.78),
        ("Agartala", 23.83, 91.28),
    ],
)
def test_the_whole_north_east_is_covered(name, lat, lon):
    """The north-eastern zone is two rings; one of them used to be missing."""
    assert zone_for(lat, lon) == "north-eastern-zone"


def test_a_point_outside_every_zone_gets_no_zone():
    """Guessing a zone would download the wrong extract."""
    assert zone_for(51.5074, -0.1278) is None
    assert zone_for(0.0, 0.0) is None
    assert zone_for(-33.87, 151.21) is None


def test_the_boundaries_are_real_and_not_stubs():
    """The polygons have to be the real ones, or the feature is guesswork."""
    assert set(ZONE_POLYGONS) == set(ZONE_SIZES_MB)
    for zone, rings in ZONE_POLYGONS.items():
        assert rings, f"{zone} has no boundary"
        for ring in rings:
            assert len(ring) > 20, f"{zone} has a stub ring"
            for point in ring:
                assert len(point) == 2
                assert 60 < point[0] < 100, f"{zone} has a bad longitude {point[0]}"
                assert 5 < point[1] < 40, f"{zone} has a bad latitude {point[1]}"
    # The north-eastern zone is split in two by Bhutan.
    assert len(ZONE_POLYGONS["north-eastern-zone"]) == 2


def test_the_boundaries_cover_the_whole_of_india():
    """Every zone has to hold a city, or part of India has no extract."""
    for name, lat, lon, zone in CITY_ZONES:
        assert zone_for(lat, lon) == zone, name


# -------------------------------------------------------------- the filters
def test_the_overpass_selectors_become_pyrosm_filters():
    """The tag filters keep the Overpass meaning: OR of the selectors."""
    filters = bracket_filters(
        [
            '["leisure"~"^(track|fitness_track|running_track)$"]',
            '["leisure"="pitch"]["sport"~"^(athletics|running|football)$"]',
        ]
    )
    assert filters == [
        '["leisure"~"^(track|fitness_track|running_track)$"]',
        '["leisure"="pitch"]["sport"~"^(athletics|running|football)$"]',
    ]


def test_an_overpass_statement_becomes_a_filter():
    """The hazard config writes whole statements; only the tags matter."""
    filters = bracket_filters(['nwr(around:1000,12.9,77.5)["landuse"="industrial"]'])
    assert filters == ['["landuse"="industrial"]']


# ------------------------------------------------------------- the size guard
def test_the_big_zones_are_skipped(monkeypatch):
    """At 300 MB the southern and the central zones do not fit."""
    monkeypatch.setattr(settings, "GEOFABRIK_MAX_MB", 300)
    source = GeofabrikSource()
    assert source.zone_over_limit("southern-zone") is True
    assert source.zone_over_limit("central-zone") is True
    assert source.zone_over_limit("western-zone") is False
    assert source.zone_over_limit("north-eastern-zone") is False


def test_the_size_limit_is_a_setting(monkeypatch):
    monkeypatch.setattr(settings, "GEOFABRIK_MAX_MB", 600)
    source = GeofabrikSource()
    assert source.zone_over_limit("southern-zone") is False


def test_the_known_sizes_match_what_geofabrik_publishes():
    """The numbers are the published sizes, in MB."""
    assert ZONE_SIZES_MB["southern-zone"] > 500
    assert ZONE_SIZES_MB["central-zone"] > 300
    assert ZONE_SIZES_MB["north-eastern-zone"] < 150


@pytest.mark.asyncio
async def test_a_zone_over_the_limit_is_not_even_downloaded(
    monkeypatch, cache_dir
):
    monkeypatch.setattr(settings, "GEOFABRIK_ENABLED", True)
    monkeypatch.setattr(settings, "GEOFABRIK_MAX_MB", 300)
    source = GeofabrikSource()
    with respx.mock:
        route = respx.get(zone_url("southern-zone"))
        # Bengaluru is in the southern zone.
        result = await source.get_venues("running", 12.97, 77.59)
        assert not route.called
    assert result.ok is False
    assert "not ready" in (result.error or "")


@pytest.mark.asyncio
async def test_a_download_that_is_too_big_is_refused(monkeypatch, cache_dir):
    """The server says how big the file is, so it is not even written."""
    monkeypatch.setattr(settings, "GEOFABRIK_MAX_MB", 300)
    with respx.mock:
        respx.get(zone_url("north-eastern-zone")).mock(
            return_value=Response(
                200,
                content=b"",
                headers={"content-length": str(500 * 1024 * 1024)},
            )
        )
        path = await geofabrik.download_zone(
            "north-eastern-zone", 300 * 1024 * 1024, 5
        )
    assert path is None
    assert not (
        cache_dir / "geofabrik" / "north-eastern-zone-latest.osm.pbf"
    ).exists()


@pytest.mark.asyncio
async def test_a_download_that_grows_past_the_limit_is_stopped(
    monkeypatch, cache_dir
):
    """A file that lies about its size is stopped while it is written."""
    monkeypatch.setattr(settings, "GEOFABRIK_MAX_MB", 1)
    with respx.mock:
        respx.get(zone_url("north-eastern-zone")).mock(
            return_value=Response(200, content=iter([b"x" * (1024 * 1024)] * 4))
        )
        path = await geofabrik.download_zone(
            "north-eastern-zone", 1024 * 1024, 5
        )
    assert path is None
    assert not (
        cache_dir / "geofabrik" / "north-eastern-zone-latest.osm.pbf"
    ).exists()


@pytest.mark.asyncio
async def test_a_download_under_the_limit_is_kept(monkeypatch, cache_dir):
    monkeypatch.setattr(settings, "GEOFABRIK_MAX_MB", 300)
    with respx.mock:
        respx.get(zone_url("north-eastern-zone")).mock(
            return_value=Response(200, content=b"not a real pbf, but it is small")
        )
        path = await geofabrik.download_zone(
            "north-eastern-zone", 300 * 1024 * 1024, 5
        )
    assert path is not None
    assert path.exists()


# ------------------------------------------------------- the request path
@pytest.mark.asyncio
async def test_a_request_never_waits_for_the_download(
    monkeypatch, cache_dir
):
    """Nobody waits minutes for a 500 MB file."""
    monkeypatch.setattr(settings, "GEOFABRIK_ENABLED", True)
    monkeypatch.setattr(settings, "GEOFABRIK_REQUEST_TIMEOUT_S", 0.2)

    async def slow_body():
        yield b"x" * 1024
        await asyncio.sleep(30)

    source = GeofabrikSource()
    with respx.mock:
        route = respx.get(zone_url("western-zone")).mock(
            return_value=Response(200, stream=slow_body())
        )
        started = time.monotonic()
        # Pune is in the western zone.
        result = await source.get_venues("running", 18.52, 73.85)
        took = time.monotonic() - started
        # Let the background task start, so we can see the request went out.
        await asyncio.sleep(0.05)
        assert route.called

    assert result.ok is False
    assert took < 2.0, f"the request waited {took:.1f}s for the download"
    # The download goes on in the background, so a later request finds it.
    assert "western-zone" in geofabrik._DOWNLOADS


@pytest.mark.asyncio
async def test_a_failed_download_does_not_raise_into_the_event_loop(
    monkeypatch, cache_dir
):
    """A broken download is a normal outcome, not an event loop error."""
    monkeypatch.setattr(settings, "GEOFABRIK_ENABLED", True)

    with respx.mock:
        respx.get(zone_url("western-zone")).mock(
            return_value=Response(404, text="not found")
        )
        source = GeofabrikSource()
        result = await source.get_venues("running", 18.52, 73.85)
        # Let the background task finish inside the mock.
        await asyncio.sleep(0.05)

    assert result.ok is False
    task = geofabrik._DOWNLOADS.get("western-zone")
    assert task is not None
    # The task swallowed the failure: it did not raise.
    assert task.done() and task.exception() is None


@pytest.mark.asyncio
async def test_a_point_with_no_zone_gets_no_extract(monkeypatch, cache_dir):
    """Outside every zone there is nothing to download."""
    monkeypatch.setattr(settings, "GEOFABRIK_ENABLED", True)
    source = GeofabrikSource()
    with respx.mock:
        route = respx.get(url__startswith="https://download.geofabrik.de")
        result = await source.get_venues("running", 51.5074, -0.1278)
        assert not route.called
    assert result.ok is False


@pytest.mark.asyncio
async def test_the_feature_is_off_by_default(monkeypatch, cache_dir):
    """A pre-downloaded extract cannot be guaranteed before a demo."""
    monkeypatch.setattr(settings, "GEOFABRIK_ENABLED", False)
    source = GeofabrikSource()
    assert source.enabled is False
    result = await source.get_venues("running", 12.97, 77.59)
    assert result.ok is False
    assert result.error == "disabled"


# ------------------------------------------------------------ the fallback
def _overpass_element(lat: float, lon: float, name: str) -> dict:
    return {
        "type": "way",
        "id": 1,
        "center": {"lat": lat, "lon": lon},
        "tags": {"leisure": "park", "name": name},
    }


@pytest.mark.asyncio
async def test_a_broken_geofabrik_falls_back_to_overpass(monkeypatch):
    """A failure in the local path must never cost the user the answer."""
    from app.context import Intent, gather_context

    monkeypatch.setattr(settings, "GEOFABRIK_ENABLED", True)

    async def broken(self, *args, **kwargs):
        raise RuntimeError("the extract could not be read")

    monkeypatch.setattr(GeofabrikSource, "get_venues", broken)

    with respx.mock:
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=Response(200, json=[])
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
            return_value=Response(
                200, json={"elements": [_overpass_element(12.91, 77.59, "Anuroopa Park")]}
            )
        )
        intent = Intent(
            activity="running",
            location_text="JP Nagar, Bengaluru",
            radius_km=5.0,
            lat=12.9097,
            lon=77.5866,
            raw_text="where can I run tomorrow morning?",
        )
        bundle = await gather_context(intent)

    assert bundle["venues"], "the Overpass venues were lost"
    assert bundle["venues"][0].name == "Anuroopa Park"
    assert bundle["source_status"]["overpass_venues"] is True
    assert bundle["source_status"]["geofabrik_venues"] is False


@pytest.mark.asyncio
async def test_an_empty_local_answer_falls_back_to_overpass(monkeypatch):
    """An extract with nothing in it must not produce an empty answer."""
    from app.context import Intent, gather_context
    from app.sources.base import SourceResult

    monkeypatch.setattr(settings, "GEOFABRIK_ENABLED", True)

    async def empty(self, *args, **kwargs):
        return SourceResult(source="geofabrik", ok=True, data=[])

    monkeypatch.setattr(GeofabrikSource, "get_venues", empty)

    with respx.mock:
        respx.get("https://nominatim.openstreetmap.org/search").mock(
            return_value=Response(200, json=[])
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
            return_value=Response(
                200, json={"elements": [_overpass_element(12.91, 77.59, "Anuroopa Park")]}
            )
        )
        intent = Intent(
            activity="running",
            location_text="JP Nagar, Bengaluru",
            radius_km=5.0,
            lat=12.9097,
            lon=77.5866,
            raw_text="where can I run tomorrow morning?",
        )
        bundle = await gather_context(intent)

    assert bundle["venues"], "an empty local answer was published"
    assert bundle["venues"][0].name == "Anuroopa Park"


@pytest.mark.asyncio
async def test_a_broken_geofabrik_does_not_break_the_hazards(monkeypatch):
    """The hazard path falls back the same way."""
    monkeypatch.setattr(settings, "GEOFABRIK_ENABLED", True)

    async def broken(self, *args, **kwargs):
        raise RuntimeError("the extract could not be read")

    # Patch the internal extraction step, not get_hazards itself: the real
    # get_hazards wraps it in BaseSource.fetch, which turns a failure into a
    # SourceResult with ok=False instead of letting it propagate.
    monkeypatch.setattr(GeofabrikSource, "_extract_or_none", broken)

    source = GeofabrikSource()
    assert source.enabled is True
    result = await source.get_hazards(12.91, 77.59, radius_km=3.0)
    assert result.ok is False
    assert result.error


# ------------------------------------------------------------ the deadline
def test_the_request_deadline_still_holds(monkeypatch):
    """A slow source must not turn into a hung request."""
    from fastapi.testclient import TestClient

    from app import main as main_module

    monkeypatch.setattr(settings, "REQUEST_DEADLINE_S", 0.5)

    async def slow_gather(intent):
        await asyncio.sleep(30)
        return {}

    monkeypatch.setattr("app.pipeline.gather_context", slow_gather)

    client = TestClient(main_module.app)
    started = time.monotonic()
    resp = client.post("/ask", json={"question": "where can I run near JP Nagar?"})
    took = time.monotonic() - started

    assert resp.status_code == 200
    assert resp.json()["status"] == "busy"
    assert took < 10, f"the request took {took:.1f}s"
