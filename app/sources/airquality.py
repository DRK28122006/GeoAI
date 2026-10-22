"""Air quality.

Two sources, both free and open:

* **Open-Meteo air quality** (CAMS ENSEMBLE global grid) - the forecast.
  Outside Europe the grid is roughly 25-40 km wide, so every venue in a city
  usually shares one value. Venue-to-venue differences therefore come from
  the exposure engine, not from this API. We say so in the response.
* **WAQI** (free token) - a real monitoring station, used to compute a bias
  factor between the coarse grid and what a station actually reports.

Credit: Open-Meteo, CAMS ENSEMBLE data (Copernicus/ECMWF).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings
from app.sources.base import BaseSource, SourceResult, redact

logger = logging.getLogger("geoai.airquality")

HOURLY_VARS = "pm2_5,pm10,nitrogen_dioxide,ozone,us_aqi,european_aqi"


class AQSource(BaseSource):
    def __init__(self):
        super().__init__("open-meteo-air-quality")

    async def get_aqi(
        self, lats: list[float], lons: list[float], days: int = 3
    ) -> SourceResult[dict[str, Any]]:
        params = {
            "latitude": ",".join(f"{v:.5f}" for v in lats),
            "longitude": ",".join(f"{v:.5f}" for v in lons),
            "hourly": HOURLY_VARS,
            "timezone": settings.TIMEZONE,
            "forecast_days": max(1, min(days, 7)),
        }
        coords_key = ",".join(f"{a:.3f},{b:.3f}" for a, b in zip(lats, lons))
        cache_key = f"aq:{coords_key}:{days}"

        async def _fetch(self_src: BaseSource):
            resp = await self_src._request(
                "GET",
                "https://air-quality-api.open-meteo.com/v1/air-quality",
                params=params,
            )
            data = resp.json()
            forecasts = data if isinstance(data, list) else [data]

            payload: dict[str, Any] = {
                "forecasts": forecasts,
                "bias_factor": 1.0,
                "station": None,
                "station_limitations": [],
            }

            # --- WAQI bias correction against the nearest station ----------
            if settings.WAQI_TOKEN:
                try:
                    bias = await self_src._waqi_bias(lats[0], lons[0], forecasts[0])
                    payload.update(bias)
                except Exception as exc:  # noqa: BLE001 - degrade, never crash
                    safe = redact(exc)
                    logger.warning("WAQI bias correction failed: %s", safe)
                    payload["station_limitations"].append(
                        "The nearby air quality station could not be reached, so the "
                        "forecast grid is used as it is."
                    )
            else:
                payload["station_limitations"].append(
                    "No air-quality station token configured; forecast grid used as-is."
                )

            return payload

        return await self.fetch(cache_key, settings.TTL_AQ_FORECAST, _fetch, params)

    async def _waqi_bias(
        self, lat: float, lon: float, forecast: dict[str, Any]
    ) -> dict[str, Any]:
        """Compare the nearest station's PM2.5 with the grid value right now."""
        resp = await self._request(
            "GET",
            f"https://api.waqi.info/feed/geo:{lat:.4f};{lon:.4f}/",
            params={"token": settings.WAQI_TOKEN},
        )
        body = resp.json()
        if body.get("status") != "ok":
            raise RuntimeError(body.get("data", "WAQI error"))

        data = body.get("data") or {}
        station = data.get("station") or {}
        obs_pm25 = (data.get("iaqi") or {}).get("pm25")
        if obs_pm25 is None:
            raise RuntimeError("station has no PM2.5 reading")

        hourly = forecast.get("hourly", {}) if isinstance(forecast, dict) else {}
        # The station reads the air right now, so the grid value has to be the
        # one for the current hour and not the first hour of the day.
        times = hourly.get("time") or []
        now_hour = datetime.now(ZoneInfo(settings.TIMEZONE)).strftime("%Y-%m-%dT%H")
        index = next(
            (i for i, t in enumerate(times) if str(t)[:13] == now_hour),
            0,
        )
        grid_pm25 = (hourly.get("pm2_5") or [None])[index]
        if not grid_pm25:
            raise RuntimeError("forecast has no PM2.5 value")

        # the bias factor is clamped to 0.5 - 2.0 so a single odd station
        # reading cannot distort the whole grid
        factor = max(0.5, min(2.0, float(obs_pm25) / float(grid_pm25)))
        return {
            "bias_factor": round(factor, 3),
            "station": {
                "name": station.get("name"),
                "uid": station.get("uid"),
                "lat": station.get("geo", [None, None])[0],
                "lon": station.get("geo", [None, None])[1],
                "observed_pm25": obs_pm25,
                "observed_aqi": data.get("aqi"),
                "time": (data.get("time") or {}).get("s"),
            },
            "station_limitations": [],
        }
