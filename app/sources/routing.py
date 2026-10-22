"""Travel time from the user's location to every shortlisted venue.

One OSRM `/table` call covers the whole shortlist. The public demo server is
fair-use only, so responses are cached and the table is capped. When OSRM is
unavailable the engine falls back to a straight-line estimate, which is
flagged as a limitation in the response.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.config import settings
from app.sources.base import BaseSource, SourceResult

MAX_DESTINATIONS = 25


class RoutingSource(BaseSource):
    def __init__(self):
        super().__init__("routing", rate_limit=2)

    async def get_travel_times(
        self, origin: tuple[float, float], destinations: list[tuple[float, float]]
    ) -> SourceResult[dict[str, Any]]:
        if not destinations:
            return SourceResult(
                source=self.name, ok=True, params={}, data={"durations": [], "source": "none"}
            )

        dests = destinations[:MAX_DESTINATIONS]
        coords = [f"{origin[1]:.6f},{origin[0]:.6f}"] + [
            f"{lon:.6f},{lat:.6f}" for lat, lon in dests
        ]
        url = f"https://router.project-osrm.org/table/v1/driving/{';'.join(coords)}"
        params = {"sources": "0", "annotations": "duration,distance"}
        # The key has to identify the whole destination set: two different
        # shortlists of the same length would otherwise share a cache entry
        # and get each other's travel times.
        dest_key = hashlib.sha1(
            ";".join(f"{la:.5f},{lo:.5f}" for la, lo in dests).encode()
        ).hexdigest()[:16]
        cache_key = f"route:{origin[0]:.4f},{origin[1]:.4f}:{dest_key}"

        async def _fetch(self_src: BaseSource):
            resp = await self_src._request("GET", url, params=params, timeout=20)
            body = resp.json()
            if body.get("code") != "Ok":
                raise RuntimeError(f"OSRM status {body.get('code')}")
            durations = (body.get("durations") or [[]])[0]
            distances = (body.get("distances") or [[]])[0]
            return {
                "source": "osrm",
                # A null means the pair is not reachable by car. The positions
                # have to be kept, otherwise every later venue would be given
                # the travel time of the wrong destination.
                "durations": [None if d is None else float(d) for d in durations[1:]],
                "distances": [None if d is None else float(d) for d in distances[1:]],
            }

        return await self.fetch(cache_key, settings.TTL_ROUTING, _fetch, params)
