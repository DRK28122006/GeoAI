from typing import Any

from astral import LocationInfo
from astral.sun import sun

from app.config import settings
from app.sources.base import BaseSource, SourceResult


class DaylightSource(BaseSource):
    def __init__(self):
        super().__init__("daylight")

    async def get_daylight(self, lat: float, lon: float) -> SourceResult[Any]:
        # Try Open-Meteo daily first
        params = {
            "latitude": str(lat),
            "longitude": str(lon),
            "daily": "sunrise,sunset",
            "timezone": "Asia/Kolkata"
        }
        cache_key = f"daylight:{lat:.3f}:{lon:.3f}"

        async def _fetch(self_src: BaseSource):
            try:
                resp = await self_src._request("GET", "https://api.open-meteo.com/v1/forecast", params=params)
                data = resp.json()
                return {
                    "sunrise": data["daily"]["sunrise"][0],
                    "sunset": data["daily"]["sunset"][0],
                    "source": "open-meteo"
                }
            except Exception:
                # Fallback to astral
                city = LocationInfo(lat, lon, "Local", "UTC", 5.5)
                s = sun(city)
                return {
                    "sunrise": s["sunrise"].isoformat(),
                    "sunset": s["sunset"].isoformat(),
                    "source": "astral"
                }

        return await self.fetch(cache_key, settings.TTL_OVERPASS, _fetch, params)
