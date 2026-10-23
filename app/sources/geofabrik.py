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
the user's coordinates is picked from Geofabrik's own boundary files
(`app/sources/geofabrik_zones.py`).

Three rules keep this safe in front of a judge:

* **A request never waits for a download.** The extract is fetched in the
  background; until it is ready the answer comes from Overpass. The
  in-request attempt has a hard, short timeout
  (`GEOFABRIK_REQUEST_TIMEOUT_S`), so a user cannot be made to wait minutes
  for a 500 MB file.
* **A zone that is too big is skipped.** The limit is `GEOFABRIK_MAX_MB`
  (300 MB by default), so the southern (533 MB) and central (336 MB) zones
  are refused and those regions are answered from Overpass.
* **Every failure falls back to Overpass.** No extract, a broken download, an
  unreadable file, a zone that does not cover the point: the answer is still
  an answer, never an error and never an empty venue list.

The feature is **off by default** (`GEOFABRIK_ENABLED`), because a
pre-downloaded extract cannot be guaranteed before a demo. Turn it on with
`GEOFABRIK_ENABLED=true` and give the container some time to fetch the
extracts: the first request for a zone starts the download, and the requests
after that are answered from the local file.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import load_config, settings
from app.exposure_engine import haversine_km
from app.sources.base import BaseSource, SourceResult
from app.sources.overpass import Hazard, OverpassSource, Venue

logger = logging.getLogger("geoai.geofabrik")

# Geofabrik publishes the Indian zones as
# `<zone>-zone-latest.osm.pbf` under `/asia/india/`.
GEOFABRIK_BASE_URL = "https://download.geofabrik.de/asia/india"

# The size of each zone extract in MB, as published by Geofabrik. The
# numbers move a little every week. They are used to refuse a zone *before*
# the download starts, so we do not spend the bandwidth; the real check is
# the Content-Length of the download itself.
ZONE_SIZES_MB: dict[str, float] = {
    "central-zone": 336.0,
    "eastern-zone": 236.0,
    "north-eastern-zone": 105.0,
    "northern-zone": 214.0,
    "southern-zone": 533.0,
    "western-zone": 211.0,
}

# The tag columns the venue and hazard readers ask pyrosm for. The rest of
# the tags arrive in the JSON column and are merged in afterwards.
VENUE_TAG_COLUMNS = [
    "name",
    "operator",
    "access",
    "lit",
    "leisure",
    "sport",
    "landuse",
    "amenity",
    "natural",
    "highway",
    "waterway",
    "man_made",
    "power",
    "building",
    "tourism",
    "route",
    "shop",
    "public_transport",
    "leisure",
    "opening_hours",
]
HAZARD_TAG_COLUMNS = [
    "name",
    "highway",
    "landuse",
    "power",
    "man_made",
    "natural",
    "waterway",
    "leisure",
]

# The columns pyrosm adds to every row that are not tags.
_META_COLUMNS = {"id", "timestamp", "version", "osm_type", "geometry", "tags"}

# An Overpass statement looks like `nwr(around:1000,12.9,77.5)["a"="b"]`.
# pyrosm wants the bracket part on its own.
_OVERPASS_STATEMENT = re.compile(r"^\s*(?:nwr|way|node|rel)\s*\([^)]*\)\s*", re.IGNORECASE)

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


# --------------------------------------------------------------------- zones
def zone_url(zone: str) -> str:
    """The Geofabrik download URL of one Indian zone.

    Geofabrik names the files `<zone>-zone-latest.osm.pbf` inside
    `/asia/india/`, so the `central` zone is
    `https://download.geofabrik.de/asia/india/central-zone-latest.osm.pbf`
    and the `north-eastern` zone is `.../north-eastern-zone-latest.osm.pbf`.
    The zone names used here already carry the `-zone` suffix, so it is
    added only when it is missing.
    """
    slug = (zone or "").strip().lower()
    if not slug:
        raise ValueError("no zone given")
    if not slug.endswith("-zone"):
        slug = f"{slug}-zone"
    return f"{GEOFABRIK_BASE_URL}/{slug}-latest.osm.pbf"


