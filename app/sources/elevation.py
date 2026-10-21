from typing import Any

from app.config import settings
from app.sources.base import BaseSource, SourceResult


class ElevationSource(BaseSource):
    def __init__(self):
        super().__init__("elevation")

    async def get_elevation(self, lats: list[float], lons: list[float]) -> SourceResult[Any]:
        # Open-Meteo elevation API
        # coords: lat,lon;lat,lon...
        coords = ";".join([f"{lat},{lon}" for lat, lon in zip(lats, lons)])
        url = f"https://api.open-meteo.com/v1/elevation?coords={coords}"

        cache_key = f"elev:{hash(tuple(zip(lats, lons)))}"

        async def _fetch(self_src: BaseSource):
            resp = await self_src._request("GET", url)
            return resp.json()

        return await self.fetch(cache_key, settings.TTL_OVERPASS, _fetch, {"url": url})
