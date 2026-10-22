"""Local OpenStreetMap data from Geofabrik regional extracts.

The Overpass mirrors are slow and serialise one request at a time. Geofabrik
publishes the same OSM data as regional extracts, so we download the extract
that contains the user's location once, cache it on disk, and query it locally
with `pyrosm`. Overpass stays as the fallback when the extract cannot be
fetched or parsed.

Granularity: Geofabrik's finest Indian extracts are **zones** (central,
eastern, north-eastern, northern, southern, western), roughly 100-560 MB each.
State-level extracts are not published for India, and the whole-country file
is ~1.7 GB, so the zone is the right unit: small enough to download on first
use, large enough to cover a whole region of cities. The zone that contains
the user's coordinates is picked from the Geofabrik boundary KML.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from pathlib import Path
from typing import Any

from app.config import settings
from app.exposure_engine import haversine_km
from app.sources.base import BaseSource, SourceResult
from app.sources.overpass import Hazard, OverpassSource, Venue

logger = logging.getLogger("geoai.geofabrik")

try:
    from pyrosm import OSM as _PyrosmOSM  # type: ignore

    _PYROSM_AVAILABLE = True
except Exception as exc:  # noqa: BLE001
    _PYROSM_AVAILABLE = False
    logger.info("pyrosm is not available, Geofabrik source disabled: %s", exc)

try:
    from shapely.geometry import Point, Polygon  # type: ignore

    _SHAPELY_AVAILABLE = True
except Exception:  # noqa: BLE001
    _SHAPELY_AVAILABLE = False

try:
    from app.sources.geofabrik_zones import ZONE_POLYGONS

    _ZONES_AVAILABLE = True
except Exception:  # noqa: BLE001
    ZONE_POLYGONS = {}
    _ZONES_AVAILABLE = False


def zone_for(lat: float, lon: float) -> str:
    """Pick the Geofabrik zone whose polygon contains the point."""
    if _ZONES_AVAILABLE and _SHAPELY_AVAILABLE:
        point = Point(lon, lat)
        for zone, ring in ZONE_POLYGONS.items():
            try:
                if Polygon(ring).contains(point):
                    return zone
            except Exception:  # noqa: BLE001, S112
                continue
    # Fallback heuristic, used when the polygons are not available.
    if lat >= 27:
        return "north-eastern-zone" if lon >= 87.5 else "northern-zone"
    if lat >= 20:
        if lon >= 87.5:
            return "north-eastern-zone"
        if lon >= 82:
            return "eastern-zone"
        if lon >= 74:
            return "central-zone"
        return "western-zone"
    return "southern-zone"


def _selectors_to_filter(selectors: list[str]) -> dict[str, Any]:
    """Turn Overpass tag selectors into a pyrosm custom_filter."""
    result: dict[str, list[str] | bool] = {}
    for sel in selectors or []:
        for bracket in re.findall(r"\[[^\]]+\]", sel):
            m = re.search(r'"(\w+)"~"([^"]+)"', bracket)
            if m:
                key, vals = m.group(1), [v for v in m.group(2).split("|") if v]
                if vals and any(v in {".", ".*"} for v in vals):
                    result[key] = True
                else:
                    result.setdefault(key, [])
                    if isinstance(result[key], list):
                        result[key].extend(vals)
            m = re.search(r'"(\w+)"="([^"]+)"', bracket)
            if m:
                key, val = m.group(1), m.group(2)
                result.setdefault(key, [])
                if isinstance(result[key], list):
                    result[key].append(val)
    # de-duplicate list values
    for key, val in list(result.items()):
        if isinstance(val, list):
            uniq = []
            for v in val:
                if v not in uniq:
                    uniq.append(v)
            result[key] = uniq if uniq else True
    return result


def _classify(tags: dict[str, Any]) -> str:
    return OverpassSource._classify(tags)


class GeofabrikSource(BaseSource):
    def __init__(self):
        super().__init__("geofabrik")
        self.activities = __import__("app.config", fromlist=["load_config"]).load_config(
            "activities"
        )
        self.hazards_cfg = __import__("app.config", fromlist=["load_config"]).load_config(
            "hazards"
        )

    @property
    def enabled(self) -> bool:
        return bool(settings.GEOFABRIK_ENABLED and _PYROSM_AVAILABLE)

    def _cache_path(self, zone: str) -> Path:
        cache_dir = Path(settings.CACHE_DIR) / "geofabrik"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / f"{zone}-latest.osm.pbf"

    async def _download_zone(self, zone: str) -> Path:
        path = self._cache_path(zone)
        # Fresh enough: reuse.
        if path.exists():
            age = time.time() - path.stat().st_mtime
            if age < settings.TTL_GEOFABRIK:
                return path
        url = f"https://download.geofabrik.de/asia/india/{zone}-latest.osm.pbf"
        tmp = path.with_suffix(".pbf.part")
        async with __import__("httpx").AsyncClient(
            timeout=None, follow_redirects=True
        ) as client, client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as handle:  # noqa: ASYNC230
                async for chunk in resp.aiter_bytes(chunk_size=1 << 20):
                    handle.write(chunk)
        tmp.replace(path)
        return path

    def _point_for(self, geom: Any) -> tuple[float, float] | None:
        if geom is None:
            return None
        try:
            if geom.geom_type == "Point":
                return float(geom.y), float(geom.x)
            centroid = geom.centroid
            return float(centroid.y), float(centroid.x)
        except Exception:  # noqa: BLE001
            return None

    def _venues_from_gdf(self, gdf: Any, activity: str, lat: float, lon: float, radius_km: float) -> list[Venue]:
        venues: list[Venue] = []
        seen: set[tuple[float, float]] = set()
        for _, row in gdf.iterrows():
            geom = row.geometry
            point = self._point_for(geom)
            if point is None:
                continue
            if haversine_km((lat, lon), point) > radius_km + 0.5:
                continue
            tags = {k: v for k, v in row.to_dict().items() if v is not None and not (isinstance(v, float) and math.isnan(v))}
            # drop geometry from tags
            tags.pop("geometry", None)
            if str(tags.get("access", "")).lower() in {"private", "no"}:
                continue
            kind = _classify(tags)
            name = tags.get("name") or tags.get("operator") or None
            if not name:
                name = f"{kind.replace('_', ' ').capitalize()} (no name in OpenStreetMap)"
            key = (round(point[0], 5), round(point[1], 5))
            if key in seen:
                continue
            seen.add(key)
            venues.append(
                Venue(
                    id=f"geofabrik/{row.get('id', len(venues))}",
                    name=name,
                    lat=point[0],
                    lon=point[1],
                    kind=kind,
                    tags=tags,
                    lit=str(tags.get("lit", "")).lower() in {"yes", "true", "1"},
                )
            )
            if len(venues) >= 200:
                break
        return venues

    def _hazards_from_gdf(self, gdf: Any) -> list[Hazard]:
        hazards: list[Hazard] = []
        for _, row in gdf.iterrows():
            geom = row.geometry
            tags = {k: v for k, v in row.to_dict().items() if v is not None and not (isinstance(v, float) and math.isnan(v))}
            tags.pop("geometry", None)
            highway = tags.get("highway")
            if highway in {"motorway", "trunk", "primary", "secondary"}:
                kind = "road"
                road_class = highway
            elif tags.get("landuse") == "industrial" or tags.get("man_made") == "works":
                kind = "industrial"
                road_class = None
            elif tags.get("power") == "plant":
                kind = "power"
                road_class = None
            elif tags.get("landuse") == "landfill":
                kind = "landfill"
                road_class = None
            elif tags.get("natural") == "water" or tags.get("waterway") in {
                "river",
                "canal",
                "riverbank",
                "dock",
            }:
                kind = "water"
                road_class = None
            elif tags.get("landuse") in {"forest", "grass", "meadow", "orchard"} or tags.get(
                "natural"
            ) in {"wood", "scrub", "grassland"} or tags.get("leisure") in {
                "park",
                "garden",
                "nature_reserve",
            }:
                kind = "green"
                road_class = None
            else:
                continue

            if geom is None:
                continue
            if geom.geom_type in {"LineString", "MultiLineString"}:
                if geom.geom_type == "MultiLineString":
                    lines = list(geom.geoms)
                else:
                    lines = [geom]
                for line in lines:
                    pts = [(float(y), float(x)) for x, y in line.coords]
                    if pts:
                        hazards.append(
                            Hazard(
                                id=f"geofabrik/{tags.get('id', len(hazards))}",
                                kind=kind,
                                road_class=road_class,
                                name=tags.get("name"),
                                points=pts,
                            )
                        )
            else:
                centroid = geom.centroid
                hazards.append(
                    Hazard(
                        id=f"geofabrik/{tags.get('id', len(hazards))}",
                        kind=kind,
                        road_class=road_class,
                        name=tags.get("name"),
                        points=[(float(centroid.y), float(centroid.x))],
                    )
                )
        return hazards

    async def get_venues(
        self, activity: str, lat: float, lon: float, radius_km: float | None = None
    ) -> SourceResult[list[Venue]]:
        if not self.enabled:
            return SourceResult(
                source=self.name, ok=False, params={"activity": activity}, error="disabled"
            )
        if activity not in self.activities:
            return SourceResult(
                source=self.name,
                ok=False,
                params={"activity": activity},
                error=f"Unsupported activity: {activity}",
            )
        act = self.activities[activity]
        radius = radius_km or act.get("radius_km", 5)
        selectors = act.get("overpass", [])
        custom_filter = _selectors_to_filter(selectors)
        cache_key = f"gf_venues:{activity}:{lat:.4f}:{lon:.4f}:{radius}"

        async def _fetch(self_src: BaseSource):
            zone = zone_for(lat, lon)
            path = await self_src._download_zone(zone)
            loop = asyncio.get_event_loop()

            def _read():
                osm = _PyrosmOSM(str(path), bounding_box=[lon - radius / 90.0, lat - radius / 111.0, lon + radius / 90.0, lat + radius / 111.0])
                if custom_filter:
                    gdf = osm.get_data_by_custom_criteria(custom_filter=custom_filter)
                else:
                    gdf = osm.get_pois()
                if gdf is None or len(gdf) == 0:
                    return []
                return self_src._venues_from_gdf(gdf, activity, lat, lon, radius)

            return await loop.run_in_executor(None, _read)

        try:
            data = await self.fetch(cache_key, settings.TTL_OVERPASS, _fetch, {"activity": activity})
            # self.fetch returns SourceResult; but _fetch result stored? BaseSource.fetch returns SourceResult with data.
            return data
        except Exception as exc:  # noqa: BLE001
            logger.warning("Geofabrik venues failed: %s", exc)
            return SourceResult(source=self.name, ok=False, params={"activity": activity}, error=str(exc))

    async def get_hazards(
        self, lat: float, lon: float, radius_km: float | None = None
    ) -> SourceResult[list[Hazard]]:
        if not self.enabled:
            return SourceResult(source=self.name, ok=False, params={}, error="disabled")
        radius = radius_km or 3.0
        cache_key = f"gf_hazards:{lat:.4f}:{lon:.4f}:{radius}"

        async def _fetch(self_src: BaseSource):
            zone = zone_for(lat, lon)
            path = await self_src._download_zone(zone)
            loop = asyncio.get_event_loop()

            def _read():
                osm = _PyrosmOSM(str(path), bounding_box=[lon - radius / 90.0, lat - radius / 111.0, lon + radius / 90.0, lat + radius / 111.0])
                custom_filter = {
                    "highway": ["motorway", "trunk", "primary", "secondary"],
                    "landuse": ["industrial", "landfill", "forest", "grass", "meadow", "orchard"],
                    "power": ["plant"],
                    "man_made": ["works"],
                    "natural": ["water", "wood", "scrub", "grassland"],
                    "waterway": ["river", "canal", "riverbank", "dock"],
                    "leisure": ["park", "garden", "nature_reserve"],
                }
                gdf = osm.get_data_by_custom_criteria(custom_filter=custom_filter)
                if gdf is None or len(gdf) == 0:
                    return []
                return self_src._hazards_from_gdf(gdf)

            return await loop.run_in_executor(None, _read)

        try:
            return await self.fetch(cache_key, settings.TTL_OVERPASS, _fetch, {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Geofabrik hazards failed: %s", exc)
            return SourceResult(source=self.name, ok=False, params={}, error=str(exc))
