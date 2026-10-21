"""Sunrise / sunset, used for daylight scoring and golden hour.

Open-Meteo's daily endpoint is preferred; `astral` is the offline fallback.
The previous version only fetched day 0, which broke any question about
"tomorrow" or "this weekend".
"""

from __future__ import annotations

import logging
from typing import Any

from astral import LocationInfo
from astral.sun import sun

from app.config import settings
from app.sources.base import BaseSource, SourceResult

logger = logging.getLogger("geoai.daylight")


class DaylightSource(BaseSource):
    def __init__(self):
        super().__init__("daylight")

    async def get_daylight(
        self, lat: float, lon: float, days: int = 7
    ) -> SourceResult[dict[str, Any]]:
        params = {
            "latitude": f"{lat:.5f}",
            "longitude": f"{lon:.5f}",
            "daily": "sunrise,sunset",
            "timezone": settings.TIMEZONE,
            "forecast_days": max(1, min(days, 16)),
        }
        cache_key = f"daylight:{lat:.3f}:{lon:.3f}:{days}"

        async def _fetch(self_src: BaseSource):
            try:
                resp = await self_src._request(
                    "GET", "https://api.open-meteo.com/v1/forecast", params=params
                )
                data = resp.json()
                daily = data.get("daily", {})
                return {
                    "source": "open-meteo",
                    "dates": daily.get("time", []),
                    "sunrise": daily.get("sunrise", []),
                    "sunset": daily.get("sunset", []),
                }
            except Exception as exc:  # noqa: BLE001 - offline fallback
                logger.warning("Open-Meteo daylight failed, using astral: %s", exc)
                city = LocationInfo(lat, lon, "Local", "UTC", 5.5)
                s = sun(city)
                return {
                    "source": "astral",
                    "dates": [s["sunrise"].date().isoformat()],
                    "sunrise": [s["sunrise"].isoformat()],
                    "sunset": [s["sunset"].isoformat()],
                }

        return await self.fetch(cache_key, settings.TTL_DAYLIGHT, _fetch, params)
