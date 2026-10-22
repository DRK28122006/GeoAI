"""Weather forecasts from Open-Meteo (free, no key).

Open-Meteo returns a **list** of objects when several coordinates are
requested, in the same order as the input. The previous version indexed the
list once and scored every venue with the origin's forecast; this version
keeps the per-location payloads separate.
"""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.sources.base import BaseSource, SourceResult

HOURLY_VARS = (
    "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation_probability,"
    "precipitation,weather_code,cloud_cover,visibility,wind_speed_10m,wind_direction_10m,"
    "wind_gusts_10m,uv_index,cape,is_day"
)


class WeatherSource(BaseSource):
    def __init__(self):
        super().__init__("open-meteo-weather")

    async def get_forecast(
        self, lats: list[float], lons: list[float], days: int = 3
    ) -> SourceResult[list[dict[str, Any]]]:
        params = {
            "latitude": ",".join(f"{v:.5f}" for v in lats),
            "longitude": ",".join(f"{v:.5f}" for v in lons),
            "hourly": HOURLY_VARS,
            "timezone": settings.TIMEZONE,
            "forecast_days": max(1, min(days, 16)),
            "wind_speed_unit": "kmh",
        }
        coords_key = ",".join(f"{a:.3f},{b:.3f}" for a, b in zip(lats, lons))
        cache_key = f"weather:{coords_key}:{days}"

        async def _fetch(self_src: BaseSource):
            resp = await self_src._request(
                "GET", "https://api.open-meteo.com/v1/forecast", params=params
            )
            data = resp.json()
            if isinstance(data, dict):
                return [data]
            return data

        return await self.fetch(cache_key, settings.TTL_WEATHER_FORECAST, _fetch, params)
