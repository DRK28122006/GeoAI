from typing import Any

from app.config import settings
from app.sources.base import BaseSource, SourceResult


class RoutingSource(BaseSource):
    def __init__(self):
        super().__init__("routing", rate_limit=5)

    async def get_travel_times(self, origin: tuple[float, float], destinations: list[tuple[float, float]]) -> SourceResult[Any]:
        # OSRM /table endpoint
        # /table/v1/driving/{coords}
        # coords: lon,lat;lon,lat...

        coords = []
        coords.append(f"{origin[1]},{origin[0]}") # lon,lat
        for d in destinations:
            coords.append(f"{d[1]},{d[0]}")

        url = f"http://router.project-osrm.org/table/v1/driving/{';'.join(coords)}"

        # Cache based on origin and set of destinations
        cache_key = f"route:{origin}:{hash(tuple(destinations))}"

        async def _fetch(self_src: BaseSource):
            resp = await self_src._request("GET", url)
            return resp.json()

        return await self.fetch(cache_key, settings.TTL_ROUTING, _fetch, {"url": url})
