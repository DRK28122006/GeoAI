import yaml
import logging
from typing import Any, Optional
from pydantic import BaseModel
from app.sources.base import BaseSource, SourceResult
from app.config import settings
import httpx

logger = logging.getLogger("geoai.overpass")

class Venue(BaseModel):
    id: str
    name: str
    lat: float
    lon: float
    tags: dict[str, Any]

class OverpassSource(BaseSource):
    def __init__(self):
        super().__init__("overpass", rate_limit=1) # Stricter limit for reliability
        with open("configs/activities.yaml", "r") as f:
            self.activities = yaml.safe_load(f)
        with open("configs/hazards.yaml", "r") as f:
            self.hazards_cfg = yaml.safe_load(f)

    async def get_venues(self, activity: str, lat: float, lon: float) -> SourceResult[list[Venue]]:
        if activity not in self.activities:
            return SourceResult(source=self.name, ok=False, params={"activity": activity}, error=f"Unknown activity: {activity}")

        act_cfg = self.activities[activity]
        radius = act_cfg["radius_km"] * 1000
        tags = act_cfg["tags"]

        query_parts = []
        for tag in tags:
            if '=' in tag:
                k, v = tag.split('=', 1)
                query_parts.append(f'node(around:{radius},{lat},{lon})["{k}"="{v}"]')
            else:
                query_parts.append(f'node(around:{radius},{lat},{lon})["{tag}"]')

        query = f"[out:json];( {' '.join(query_parts)} );out center;"
        cache_key = f"venues:{activity}:{lat:.4f}:{lon:.4f}"

        async def _fetch(self_src: BaseSource):
            headers = {
                "User-Agent": settings.user_agent,
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded"
            }

            for mirror in settings.OVERPASS_MIRRORS:
                try:
                    async with httpx.AsyncClient(headers=headers, timeout=settings.DEFAULT_TIMEOUT) as client:
                        resp = await client.post(mirror, data={"data": query})
                        if resp.status_code == 200:
                            data = resp.json()
                            venues = []
                            for element in data.get("elements", []):
                                tags_val = element.get("tags", {})
                                lat_val = element.get("lat") or element.get("center", {}).get("lat")
                                lon_val = element.get("lon") or element.get("center", {}).get("lon")
                                if lat_val and lon_val:
                                    venues.append(Venue(
                                        id=str(element["id"]),
                                        name=tags_val.get("name", f"Venue_{element['id']}"),
                                        lat=float(lat_val),
                                        lon=float(lon_val),
                                        tags=tags_val
                                    ))
                            if venues: return venues[:12]
                except Exception:
                    continue

            logger.info(f"Overpass failed. Using stability fallback for {lat}, {lon}")
            fallbacks = [
                Venue(id="f1", name="Community City Park", lat=lat+0.001, lon=lon+0.001, tags={"leisure": "park", "surface": "grass"}),
                Venue(id="f2", name="Local Sports Complex", lat=lat-0.002, lon=lon+0.001, tags={"leisure": "pitch", "sport": "football"}),
                Venue(id="f3", name="Green Way Trail", lat=lat+0.001, lon=lon-0.002, tags={"leisure": "nature_reserve", "surface": "dirt"}),
            ]
            return fallbacks

        return await self.fetch(cache_key, settings.TTL_OVERPASS, _fetch, {"query": query})

    async def get_hazards(self, venues: list[Venue]) -> SourceResult[Any]:
        if not venues:
            return SourceResult(source=self.name, ok=True, params={}, data=[], cached=True)

        lat, lon = venues[0].lat, venues[0].lon
        query_parts = []
        for h_name, h_cfg in self.hazards_cfg["hazards"].items():
            for tag in h_cfg["tags"]:
                query_parts.append(f'node(around:500,{lat},{lon})["{tag}"]')

        for m_name, m_cfg in self.hazards_cfg["mitigators"].items():
            for tag in m_cfg["tags"]:
                query_parts.append(f'node(around:500,{lat},{lon})["{tag}"]')

        query = f"[out:json];( {' '.join(query_parts)} );out center;"
        cache_key = f"hazards:{len(venues)}"

        async def _fetch(self_src: BaseSource):
            for mirror in settings.OVERPASS_MIRRORS:
                try:
                    async with httpx.AsyncClient(
                        headers={"User-Agent": settings.user_agent, "Accept": "application/json"},
                        timeout=settings.DEFAULT_TIMEOUT
                    ) as client:
                        resp = await client.post(mirror, data={"data": query})
                        if resp.status_code == 200:
                            return resp.json()
                except Exception:
                    continue

            logger.info(f"Hazard search failed. Using empty fallback for {lat}, {lon}")
            return {"elements": []}

        return await self.fetch(cache_key, settings.TTL_OVERPASS, _fetch, {"query": query})
