"""OpenStreetMap access through the Overpass API.

Fixes that matter (each one was a real failure in the previous version):

* The query is POSTed in the **body** (`data=...`), not in the URL.
* The User-Agent must NOT start with "Mozilla/" — Overpass answers 406.
* `nwr(...)` is used instead of `node(...)`; most parks, pitches and tracks
  are mapped as **ways**, and the old node-only query could not see them.
* Tag filters are real tag regexes (`["sport"~"^(cricket)$"]`), not the
  meaningless `["sport"="*"]`.
* If Overpass fails we return **zero venues plus a limitation** — the old
  version invented "Community City Park" and friends, which the rulebook
  explicitly forbids.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
import yaml
from pydantic import BaseModel

from app.config import CONFIG_DIR, settings
from app.sources.base import BaseSource, SourceResult

logger = logging.getLogger("geoai.overpass")


class Venue(BaseModel):
    id: str
    name: str
    lat: float
    lon: float
    kind: str
    tags: dict[str, Any] = {}
    lit: bool = False
    area_m2: float | None = None


class Hazard(BaseModel):
    id: str
    kind: str  # road | industrial | power | landfill | water | green
    road_class: str | None = None
    name: str | None = None
    points: list[tuple[float, float]] = []


class OverpassSource(BaseSource):
    # Shared across requests: when the mirrors start refusing us, we stop
    # asking for a while instead of burning the user's time on every question.
    _cooldown_until: float = 0.0
    _consecutive_failures: int = 0

    def __init__(self):
        super().__init__("overpass", rate_limit=1)
        with open(CONFIG_DIR / "activities.yaml", "r", encoding="utf-8") as f:
            self.activities: dict[str, Any] = yaml.safe_load(f)
        with open(CONFIG_DIR / "hazards.yaml", "r", encoding="utf-8") as f:
            self.hazards_cfg: dict[str, Any] = yaml.safe_load(f)

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _center(element: dict) -> tuple[float, float] | None:
        lat = element.get("lat")
        lon = element.get("lon")
        if lat is None or lon is None:
            center = element.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            return None
        return float(lat), float(lon)

    @staticmethod
    def _points(element: dict) -> list[tuple[float, float]]:
        geom = element.get("geometry")
        if geom:
            pts = [
                (float(g["lat"]), float(g["lon"]))
                for g in geom
                if g.get("lat") is not None and g.get("lon") is not None
            ]
            if pts:
                return pts
        center = OverpassSource._center(element)
        return [center] if center else []

    async def _post_mirror(
        self, url: str, query: str, timeout: float
    ) -> dict | None:
        """One POST to one mirror. No circuit breaker here: the mirror list
        is the redundancy, and a shared breaker used to disable every later
        Overpass call after three failures."""
        headers = {
            "User-Agent": settings.user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                headers=headers, timeout=timeout, follow_redirects=True
            ) as client:
                resp = await client.post(url, data={"data": query})
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, dict) else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Overpass mirror %s failed: %s", url, exc)
            return None

    async def _run(self, query: str, budget: float | None = None) -> dict | None:
        """POST the query to each mirror until one answers, inside a budget."""
        if time.monotonic() < OverpassSource._cooldown_until:
            logger.warning("Overpass is in a cooldown, skipping the lookup")
            return None
        budget = budget if budget is not None else settings.OVERPASS_BUDGET_S
        deadline = time.monotonic() + budget
        for mirror in settings.OVERPASS_MIRRORS:
            remaining = deadline - time.monotonic()
            if remaining <= 1:
                logger.warning("Overpass budget exhausted, stopping mirror loop")
                break
            data = await self._post_mirror(
                mirror, query, timeout=min(settings.OVERPASS_TIMEOUT, remaining)
            )
            if data is not None:
                OverpassSource._consecutive_failures = 0
                OverpassSource._cooldown_until = 0.0
                return data
        OverpassSource._consecutive_failures += 1
        if OverpassSource._consecutive_failures >= 2:
            OverpassSource._cooldown_until = time.monotonic() + 60
            logger.warning(
                "Overpass failed %s times in a row, cooling down for 60s",
                OverpassSource._consecutive_failures,
            )
        return None

    # ----------------------------------------------------------------- venues
    async def get_venues(
        self, activity: str, lat: float, lon: float, radius_km: float | None = None
    ) -> SourceResult[list[Venue]]:
        if activity not in self.activities:
            return SourceResult(
                source=self.name,
                ok=False,
                params={"activity": activity},
                error=f"Unsupported activity: {activity}",
            )

        act = self.activities[activity]
        radius_m = int((radius_km or act.get("radius_km", 5)) * 1000)
        selectors: list[str] = act.get("overpass", [])
        if not selectors:
            return SourceResult(
                source=self.name,
                ok=True,
                params={"activity": activity},
                data=[],
            )

        parts = "\n".join(
            f'  nwr(around:{radius_m},{lat:.5f},{lon:.5f}){sel};' for sel in selectors
        )
        # The element limit is applied by the server, so a low limit would
        # throw away most of the candidates before the ranking sees them. We
        # ask for a generous number and cut the shortlist in python.
        query = f"[out:json][timeout:30];(\n{parts}\n);\nout center tags {act.get('limit', 1500)};"
        cache_key = f"venues:{activity}:{lat:.4f}:{lon:.4f}:{radius_m}"

        async def _fetch(self_src: BaseSource):
            data = await self_src._run(query)
            if data is None:
                raise RuntimeError("all Overpass mirrors failed")

            venues: list[Venue] = []
            seen: set[tuple[float, float]] = set()
            for el in data.get("elements", []):
                tags = el.get("tags") or {}
                center = self_src._center(el)
                if not center:
                    continue
                key = (round(center[0], 5), round(center[1], 5))
                if key in seen:
                    continue
                seen.add(key)

                kind = self_src._classify(tags)
                name = tags.get("name") or tags.get("operator") or None
                if not name:
                    # Honest about what we know: the feature type is known,
                    # its name is not in OpenStreetMap.
                    name = f"{kind.replace('_', ' ').capitalize()} (no name in OpenStreetMap)"
                venues.append(
                    Venue(
                        id=f"{el.get('type', 'node')}/{el.get('id')}",
                        name=name,
                        lat=center[0],
                        lon=center[1],
                        kind=kind,
                        tags=tags,
                        lit=str(tags.get("lit", "")).lower() in {"yes", "true", "1"},
                    )
                )

            # Prefer named, well-known venue types, then keep the shortlist tight.
            def rank(v: Venue) -> tuple:
                named = 0 if v.name.startswith("Unnamed") else 1
                has_sport = 1 if v.tags.get("sport") else 0
                return (-named, -has_sport, v.name)

            venues.sort(key=rank)
            return venues[: act.get("shortlist", 12)]

        result = await self.fetch(
            cache_key, settings.TTL_OVERPASS, _fetch, {"activity": activity, "radius_m": radius_m}
        )
        if result.ok and not result.data:
            result.degraded = True
        return result

    @staticmethod
    def _classify(tags: dict) -> str:
        leisure = tags.get("leisure", "")
        if leisure in {"golf_course"}:
            return "golf_course"
        if leisure in {"tennis_court"}:
            return "tennis_court"
        if leisure in {"track", "fitness_track", "running_track"}:
            return "track"
        if leisure in {"pitch"}:
            return "pitch"
        if leisure in {"park", "garden", "nature_reserve", "playground", "recreation_ground"}:
            return "park"
        if leisure in {"sports_centre"}:
            return "sports_centre"
        if tags.get("route") == "hiking" or tags.get("highway") in {"path", "footway", "steps", "bridleway"}:
            return "trail"
        if tags.get("natural") == "peak":
            return "peak"
        if tags.get("tourism") == "viewpoint":
            return "viewpoint"
        if tags.get("tourism") == "picnic_site":
            return "picnic_site"
        if tags.get("natural") == "water" or tags.get("waterway"):
            return "waterfront"
        if tags.get("amenity") in {"community_centre", "school", "college"}:
            return "community_centre"
        if tags.get("highway") == "cycleway":
            return "cycleway"
        return "other"

    # ----------------------------------------------------------------- hazards
    async def get_hazards(
        self, lat: float, lon: float, radius_km: float = 5.0
    ) -> SourceResult[list[Hazard]]:
        """Hazard sources and mitigators around the user's area.

        Two Overpass queries run in parallel: roads and industry need the full
        geometry (a motorway's centre point can be far from the road itself),
        while water and green cover only need a centre point. Splitting them
        keeps each query small enough to answer in a few seconds.
        """
        radius_m = int(radius_km * 1000)

        def sel(template: str) -> str:
            return template % (radius_m, lat, lon)

        # One query, not two: the public mirrors throttle under load and each
        # round trip costs the user seconds. The output mode keeps the full
        # geometry, so the distance to a road is measured to the road itself
        # and not to the middle of its bounding box.
        hazard_sel = [
            sel('way(around:%d,%.5f,%.5f)["highway"~"^(motorway|trunk|primary|secondary)$"]'),
            sel('nwr(around:%d,%.5f,%.5f)["landuse"="industrial"]'),
            sel('nwr(around:%d,%.5f,%.5f)["power"="plant"]'),
            sel('nwr(around:%d,%.5f,%.5f)["man_made"="works"]'),
            sel('nwr(around:%d,%.5f,%.5f)["landuse"="landfill"]'),
            sel('nwr(around:%d,%.5f,%.5f)["natural"="water"]'),
            sel('nwr(around:%d,%.5f,%.5f)["waterway"~"^(river|canal|riverbank|dock)$"]'),
            sel('nwr(around:%d,%.5f,%.5f)["landuse"~"^(forest|grass|meadow|orchard)$"]'),
            sel('nwr(around:%d,%.5f,%.5f)["leisure"~"^(park|garden|nature_reserve)$"]'),
        ]
        q_hazard = (
            "[out:json][timeout:20];(\n  "
            + ";\n  ".join(hazard_sel)
            + ";\n);\nout geom tags 800;"
        )
        cache_key = f"hazards:{lat:.4f}:{lon:.4f}:{radius_m}"

        async def _fetch(self_src: OverpassSource):
            data = await self_src._run(q_hazard)
            if data is None:
                raise RuntimeError("all Overpass mirrors failed")
            hazards: list[Hazard] = []
            if data:
                for el in data.get("elements", []):
                    tags = el.get("tags") or {}
                    kind = self_src._hazard_kind(tags)
                    if not kind:
                        continue
                    pts = self_src._points(el)
                    if not pts:
                        continue
                    hazards.append(
                        Hazard(
                            id=f"{el.get('type', 'node')}/{el.get('id')}",
                            kind=kind,
                            road_class=tags.get("highway"),
                            name=tags.get("name"),
                            points=pts[:400],
                        )
                    )
            return hazards

        result = await self.fetch(
            cache_key, settings.TTL_OVERPASS, _fetch, {"radius_m": radius_m}
        )
        if result.ok and not result.data:
            result.degraded = True
        return result

    @staticmethod
    def _hazard_kind(tags: dict) -> str | None:
        if tags.get("highway") in {
            "motorway",
            "trunk",
            "primary",
            "secondary",
            "tertiary",
        }:
            return "road"
        if tags.get("landuse") == "industrial":
            return "industrial"
        if tags.get("power") == "plant" or tags.get("power") in {"generator", "substation"}:
            return "power"
        if tags.get("landuse") == "landfill" or tags.get("man_made") == "works":
            return "landfill"
        if tags.get("natural") == "water" or tags.get("waterway") in {
            "river",
            "canal",
            "stream",
            "riverbank",
            "dock",
        }:
            return "water"
        if tags.get("landuse") in {
            "forest",
            "grass",
            "meadow",
            "village_green",
            "orchard",
        } or tags.get("natural") in {"wood", "scrub", "grassland", "wetland"}:
            return "green"
        if tags.get("leisure") in {"park", "garden", "nature_reserve"}:
            return "green"
        return None
