"""Context gathering: everything the assistant needs for one question.

Runs the geocoder, the map lookups and the live weather/air-quality/routing
calls in parallel where possible, and returns a single bundle plus an
evidence list. A failing source degrades the answer, it never fails the
request: each source failure becomes a limitation note.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings
from app.exposure_engine import haversine_km
from app.planner import TimeWindow, now_ist, resolve
from app.sources.airquality import AQSource
from app.sources.daylight import DaylightSource
from app.sources.elevation import ElevationSource
from app.sources.geocode import GeocodeSource
from app.sources.overpass import OverpassSource, Venue
from app.sources.routing import RoutingSource
from app.sources.weather import WeatherSource

logger = logging.getLogger("geoai.context")


def _activity_config(activity: str) -> dict:
    """Read one activity block from the activity config."""
    import yaml

    from app.config import CONFIG_DIR

    with open(CONFIG_DIR / "activities.yaml", "r", encoding="utf-8") as f:
        acts = yaml.safe_load(f)
    return acts.get(activity) or acts.get("running") or {}


class Intent(BaseModel):
    activity: str = "running"
    location_text: str = ""
    lat: float | None = None
    lon: float | None = None
    radius_km: float = 5.0
    profile: list[str] = Field(default_factory=list)
    time_text: str = ""
    day: str | None = None
    part_of_day: str | None = None
    hour: int | None = None
    days_ahead: int = 1
    intent_type: str = "recommend"
    missing: list[str] = Field(default_factory=list)
    raw_text: str = ""


async def gather_context(intent: Intent) -> dict[str, Any]:
    started = time.perf_counter()
    geo = GeocodeSource()
    weather_src = WeatherSource()
    aq_src = AQSource()
    overpass = OverpassSource()
    routing = RoutingSource()
    daylight_src = DaylightSource()
    elevation_src = ElevationSource()

    evidence: dict[str, dict[str, Any]] = {}
    status: dict[str, bool] = {}
    limitations: list[str] = []
    timings: dict[str, float] = {}

    # The time window is resolved first: it decides how many days of forecast
    # we have to fetch. "this weekend" needs four days, "tomorrow" needs two.
    window: TimeWindow = resolve(
        time_text=intent.time_text,
        day=intent.day,
        part_of_day=intent.part_of_day,
        hour=intent.hour,
        days_ahead=intent.days_ahead,
        now=now_ist(),
        raw_text=intent.raw_text,
    )
    limitations.extend(window.assumptions)
    days_needed = max(
        1, (window.end.date() - now_ist().date()).days + 1
    )

    def add_evidence(
        key: str, source: str, value: Any, fetched_at: float | None = None
    ) -> str:
        """Evidence keys are stable and named, not numbered.

        A judge reading the answer can see exactly which source backed which
        claim, and the keys never shift depending on which sources answered.
        """
        evidence[key] = {
            "source": source,
            "value": value,
            "fetched_at": fetched_at or time.time(),
        }
        return key

    # ------------------------------------------------------------- geocode
    t0 = time.perf_counter()
    geo_res = await geo.geocode(intent.location_text)
    timings["geocode_ms"] = round((time.perf_counter() - t0) * 1000)
    status["geocode"] = geo_res.ok
    if not geo_res.ok or not geo_res.data:
        unavailable = bool(geo_res.error) and (
            "breaker" in str(geo_res.error).lower()
            or "timeout" in str(geo_res.error).lower()
            or "429" in str(geo_res.error)
        )
        message = (
            f"The place lookup service is busy right now, so I could not resolve "
            f"'{intent.location_text}'. Please try again in a moment."
            if unavailable
            else (
                f"I could not find the location '{intent.location_text}'. "
                "Try a nearby neighbourhood, landmark or city name."
            )
        )
        return {
            "error": message,
            "source_status": status,
            "limitations": [f"Geocoding failed: {geo_res.error}"],
        }

    candidates = geo_res.data
    origin = candidates[0]
    intent.lat, intent.lon = origin.lat, origin.lon
    add_evidence(
        "E_GEO",
        "Nominatim" if origin.source == "nominatim" else origin.source.title(),
        f"Location resolved to {origin.short_name} ({origin.lat:.5f}, {origin.lon:.5f})",
        geo_res.fetched_at,
    )
    if len(candidates) > 1:
        others = [c for c in candidates[1:4] if c.country_code != origin.country_code]
        if others:
            limitations.append(
                f"'{intent.location_text}' matches places in more than one country; "
                f"used '{origin.short_name}'. Other matches: "
                + ", ".join(
                    dict.fromkeys(c.short_name for c in others)
                )
                + "."
            )
        else:
            others = []
            for c in candidates[1:5]:
                if c.short_name != origin.short_name and c.short_name not in others:
                    others.append(c.short_name)
            limitations.append(
                f"Multiple places match '{intent.location_text}'; used "
                f"'{origin.short_name}'."
                + (f" Other matches: {', '.join(others)}." if others else "")
            )

    # -------------------------------------------------------------- venues
    async def timed(name: str, coro):
        t = time.perf_counter()
        try:
            res = await coro
        except Exception as exc:  # noqa: BLE001
            logger.warning("source %s raised: %s", name, exc)
            return None
        timings[f"{name}_ms"] = round((time.perf_counter() - t) * 1000)
        return res

    place_label = intent.location_text or origin.short_name
    place_label = place_label.split(",")[0].strip() or origin.short_name
    act_cfg = _activity_config(intent.activity)
    # Only the first two terms are tried: Nominatim throttles us above one
    # request a second and the venue list is only a safety net for Overpass.
    nom_terms = act_cfg.get("nominatim_terms", [])[:2]
    nom_types = act_cfg.get("nominatim_types", [])

    t0 = time.perf_counter()
    # Two independent venue sources run together and are merged: Overpass has
    # the best tag coverage but is often slow, Nominatim answers in ~1s.
    venue_res, nom_venue_res = await asyncio.gather(
        overpass.get_venues(
            intent.activity, origin.lat, origin.lon, intent.radius_km
        ),
        geo.search_venues(
            nom_terms,
            place_label,
            origin.lat,
            origin.lon,
            intent.radius_km,
            accept_types=nom_types,
        ),
    )
    timings["venues_ms"] = round((time.perf_counter() - t0) * 1000)
    status["overpass_venues"] = venue_res.ok
    venues = venue_res.data or []
    if not venue_res.ok:
        limitations.append(f"Overpass venue lookup failed: {venue_res.error}")
    # merge the Nominatim results
    nom_venues = nom_venue_res.data or []
    status["nominatim_venues"] = bool(nom_venues)
    for nv in nom_venues:
        if any(
            abs(nv["lat"] - v.lat) < 0.002 and abs(nv["lon"] - v.lon) < 0.002
            for v in venues
        ):
            continue
        if len(venues) >= act_cfg.get("shortlist", 12):
            break
        venues.append(
            Venue(
                id=f"nom/{nv['name'][:40]}",
                name=nv["name"],
                lat=nv["lat"],
                lon=nv["lon"],
                kind=str(nv.get("kind") or "other"),
                tags={"source": "nominatim"},
            )
        )
    if not venues:
        limitations.append(
            f"No {intent.activity} venues are mapped within {intent.radius_km:.0f} km of "
            f"{origin.short_name}. Try a wider radius or a nearby area."
        )
    add_evidence(
        "E_VENUES",
        "OpenStreetMap (Overpass API and Nominatim)",
        f"{len(venues)} candidate venues found within {intent.radius_km:.0f} km of "
        f"{origin.short_name}",
        venue_res.fetched_at,
    )

    # ------------------------------------------------- parallel live data
    lats = [origin.lat] + [v.lat for v in venues]
    lons = [origin.lon] + [v.lon for v in venues]
    # Weather reaches 16 days, air quality 7 days.
    weather_days = max(2, min(16, days_needed + 1))
    aq_days = max(2, min(7, days_needed + 1))

    # The hazard query only needs the origin, so it runs together with the
    # weather/air-quality/routing calls instead of waiting for them.
    # The hazard lookup is centred on the cluster of shortlisted venues rather
    # than on the user's own point, so a venue two kilometres away still gets
    # the roads that surround it.
    if venues:
        hazard_lat = sum(v.lat for v in venues) / len(venues)
        hazard_lon = sum(v.lon for v in venues) / len(venues)
        spread_km = max(
            haversine_km((hazard_lat, hazard_lon), (v.lat, v.lon)) for v in venues
        )
    else:
        hazard_lat, hazard_lon = origin.lat, origin.lon
        spread_km = 0.0
    hazard_radius_km = min(4.0, max(2.5, spread_km + 1.0))
    if spread_km + 0.5 > hazard_radius_km:
        limitations.append(
            f"The venues are spread over {spread_km:.0f} km, so the map features "
            f"around them were only mapped out to {hazard_radius_km:.0f} km."
        )
    hazard_task = asyncio.create_task(
        timed(
            "hazards",
            overpass.get_hazards(hazard_lat, hazard_lon, radius_km=hazard_radius_km),
        )
    )

    # Every source gets its own deadline and the whole group gets one too:
    # a slow upstream must not turn the answer into a minute long wait.
    tasks = {
        "hazards": hazard_task,
        "weather": asyncio.create_task(
            timed("weather", weather_src.get_forecast(lats, lons, days=weather_days))
        ),
        "air_quality": asyncio.create_task(
            timed("air_quality", aq_src.get_aqi(lats, lons, days=aq_days))
        ),
        "routing": asyncio.create_task(
            timed(
                "routing",
                routing.get_travel_times(
                    (origin.lat, origin.lon), [(v.lat, v.lon) for v in venues]
                ),
            )
        ),
        "daylight": asyncio.create_task(
            timed(
                "daylight",
                daylight_src.get_daylight(origin.lat, origin.lon, days=weather_days),
            )
        ),
        "elevation": asyncio.create_task(
            timed("elevation", elevation_src.get_elevation(lats[1:], lons[1:]))
        ),
    }
    done, pending = await asyncio.wait(
        tasks.values(), timeout=settings.CONTEXT_DEADLINE_S
    )
    for name, task in tasks.items():
        if task in pending:
            task.cancel()
            logger.warning("source %s did not answer in time", name)
    results = {
        name: (task.result() if task in done else None)
        for name, task in tasks.items()
    }
    hazard_res = results["hazards"]
    weather_res = results["weather"]
    aq_res = results["air_quality"]
    route_res = results["routing"]
    day_res = results["daylight"]
    elev_res = results["elevation"]
    if pending:
        limitations.append(
            "Some live sources were too slow to answer in time, so the answer "
            "uses only the data that arrived."
        )

    for name, res in (
        ("overpass_hazards", hazard_res),
        ("open-meteo-weather", weather_res),
        ("open-meteo-air-quality", aq_res),
        ("osrm-routing", route_res),
        ("daylight", day_res),
        ("elevation", elev_res),
    ):
        ok = bool(res and res.ok)
        status[name] = ok
        if not ok:
            limitations.append(f"{name} was unavailable; the answer is based on the other sources.")

    hazards = hazard_res.data if hazard_res and hazard_res.ok else []
    weather = weather_res.data if weather_res and weather_res.ok else []
    air_quality = aq_res.data if aq_res and aq_res.ok else {}
    routes = route_res.data if route_res and route_res.ok else {}
    daylight = day_res.data if day_res and day_res.ok else {}
    elevation = elev_res.data if elev_res and elev_res.ok else []

    if air_quality:
        limitations.extend(air_quality.get("station_limitations") or [])
        if air_quality.get("station"):
            st = air_quality["station"]
            add_evidence(
                "E_STATION",
                "WAQI",
                f"Nearest monitoring station {st.get('name')} reported PM2.5 "
                f"{st.get('observed_pm25')} ug/m3; grid bias factor "
                f"{air_quality.get('bias_factor')}",
            )

    add_evidence(
        "E_WEATHER",
        "Open-Meteo forecast",
        f"Hourly forecast for {len(lats)} locations covering the next {weather_days} days",
    )
    add_evidence(
        "E_AQ",
        "Open-Meteo air quality (CAMS ENSEMBLE)",
        "Air quality forecast: PM2.5, PM10, ozone, NO2 and US-AQI by hour",
    )
    add_evidence(
        "E_HAZARDS",
        "OpenStreetMap (Overpass API)",
        f"{len(hazards)} hazard or mitigator features mapped around the area "
        "(roads by class, industry, power plants, landfill, water, green cover)",
    )
    if routes.get("durations"):
        add_evidence(
            "E_ROUTES",
            "OSRM",
            "Travel times by car from the starting point to every shortlisted venue",
        )
    if daylight:
        add_evidence(
            "E_DAYLIGHT",
            "Open-Meteo daily" if daylight.get("source") == "open-meteo" else "Astral",
            f"Sunrise {daylight.get('sunrise', [''])[0]} / sunset "
            f"{daylight.get('sunset', [''])[0]} (IST)",
        )

    # The air-quality forecast reaches 7 days. Anything beyond that is not
    # scored, and the user is told why.
    horizon = now_ist() + timedelta(days=7)
    if window.end > horizon:
        limitations.append(
            "The request reaches beyond the 7 day air-quality forecast, so the "
            "later hours were not scored."
        )

    timings["total_ms"] = round((time.perf_counter() - started) * 1000)

    return {
        "intent": intent,
        "origin": origin,
        "candidates": candidates,
        "venues": venues,
        "weather": weather,
        "air_quality": air_quality,
        "hazards": hazards,
        "routes": routes,
        "daylight": daylight,
        "elevation": elevation,
        "window": window,
        "evidence": evidence,
        "source_status": status,
        "limitations": limitations,
        "timings": timings,
        "attribution": (
            "© OpenStreetMap contributors (Overpass, Nominatim); Open-Meteo; "
            "CAMS ENSEMBLE data (Copernicus/ECMWF); OSRM"
        ),
    }