def zone_file_name(zone: str) -> str:
    """The name the extract of a zone has on disk."""
    return Path(zone_url(zone)).name


# The prepared polygons, so the boundary is not rebuilt on every request.
_ZONE_POLYGONS: dict[str, tuple[Any, ...]] = {}


def _zone_polygons(zone: str) -> tuple[Any, ...]:
    """The polygons of one zone. A zone can have more than one ring."""
    cached = _ZONE_POLYGONS.get(zone)
    if cached is None:
        polygons = []
        for ring in ZONE_POLYGONS.get(zone) or ():
            try:
                polygons.append(Polygon(ring))
            except Exception:  # noqa: BLE001, S112 - a broken ring is skipped
                continue
        cached = tuple(polygons)
        _ZONE_POLYGONS[zone] = cached
    return cached


def zone_for(lat: float, lon: float) -> str | None:
    """The Geofabrik zone that contains the point, or None.

    None means "do not use a local extract for this point": the boundaries
    are not available, or the point is not inside any zone. The caller then
    answers from Overpass, which is the safe answer - guessing a zone would
    download the wrong extract and answer from the wrong map.
    """
    if not (_ZONES_AVAILABLE and _SHAPELY_AVAILABLE):
        return None
    try:
        point = Point(float(lon), float(lat))
    except Exception:  # noqa: BLE001
        return None
    for zone in ZONE_POLYGONS:
        for polygon in _zone_polygons(zone):
            try:
                # `covers` and not `contains`: a point exactly on the
                # boundary still belongs to the zone.
                if polygon.covers(point):
                    return zone
            except Exception:  # noqa: BLE001, S112
                continue
    return None


def bracket_filters(selectors: list[str]) -> list[str]:
    """The Overpass selectors as pyrosm bracket filters.

    pyrosm reads a list of bracket strings as the OR of the strings and the
    AND of the brackets inside one string. That is exactly what the Overpass
    query does: one statement per selector, all of them unioned.
    """
    filters: list[str] = []
    for selector in selectors or []:
        cleaned = _OVERPASS_STATEMENT.sub("", str(selector).strip())
        if cleaned.startswith("[") and cleaned.endswith("]"):
            filters.append(cleaned)
    return filters


def _row_tags(row: Any) -> dict[str, Any]:
    """The tags of one pyrosm row.

    pyrosm keeps the requested tag keys as columns and puts the remaining
    tags in a JSON column, so the two have to be merged before the tags can
    be read.
    """
    tags: dict[str, Any] = {}
    raw = row.get("tags")
    if isinstance(raw, str) and raw.strip():
        try:
            extra = json.loads(raw)
        except (TypeError, ValueError):
            extra = {}
        if isinstance(extra, dict):
            tags.update({k: v for k, v in extra.items() if v is not None})
    for key, value in row.to_dict().items():
        if key in _META_COLUMNS or value is None:
            continue
        if isinstance(value, float) and math.isnan(value):
            continue
        tags[key] = value
    return tags


def _rank_venue(venue: Venue) -> tuple:
    """The same order the Overpass path uses, so both answers look alike."""
    named = 0 if venue.name.startswith(("Unnamed", "Park (", "Pitch (")) else 1
    has_sport = 1 if venue.tags.get("sport") else 0
    notable = 1 if (venue.tags.get("wikidata") or venue.tags.get("wikipedia")) else 0
    readable = 1 if len(venue.name) > 8 and " " in venue.name else 0
    known = 0 if venue.kind in {"other", "unclassified"} else 1
    return (-named, -known, -notable, -readable, -has_sport, venue.name)


# ----------------------------------------------------------------- downloads
# One download per zone at a time, whatever the number of requests.
_DOWNLOADS: dict[str, asyncio.Task] = {}
# (bytes written, expected bytes) of the download that is in flight.
_PROGRESS: dict[str, tuple[int, int]] = {}


