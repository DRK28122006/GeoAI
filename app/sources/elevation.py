from typing import Any

from app.config import settings
from app.sources.base import BaseSource, SourceResult


class ElevationSource(BaseSource):
    def __init__(self):
        super().__init__("elevation")

    async def get_elevation(self, lats: list[float], lons: list[float]) -> SourceResult[Any]:
        # The Open-Meteo elevation endpoint takes latitude/longitude lists
        # (the "coords" parameter is rejected with 400).
        if not lats:
            return SourceResult(source=self.name, ok=True, params={}, data=[])
        lats = lats[:20]
        lons = lons[:20]
        params = {
            "latitude": ",".join(f"{v:.5f}" for v in lats),
            "longitude": ",".join(f"{v:.5f}" for v in lons),
        }

        cache_key = f"elev:{hash(tuple(zip(lats, lons)))}"

        async def _fetch(self_src: BaseSource):
            resp = await self_src._request(
                "GET", "https://api.open-meteo.com/v1/elevation", params=params
            )
            body = resp.json()
            # The endpoint answers with {"elevation": [...], ...}
            if isinstance(body, dict):
                return list(body.get("elevation") or [])
            return list(body)

        return await self.fetch(cache_key, settings.TTL_ELEVATION, _fetch, params)
