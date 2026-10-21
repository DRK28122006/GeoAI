from typing import Any

from app.config import settings
from app.sources.base import BaseSource, SourceResult


class AQSource(BaseSource):
    def __init__(self):
        super().__init__("air-quality")

    async def get_aqi(self, lats: list[float], lons: list[float], hours_ahead: int) -> SourceResult[Any]:
        params = {
            "latitude": ",".join(map(str, lats)),
            "longitude": ",".join(map(str, lons)),
            "hourly": "pm2_5,pm10,nitrogen_dioxide,ozone,us_aqi",
            "timezone": "Asia/Kolkata",
            "forecast_days": (hours_ahead // 24) + 1
        }

        coords_key = ",".join([f"{lat:.3f},{lon:.3f}" for lat, lon in zip(lats, lons)])
        cache_key = f"aq:{coords_key}:{hours_ahead}"

        async def _fetch(self_src: BaseSource):
            # 1. Get forecast from Open-Meteo
            resp = await self_src._request("GET", "https://air-quality-api.open-meteo.com/v1/air-quality", params=params)
            forecast_data = resp.json()

            # 2. Station Bias Correction
            # Note: In a real implementation, we'd iterate over each coord to find nearest station.
            # For now, we compute a global bias if WAQI/OpenAQ keys exist.
            bias_factor = 1.0
            bias_info = {"station": None, "distance": None, "timestamp": None, "limitation": "No station bias applied"}

            if settings.WAQI_TOKEN:
                try:
                    # Use the first coordinate for bias check as a proxy for the city
                    lat, lon = lats[0], lons[0]
                    bias_resp = await self_src._request("GET", "https://api.waqi.info/feed",
                                                       params={"a": settings.WAQI_TOKEN, "lat": lat, "lon": lon})
                    bias_data = bias_resp.json()
                    if bias_data.get("status") == "ok":
                        obs_aqi = bias_data["data"]["aqi"]
                        # We'd ideally compare this to the Open-Meteo forecast for 'now'
                        # Simplified: assume we can extract current forecast value
                        forecast_now = forecast_data.get("hourly", {}).get("us_aqi", [100])[0]
                        if forecast_now:
                            bias_factor = max(0.5, min(2.0, obs_aqi / forecast_now))
                            bias_info = {
                                "station": bias_data["data"].get("station", "Unknown"),
                                "distance": "Nearest",
                                "timestamp": bias_data["data"].get("time", {}).get("s", ""),
                                "limitation": None
                            }
                except Exception as e:
                    bias_info["limitation"] = f"Bias correction failed: {e}"

            return {
                "forecast": forecast_data,
                "bias_factor": bias_factor,
                "bias_info": bias_info
            }

        return await self.fetch(cache_key, settings.TTL_AQ_FORECAST, _fetch, params)