def _read_int(path: str) -> int | None:
    """The integer in a file, or None when it is missing or not a number."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read().strip()
    except OSError:
        return None
    if not raw or raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def memory_headroom_bytes() -> int | None:
    """How much memory this process may still use, in bytes.

    The container is capped (3 GB), so the free memory of the host says
    nothing: what matters is the room left in the cgroup. When that cannot
    be read the host's available memory is the best guess available.
    """
    base = Path("/sys/fs/cgroup")
    limit_file = base / "memory.max"
    used_file = base / "memory.current"
    if not (limit_file.exists() and used_file.exists()):
        # The process may live in a sub-cgroup (systemd, a container runtime).
        try:
            line = next(
                line
                for line in Path("/proc/self/cgroup").read_text().splitlines()
                if line.startswith("0::")
            )
            relative = line.split("::", 1)[1].lstrip("/")
        except (OSError, StopIteration):
            relative = ""
        if relative:
            limit_file = base / relative / "memory.max"
            used_file = base / relative / "memory.current"
    limit = _read_int(str(limit_file))
    used = _read_int(str(used_file))
    if limit is not None and used is not None and limit > 0:
        return max(0, limit - used)
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _content_length(resp: httpx.Response) -> int | None:
    """The size the server says the body has, in bytes."""
    raw = resp.headers.get("content-length")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def download_zone(zone: str, max_bytes: int, timeout_s: float) -> Path | None:
    """Download one zone extract. Never raises.

    A failed download is a normal outcome: the caller answers from Overpass
    and a later request tries again. The file is refused when it is bigger
    than `max_bytes`, so a zone that does not fit in the container is never
    written to disk in the first place.
    """
    path = Path(settings.CACHE_DIR) / "geofabrik" / zone_file_name(zone)
    tmp = path.with_suffix(".pbf.part")
    url = zone_url(zone)
    timeout = httpx.Timeout(timeout_s, connect=30.0)
    written = 0
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": settings.user_agent},
        ) as client, client.stream("GET", url) as resp:
            resp.raise_for_status()
            expected = _content_length(resp)
            if expected is not None and expected > max_bytes:
                logger.warning(
                    "the %s extract is %d MB, over the %d MB limit: not downloading it",
                    zone,
                    expected // (1024 * 1024),
                    max_bytes // (1024 * 1024),
                )
                return None
            too_big = False
            with open(tmp, "wb") as handle:  # noqa: ASYNC230
                async for chunk in resp.aiter_bytes(chunk_size=1 << 20):
                    written += len(chunk)
                    if written > max_bytes:
                        too_big = True
                        break
                    await asyncio.to_thread(handle.write, chunk)
                    _PROGRESS[zone] = (written, expected or 0)
            if too_big:
                logger.warning(
                    "the %s extract grew past the %d MB limit: not keeping it",
                    zone,
                    max_bytes // (1024 * 1024),
                )
                tmp.unlink(missing_ok=True)
                return None
        tmp.replace(path)
        logger.info(
            "the %s extract is ready (%d MB)", zone, written // (1024 * 1024)
        )
        return path
    except Exception as exc:  # noqa: BLE001 - a failed download is not an error
        logger.warning("the Geofabrik download of the %s extract failed: %s", zone, exc)
        tmp.unlink(missing_ok=True)
        return None


def _download_done(task: asyncio.Task) -> None:
    """Keep a failed background download out of the event loop."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("the background Geofabrik download failed: %s", exc)


def start_download(zone: str) -> asyncio.Task | None:
    """Start the background download of a zone, or reuse the one running."""
    task = _DOWNLOADS.get(zone)
    if task is not None and not task.done():
        return task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    max_bytes = int(settings.GEOFABRIK_MAX_MB * 1024 * 1024)
    task = loop.create_task(
        download_zone(zone, max_bytes, settings.GEOFABRIK_DOWNLOAD_TIMEOUT_S)
    )
    # The task must never raise into the event loop.
    task.add_done_callback(_download_done)
    _DOWNLOADS[zone] = task
    return task


def _nearly_done(zone: str) -> bool:
    """Whether the download in flight is about to finish.

    Only then is it worth waiting a few seconds for it inside a request.
    """
    written, expected = _PROGRESS.get(zone, (0, 0))
    if expected <= 0:
        # The size is unknown, so we cannot tell: wait, but only briefly.
        return True
    return written / expected >= 0.8


