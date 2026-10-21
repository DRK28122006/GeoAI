import yaml
import logging
from typing import Any, Optional
from pydantic import BaseModel
from app.config import settings
from app.context import Intent
from app.sources.overpass import Venue

logger = logging.getLogger("geoai.exposure")

class ExposureScore(BaseModel):
    venue_id: str
    hour: int
    total_score: float
    breakdown: dict[str, float]
    evidence_ids: list[str]

class ExposureEngine:
    def __init__(self):
        with open("configs/exposure_weights.yaml", "r") as f:
            self.weights = yaml.safe_load(f)["weights"]

    def _get_aq_category(self, aqi: float) -> str:
        if aqi <= 50: return "good"
        if aqi <= 100: return "fair"
        if aqi <= 150: return "poor"
        return "very_poor"

    def _get_temp_category(self, temp: float) -> str:
        if temp < 0: return "extreme_cold"
        if temp > 35: return "extreme_heat"
        return "moderate"

    def _get_weather_category(self, code: int) -> str:
        if code == 0: return "clear"
        if code >= 51 and code <= 67: return "rain"
        if code >= 71 and code <= 99: return "storm"
        return "moderate"

    def score(self, bundle: dict[str, Any]) -> list[ExposureScore]:
        intent = bundle["intent"]
        venues = bundle["venues"]
        weather_raw = bundle["weather"]
        aq_raw = bundle["air_quality"]
        hazards = bundle["hazards"]

        if not venues or not weather_raw or not aq_raw:
            return []

        # Normalize weather and AQ data to dictionaries
        weather = weather_raw[0] if isinstance(weather_raw, list) else weather_raw
        aq = aq_raw[0] if isinstance(aq_raw, list) else aq_raw

        scores = []
        aq_mult = 2.0 if "asthma" in intent.profile else 1.0

        for venue in venues:
            venue_base_score = 0.0
            if "leisure" in venue.tags and venue.tags["leisure"] in ["park", "nature_reserve"]:
                venue_base_score += self.weights["mitigators"].get("park", 0)

            for h in range(24):
                hourly_breakdown = {}

                try:
                    # Handle both list and dict formats for AQI
                    aqi_data = aq.get("hourly", {}).get("us_aqi", [])
                    aqi_val = aqi_data[h] if isinstance(aqi_data, list) else aqi_data.get(h, 0)
                    cat = self._get_aq_category(aqi_val)
                    score = self.weights["air_quality"].get(cat, 0) * aq_mult
                    hourly_breakdown["air_quality"] = score
                except (IndexError, KeyError, TypeError):
                    hourly_breakdown["air_quality"] = 0.0

                try:
                    # Handle both list and dict formats for Weather
                    w_data = weather.get("hourly", {})
                    temp_list = w_data.get("temperature_2m", [])
                    temp_val = temp_list[h] if isinstance(temp_list, list) else temp_list.get(h, 0)

                    code_list = w_data.get("weather_code", [])
                    w_code = code_list[h] if isinstance(code_list, list) else code_list.get(h, 0)

                    t_cat = self._get_temp_category(temp_val)
                    w_cat = self._get_weather_category(w_code)

                    t_score = self.weights["temperature"].get(t_cat, 0)
                    w_score = self.weights["weather"].get(w_cat, 0)

                    hourly_breakdown["temperature"] = t_score
                    hourly_breakdown["weather"] = w_score
                except (IndexError, KeyError, TypeError):
                    hourly_breakdown["temperature"] = 0.0
                    hourly_breakdown["weather"] = 0.0

                total = venue_base_score + sum(hourly_breakdown.values())

                scores.append(ExposureScore(
                    venue_id=venue.id,
                    hour=h,
                    total_score=total,
                    breakdown=hourly_breakdown,
                    evidence_ids=["E_WEATHER", "E_AQ"]
                ))

        return sorted(scores, key=lambda x: x.total_score, reverse=True)
