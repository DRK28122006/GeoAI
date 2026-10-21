from typing import Any

from app.config import settings
from app.sources.base import BaseSource, SourceResult


class WeatherSource(BaseSource):
    def __init__(self):
        super().__init__("open-meteo-weather")

    async def get_forecast(self, lats: list[float], lons: list[float], hours_ahead: int) -> SourceResult[Any]:
        params = {
            "latitude": ",".join(map(str, lats)),
            "longitude": ",".join(map(str, lons)),
            "hourly": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation_probability,precipitation,weather_code,cloud_cover,visibility,wind_speed_10m,wind_direction_10m,wind_gusts_10m,uv_index,cape",
            "timezone": "Asia/Kolkata",
            "forecast_days": (hours_ahead // 24) + 1
        }

        # Generate a stable cache key based on sorted coords and hours
        coords_key = ",".join([f"{lat:.3f},{lon:.3f}" for lat, lon in zip(lats, lons)])
        cache_key = f"weather:{coords_key}:{hours_ahead}"

        async def _fetch(self_src: BaseSource):
            resp = await self_src._request("GET", "https://api.open-meteo.com/v1/forecast", params=params)
            return resp.json()

        return await self.fetch(cache_key, settings.TTL_WEATHER_FORECAST, _fetch, params)