# ------------------------------------------------------------------- source
class GeofabrikSource(BaseSource):
    def __init__(self):
        super().__init__("geofabrik")
        self.activities = load_config("activities")
        self.hazards_cfg = load_config("hazards")

    @property
    def enabled(self) -> bool:
        return bool(settings.GEOFABRIK_ENABLED and _PYROSM_AVAILABLE)

    @property
    def max_bytes(self) -> int:
        """The largest extract we are willing to load, in bytes."""
        return int(settings.GEOFABRIK_MAX_MB * 1024 * 1024)

    def _cache_path(self, zone: str) -> Path:
        return Path(settings.CACHE_DIR) / "geofabrik" / zone_file_name(zone)

    def _fresh(self, path: Path) -> bool:
        """Whether the extract is on disk and recent enough to use."""
        try:
            if not path.exists():
                return False
            return (time.time() - path.stat().st_mtime) < settings.TTL_GEOFABRIK
        except OSError:
            return False

    def zone_over_limit(self, zone: str) -> bool:
        """Whether the extract of this zone is too big to load.

        The known size of the zone is checked first, so the download is not
        even started for a zone we would refuse. A file that is already on
        disk is checked as well, in case the limit was lowered after it was
        downloaded.
        """
        known_mb = ZONE_SIZES_MB.get(zone)
        if known_mb is not None and known_mb > settings.GEOFABRIK_MAX_MB:
            return True
        path = self._cache_path(zone)
        try:
            return path.exists() and path.stat().st_size > self.max_bytes
        except OSError:
            return False

    def can_load(self, path: Path) -> bool:
        """Whether the extract may be parsed in this container.

        `pyrosm` reads the extract in a streaming pass with a bounded peak
        memory, but the decode still needs room, and the container is
        capped. When there is not enough room the answer comes from
        Overpass instead.
        """
        try:
            size = path.stat().st_size
        except OSError:
            return False
        if size > self.max_bytes:
            return False
        headroom = memory_headroom_bytes()
        if headroom is None:
            return True
        free_mb = settings.GEOFABRIK_MIN_FREE_MB
        return (headroom - size) >= free_mb * 1024 * 1024

    async def _extract(self, zone: str) -> Path | None:
        """The ready extract of a zone, or None.

        A request never waits for a long download. When the extract is not
        there yet the download is started in the background and the caller
        falls back to Overpass; a later request finds the file.
        """
        try:
            return await self._extract_or_none(zone)
        except Exception as exc:  # noqa: BLE001 - a failure is not an error
            logger.warning("the %s extract could not be used: %s", zone, exc)
            return None

    async def _extract_or_none(self, zone: str) -> Path | None:
        path = self._cache_path(zone)
        if self._fresh(path):
            if self.can_load(path):
                return path
            logger.warning(
                "the %s extract is not loaded: not enough memory in the container", zone
            )
            return None
        if self.zone_over_limit(zone):
            logger.info(
                "the %s extract is over the %d MB limit: using Overpass instead",
                zone,
                settings.GEOFABRIK_MAX_MB,
            )
            return None
        task = _DOWNLOADS.get(zone)
        if task is None or task.done():
            # Nothing is running: start it for a later request and answer
            # from Overpass now.
            start_download(zone)
            return None
        if not _nearly_done(zone):
            # The download is minutes away. Waiting would only make the
            # answer later.
            return None
        try:
            # `shield` so that the timeout does not cancel the download.
            await asyncio.wait_for(
                asyncio.shield(task), timeout=settings.GEOFABRIK_REQUEST_TIMEOUT_S
            )
        except Exception as exc:  # noqa: BLE001 - not ready is not an error
            logger.info("the %s extract is not ready yet: %s", zone, exc)
            return None
        if self._fresh(path) and self.can_load(path):
            return path
        return None

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

    def _venues_from_gdf(
        self, gdf: Any, activity: str, lat: float, lon: float, radius_km: float
    ) -> list[Venue]:
        venues: list[Venue] = []
        seen: set[tuple[float, float]] = set()
        for _, row in gdf.iterrows():
            point = self._point_for(row.geometry)
            if point is None:
                continue
            if haversine_km((lat, lon), point) > radius_km + 0.5:
                continue
            tags = _row_tags(row)
            # A private pitch is not somewhere we can send a person.
            if str(tags.get("access", "")).lower() in {"private", "no"}:
                continue
            kind = _classify(tags)
            name = tags.get("name") or tags.get("operator") or None
            if not name:
                # Honest about what we know: the feature type is known,
                # its name is not in OpenStreetMap.
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
        venues.sort(key=_rank_venue)
        return venues

    def _hazards_from_gdf(self, gdf: Any) -> list[Hazard]:
        hazards: list[Hazard] = []
        for _, row in gdf.iterrows():
            geom = row.geometry
            tags = _row_tags(row)
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

    def _bbox(self, lat: float, lon: float, radius_km: float) -> list[float]:
        """The bounding box of the search, as pyrosm wants it."""
        return [
            lon - radius_km / 90.0,
            lat - radius_km / 111.0,
            lon + radius_km / 90.0,
            lat + radius_km / 111.0,
        ]

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
        custom_filter = bracket_filters(selectors)
        cache_key = f"gf_venues:{activity}:{lat:.4f}:{lon:.4f}:{radius}"

        zone = zone_for(lat, lon)
        path = await self._extract(zone) if zone else None
        if path is None:
            # No local extract. The caller answers from Overpass, and the
            # download is already running in the background.
            return SourceResult(
                source=self.name,
                ok=False,
                params={"activity": activity, "zone": zone},
                error="the local extract is not ready",
            )

        async def _fetch(self_src: BaseSource):
            loop = asyncio.get_event_loop()

            def _read():
                osm = _PyrosmOSM(str(path), bounding_box=self_src._bbox(lat, lon, radius))
                if custom_filter:
                    gdf = osm.get_data_by_custom_criteria(
                        custom_filter=custom_filter, tags_as_columns=VENUE_TAG_COLUMNS
                    )
                else:
                    gdf = osm.get_pois()
                if gdf is None or len(gdf) == 0:
                    return []
                return self_src._venues_from_gdf(gdf, activity, lat, lon, radius)

            return await loop.run_in_executor(None, _read)

        try:
            result = await self.fetch(
                cache_key,
                settings.TTL_OVERPASS,
                _fetch,
                {"activity": activity, "zone": zone},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Geofabrik venues failed: %s", exc)
            return SourceResult(
                source=self.name, ok=False, params={"activity": activity}, error=str(exc)
            )
        if result.ok and not result.data:
            # No venue in the local extract: the caller falls back to
            # Overpass rather than answering with an empty list.
            result.degraded = True
        return result

    async def get_hazards(
        self, lat: float, lon: float, radius_km: float | None = None
    ) -> SourceResult[list[Hazard]]:
        if not self.enabled:
            return SourceResult(source=self.name, ok=False, params={}, error="disabled")
        radius = radius_km or 3.0
        cache_key = f"gf_hazards:{lat:.4f}:{lon:.4f}:{radius}"
        groups = self.hazards_cfg.get("overpass", {})
        selectors = list(groups.get("roads", [])) + list(groups.get("other", []))
        custom_filter = bracket_filters(selectors)

        zone = zone_for(lat, lon)
        path = await self._extract(zone) if zone else None
        if path is None:
            return SourceResult(
                source=self.name,
                ok=False,
                params={"zone": zone},
                error="the local extract is not ready",
            )

        async def _fetch(self_src: BaseSource):
            loop = asyncio.get_event_loop()

            def _read():
                osm = _PyrosmOSM(str(path), bounding_box=self_src._bbox(lat, lon, radius))
                gdf = osm.get_data_by_custom_criteria(
                    custom_filter=custom_filter, tags_as_columns=HAZARD_TAG_COLUMNS
                )
                if gdf is None or len(gdf) == 0:
                    return []
                return self_src._hazards_from_gdf(gdf)

            return await loop.run_in_executor(None, _read)

        try:
            result = await self.fetch(
                cache_key, settings.TTL_OVERPASS, _fetch, {"zone": zone}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Geofabrik hazards failed: %s", exc)
            return SourceResult(source=self.name, ok=False, params={}, error=str(exc))
        if result.ok and not result.data:
            result.degraded = True
        return result


def _classify(tags: dict[str, Any]) -> str:
    return OverpassSource._classify(tags)
