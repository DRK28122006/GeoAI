"""The geo-exposure engine.

Fully deterministic, no SLM involved. For every (venue, hour) pair it:

1. reads the live weather and air quality for that venue and hour,
2. computes a **downwind exposure** term from the map features around the
   venue (roads, industry, power plants, landfills) using the wind direction
   and speed of that hour,
3. estimates a local PM2.5 value by correcting the coarse forecast grid with
   the local exposure term and the nearest station's bias,
4. applies hard constraints (thunderstorm, rain, heat, air quality, darkness,
   travel time) which disqualify a slot outright,
5. scores what is left on a 0-100 scale with a transparent breakdown.

The interesting property: the same venue scores differently at different
hours, because the wind and the traffic both change.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any

import yaml
from pydantic import BaseModel, Field

from app.config import CONFIG_DIR
from app.planner import TZ

logger = logging.getLogger("geoai.exposure")

WIND_DIRS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]

THUNDERSTORM_CODES = {95, 96, 99}
HEAVY_RAIN_CODES = {65, 67, 82, 86, 95, 96, 99}


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Bearing from a to b, degrees clockwise from north."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dl = lon2 - lon1
    x = math.sin(dl) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def wind_dir_label(deg: float | None) -> str:
    if deg is None:
        return "unknown"
    return WIND_DIRS[int((deg % 360) / 22.5 + 0.5) % 16]


class Slot(BaseModel):
    slot_id: str
    venue_id: str
    place: str
    lat: float
    lon: float
    when: datetime
    time_label: str
    score: float
    breakdown: dict[str, float] = Field(default_factory=dict)
    weather: dict[str, Any] = Field(default_factory=dict)
    air: dict[str, Any] = Field(default_factory=dict)
    travel_min: float | None = None
    travel_min_at_time: float | None = None
    distance_km: float | None = None
    daylight: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    disqualified: str | None = None


class EngineResult(BaseModel):
    slots: list[Slot] = Field(default_factory=list)
    all_slots: list[Slot] = Field(default_factory=list)
    avoid: list[dict[str, Any]] = Field(default_factory=list)
    alternatives: list[Slot] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ExposureEngine:
    def __init__(self) -> None:
        with open(CONFIG_DIR / "activities.yaml", "r", encoding="utf-8") as f:
            self.activities: dict[str, Any] = yaml.safe_load(f)
        with open(CONFIG_DIR / "hazards.yaml", "r", encoding="utf-8") as f:
            self.hazard_cfg: dict[str, Any] = yaml.safe_load(f)
        with open(CONFIG_DIR / "exposure_weights.yaml", "r", encoding="utf-8") as f:
            self.weights: dict[str, Any] = yaml.safe_load(f)
        with open(CONFIG_DIR / "profiles.yaml", "r", encoding="utf-8") as f:
            self.profile_cfg: dict[str, Any] = yaml.safe_load(f)

    # ------------------------------------------------------------- utilities
    def _activity(self, activity: str) -> dict[str, Any]:
        return self.activities.get(activity) or self.activities["running"]

    def _profile_limits(self, profiles: list[str]) -> dict[str, Any]:
        limits: dict[str, Any] = {}
        for p in profiles:
            cfg = self.profile_cfg.get("profiles", {}).get(p)
            if not cfg:
                continue
            limits[p] = cfg
        return limits

    def _traffic_multiplier(self, cls: str, hour: int) -> float:
        classes = self.hazard_cfg["classes"]
        cfg = classes.get(cls) or classes.get("primary")
        profile = self.hazard_cfg["traffic_profile"]
        for start, end in profile.get("rush_hours", []):
            if start <= hour < end:
                return float(cfg.get("traffic_rush", 1.0))
        for start, end in profile.get("night_hours", []):
            if (start <= hour < 24) or (hour < end):
                return float(cfg.get("traffic_night", 0.3))
        return float(cfg.get("traffic_normal", 0.6))

    @staticmethod
    def _hourly_index(hourly: dict[str, Any], when: datetime) -> int | None:
        times = hourly.get("time") or []
        target = when.strftime("%Y-%m-%dT%H:%M")
        for i, t in enumerate(times):
            if str(t)[:16] == target:
                return i
        return None

    @staticmethod
    def _value(hourly: dict[str, Any], key: str, idx: int | None) -> Any:
        if idx is None:
            return None
        arr = hourly.get(key) or []
        if idx >= len(arr):
            return None
        return arr[idx]

    # -------------------------------------------------------------- exposure
    def _venue_hazards(
        self, venue_lat: float, venue_lon: float, hazards: list[Any]
    ) -> list[dict[str, Any]]:
        """Distance + bearing from each hazard to the venue (geometry aware)."""
        out = []
        venue_pt = (venue_lat, venue_lon)
        classes = self.hazard_cfg["classes"]
        for h in hazards:
            cls_key = h.road_class if h.kind == "road" else h.kind
            cls = classes.get(cls_key or "")
            if not cls:
                continue
            best_d = None
            best_pt = None
            for pt in h.points:
                d = haversine_km(venue_pt, pt)
                if best_d is None or d < best_d:
                    best_d, best_pt = d, pt
            if best_d is None:
                continue
            out.append(
                {
                    "hazard": h,
                    "class": cls_key,
                    "weight": float(cls.get("weight", 0.0)),
                    "length_scale_m": float(cls.get("length_scale_m", 500)),
                    "distance_m": best_d * 1000.0,
                    "bearing": bearing_deg(best_pt, venue_pt),
                }
            )
        return out

    def _mitigation(self, venue_lat: float, venue_lon: float, hazards: list[Any]) -> float:
        venue_pt = (venue_lat, venue_lon)
        reduction = 0.0
        for m in self.hazard_cfg.get("mitigators", {}).values():
            radius_m = float(m["radius_m"])
            for h in hazards:
                if h.kind != m["kind"]:
                    continue
                d = min(haversine_km(venue_pt, pt) * 1000.0 for pt in h.points)
                if d <= radius_m:
                    reduction = max(reduction, float(m["max_reduction"]))
                    break
        return reduction

    def _exposure(
        self,
        venue_hazards: list[dict[str, Any]],
        wind_from: float | None,
        wind_kmh: float | None,
        hour: int,
    ) -> tuple[float, list[str]]:
        if not venue_hazards:
            return 0.0, []
        calm_cfg = self.hazard_cfg["calm"]
        wind_mps = (wind_kmh or 0.0) / 3.6
        calm = 1.0 + float(calm_cfg["k"]) * (
            1.0 - min(wind_mps / float(calm_cfg["reference_wind_mps"]), 1.0)
        )
        wind_toward = None if wind_from is None else (wind_from + 180.0) % 360.0

        exposure = 0.0
        contributors = []
        for vh in venue_hazards:
            if vh["weight"] <= 0 or wind_toward is None:
                continue
            theta = math.radians(vh["bearing"])
            phi = math.radians(wind_toward)
            alignment = max(0.0, math.cos(theta - phi))
            if alignment <= 0:
                continue
            decay = math.exp(-vh["distance_m"] / vh["length_scale_m"])
            traffic = self._traffic_multiplier(vh["class"], hour)
            contribution = vh["weight"] * decay * alignment * calm * traffic
            if contribution > 0.001:
                exposure += contribution
                contributors.append(
                    f"{vh['class']} {vh['distance_m'] / 1000.0:.1f}km away, "
                    f"downwind (align {alignment:.2f}, traffic x{traffic:.2f})"
                )

        cap = float(self.weights["exposure"]["max_exposure"])
        return min(exposure, cap), contributors

    # ---------------------------------------------------------------- scoring
    def _comfort_score(self, temp_c: float | None, humidity: int | None, act: dict) -> float:
        band = act.get("comfort_band_c", [14, 28])
        lo, hi = float(band[0]), float(band[1])
        if temp_c is None:
            return 50.0
        if lo <= temp_c <= hi:
            base = 100.0
        else:
            dist = lo - temp_c if temp_c < lo else temp_c - hi
            base = max(0.0, 100.0 - dist * float(self.weights["comfort"]["penalty_per_degree"]) * 100)
        if humidity and humidity > int(self.weights["comfort"]["humidity_penalty_above"]):
            base -= (
                (humidity - int(self.weights["comfort"]["humidity_penalty_above"]))
                * float(self.weights["comfort"]["humidity_penalty_per_pct"])
                * 100
            )
        return max(0.0, min(100.0, base))

    def _air_score(self, local_pm25: float | None, us_aqi: float | None) -> float:
        bands = self.weights["aqi_bands"]
        aqi = us_aqi
        if aqi is None and local_pm25 is not None:
            # rough US AQI approximation from PM2.5 (CPCB-aligned breakpoints)
            aqi = min(500.0, (local_pm25 / 12.0) * 50.0)
        if aqi is None:
            return 50.0
        for band in bands.values():
            if aqi <= float(band["max"]):
                return float(band["score"])
        return 0.0

    def _wind_rain_score(
        self, wind_kmh: float | None, gusts: float | None, precip_prob: int | None, act: dict
    ) -> float:
        comfort = float(
            self.weights["wind"]["comfortable_kmh"].get(act.get("_key", "running"), 22)
        )
        score = 100.0
        if wind_kmh is not None and wind_kmh > comfort:
            score -= min(60.0, (wind_kmh - comfort) * 2.2)
        if gusts is not None and wind_kmh is not None and gusts > wind_kmh * 1.3:
            score -= min(20.0, (gusts - wind_kmh) * 0.5)
        if precip_prob:
            score -= min(60.0, (precip_prob / 10.0) * float(self.weights["rain"]["precip_prob_penalty"]) * 10)
        return max(0.0, min(100.0, score))

    def _daylight_score(self, when: datetime, sunrise: str | None, sunset: str | None, act: dict) -> float:
        if not sunset:
            return 70.0
        sunset_dt = self._parse_local(sunset)
        sunrise_dt = self._parse_local(sunrise) if sunrise else None
        if sunset_dt is None:
            return 70.0
        if sunrise_dt is not None and sunrise_dt <= when <= sunset_dt:
            minutes_to_sunset = (sunset_dt - when).total_seconds() / 60
            golden = float(self.weights["daylight"]["golden_hour_before_sunset_min"])
            if minutes_to_sunset <= golden:
                return 100.0
            return 92.0
        blue = int(self.weights["daylight"]["blue_hour_after_sunset_min"])
        if sunset_dt < when <= sunset_dt + timedelta_minutes(blue):
            return 70.0
        return 25.0

    @staticmethod
    def _parse_local(value: str | None) -> datetime | None:
        """Open-Meteo returns naive local ISO strings; make them timezone aware."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt

    def _travel_factor(self, hour: int) -> float:
        """Rush hour makes the trip longer. A documented proxy for traffic."""
        profile = self.hazard_cfg["traffic_profile"]
        t = self.weights["travel"]
        for start, end in profile.get("rush_hours", []):
            if start <= hour < end:
                return float(t.get("rush_factor", 1.3))
        for start, end in profile.get("night_hours", []):
            if (start <= hour < 24) or (hour < end):
                return float(t.get("night_factor", 0.85))
        return float(t.get("normal_factor", 1.0))

    def _travel_score(self, travel_min: float | None, act: dict) -> float:
        if travel_min is None:
            return 60.0
        duration = float(act.get("duration_min", 60))
        penalty = (
            travel_min
            * float(self.weights["travel"]["penalty_per_min"])
            * (60.0 / max(30.0, duration))
        )
        return max(0.0, min(100.0, 100.0 - penalty))

    # ------------------------------------------------------------------ main
    def score(self, bundle: dict[str, Any]) -> EngineResult:
        intent = bundle["intent"]
        venues = bundle.get("venues") or []
        weather_list = bundle.get("weather") or []
        aq = bundle.get("air_quality") or {}
        aq_list = aq.get("forecasts") or []
        hazards = bundle.get("hazards") or []
        routes = bundle.get("routes") or {}
        daylight = bundle.get("daylight") or {}
        window = bundle["window"]

        act = dict(self._activity(intent.activity))
        act["_key"] = intent.activity
        profiles = self._profile_limits(intent.profile)

        pm25_limit = 90.0
        temp_limit = float(act.get("temp_limit_c", 37))
        exposure_weight = 1.0
        air_weight = 1.0
        for cfg in profiles.values():
            pm25_limit = min(pm25_limit, float(cfg.get("pm25_limit", 90)))
            temp_limit = min(temp_limit, float(cfg.get("temp_limit_c", 34)))
            exposure_weight *= float(cfg.get("exposure_weight", 1.0))
            air_weight *= float(cfg.get("air_weight", 1.0))

        bias = float(aq.get("bias_factor", 1.0) or 1.0)
        beta = float(self.weights["exposure"]["beta"])
        w = self.weights["score_weights"]

        slots: list[Slot] = []
        avoid: list[dict[str, Any]] = []

        # per-venue hazard geometry (time independent)
        venue_hazards = [self._venue_hazards(v.lat, v.lon, hazards) for v in venues]
        mitigations = [self._mitigation(v.lat, v.lon, hazards) for v in venues]

        durations = routes.get("durations") or []
        distances = routes.get("distances") or []

        for vi, venue in enumerate(venues):
            w_hourly = (weather_list[vi + 1] if vi + 1 < len(weather_list) else {}) or {}
            a_hourly = (
                aq_list[vi + 1] if vi + 1 < len(aq_list) else (aq_list[0] if aq_list else {})
            ) or {}
            travel_min = durations[vi] / 60.0 if vi < len(durations) else None
            distance_km = distances[vi] / 1000.0 if vi < len(distances) else None

            for when in window.hours:
                idx = self._hourly_index(w_hourly.get("hourly", {}), when)
                if idx is None:
                    idx = self._hourly_index((weather_list[0] if weather_list else {}).get("hourly", {}), when)
                a_idx = self._hourly_index(a_hourly.get("hourly", {}), when)
                if a_idx is None:
                    a_idx = self._hourly_index((aq_list[0] if aq_list else {}).get("hourly", {}), when)

                temp = self._value(w_hourly.get("hourly", {}), "apparent_temperature", idx)
                if temp is None:
                    temp = self._value(w_hourly.get("hourly", {}), "temperature_2m", idx)
                humidity = self._value(w_hourly.get("hourly", {}), "relative_humidity_2m", idx)
                precip_prob = self._value(w_hourly.get("hourly", {}), "precipitation_probability", idx)
                precip = self._value(w_hourly.get("hourly", {}), "precipitation", idx)
                code = self._value(w_hourly.get("hourly", {}), "weather_code", idx)
                cloud = self._value(w_hourly.get("hourly", {}), "cloud_cover", idx)
                visibility = self._value(w_hourly.get("hourly", {}), "visibility", idx)
                wind_kmh = self._value(w_hourly.get("hourly", {}), "wind_speed_10m", idx)
                gusts = self._value(w_hourly.get("hourly", {}), "wind_gusts_10m", idx)
                wind_from = self._value(w_hourly.get("hourly", {}), "wind_direction_10m", idx)
                uv = self._value(w_hourly.get("hourly", {}), "uv_index", idx)
                is_day = self._value(w_hourly.get("hourly", {}), "is_day", idx)
                cape = self._value(w_hourly.get("hourly", {}), "cape", idx)

                pm25 = self._value(a_hourly.get("hourly", {}), "pm2_5", a_idx)
                pm10 = self._value(a_hourly.get("hourly", {}), "pm10", a_idx)
                o3 = self._value(a_hourly.get("hourly", {}), "ozone", a_idx)
                no2 = self._value(a_hourly.get("hourly", {}), "nitrogen_dioxide", a_idx)
                us_aqi = self._value(a_hourly.get("hourly", {}), "us_aqi", a_idx)

                exposure, contributors = self._exposure(
                    venue_hazards[vi], wind_from, wind_kmh, when.hour
                )
                exposure *= (1.0 - mitigations[vi]) * exposure_weight
                local_pm25 = None
                if pm25 is not None:
                    local_pm25 = float(pm25) * bias * (1.0 + beta * exposure)

                sunrise, sunset = self._sunset_for(daylight, when.date().isoformat())
                day_score = self._daylight_score(when, sunrise, sunset, act)

                # ---------------------------------------------------- scoring
                comfort = self._comfort_score(temp, humidity, act)
                air = self._air_score(local_pm25, us_aqi) * air_weight
                air = max(0.0, min(100.0, air))
                wind_rain = self._wind_rain_score(wind_kmh, gusts, precip_prob, act)
                travel_at_time = (
                    None if travel_min is None else travel_min * self._travel_factor(when.hour)
                )
                travel = self._travel_score(travel_at_time, act)

                breakdown = {
                    "comfort": round(comfort, 1),
                    "air": round(air, 1),
                    "wind_rain": round(wind_rain, 1),
                    "daylight": round(day_score, 1),
                    "travel": round(travel, 1),
                }
                total = (
                    comfort * w["comfort"]
                    + air * w["air"]
                    + wind_rain * w["wind_rain"]
                    + day_score * w["daylight"]
                    + travel * w["travel"]
                ) / (w["comfort"] + w["air"] + w["wind_rain"] + w["daylight"] + w["travel"])
                total -= min(25.0, exposure * 10.0 * w["hazard"] / 20.0)

                # ------------------------------------------------ constraints
                reasons = []
                if idx is None:
                    reasons.append("no forecast is available for this hour")
                if code in THUNDERSTORM_CODES or (cape or 0) > 1500:
                    reasons.append("thunderstorm risk")
                if precip_prob is not None and precip_prob > float(act.get("precip_prob_limit", 60)):
                    reasons.append(f"{precip_prob}% chance of rain")
                if code in HEAVY_RAIN_CODES and precip_prob is None:
                    reasons.append("heavy rain in the forecast")
                if temp is not None and float(temp) > temp_limit:
                    reasons.append(f"apparent temperature {temp:.0f}C is above the {temp_limit:.0f}C limit")
                if local_pm25 is not None and local_pm25 > pm25_limit:
                    reasons.append(f"local PM2.5 {local_pm25:.0f} ug/m3 is above the {pm25_limit:.0f} limit")
                if act.get("daylight_required") and not is_day and not venue.lit:
                    reasons.append("it is dark and this venue is not lit")
                if (
                    travel_at_time is not None
                    and travel_at_time > float(self.weights["travel"]["max_travel_min"])
                ):
                    reasons.append(
                        f"travel time {travel_at_time:.0f} min at that hour is too long"
                    )

                slot = Slot(
                    slot_id=f"S{vi + 1}-{when.strftime('%m%d%H')}",
                    venue_id=venue.id,
                    place=venue.name,
                    lat=venue.lat,
                    lon=venue.lon,
                    when=when,
                    time_label=when.strftime("%a %d %b, %H:%M"),
                    score=round(max(0.0, min(100.0, total)), 1),
                    breakdown=breakdown,
                    weather={
                        "apparent_temp_c": temp,
                        "humidity": humidity,
                        "precip_prob": precip_prob,
                        "precip_mm": precip,
                        "weather_code": code,
                        "cloud_cover": cloud,
                        "visibility_m": visibility,
                        "wind_kmh": wind_kmh,
                        "wind_gusts_kmh": gusts,
                        "wind_from_deg": wind_from,
                        "wind_from": wind_dir_label(wind_from),
                        "uv_index": uv,
                        "cape": cape,
                        "is_day": is_day,
                    },
                    air={
                        "pm2_5_forecast": pm25,
                        "local_pm2_5": None if local_pm25 is None else round(local_pm25, 1),
                        "pm10": pm10,
                        "o3": o3,
                        "no2": no2,
                        "us_aqi": us_aqi,
                        "exposure": round(exposure, 3),
                        "exposure_sources": contributors[:3],
                        "bias_factor": bias,
                    },
                    travel_min=None if travel_min is None else round(travel_min, 1),
                    travel_min_at_time=(
                        None if travel_at_time is None else round(travel_at_time, 1)
                    ),
                    distance_km=None if distance_km is None else round(distance_km, 2),
                    daylight={"sunrise": sunrise, "sunset": sunset},
                    evidence_ids=["E_VENUES", "E_WEATHER", "E_AQ", "E_HAZARDS"],
                    disqualified="; ".join(reasons) if reasons else None,
                )
                slots.append(slot)

                if reasons:
                    avoid.append(
                        {
                            "window": slot.time_label,
                            "venue": venue.name,
                            "reason": "; ".join(reasons),
                            "evidence": ["E_WEATHER", "E_AQ"],
                        }
                    )

        slots.sort(key=lambda s: s.score, reverse=True)
        good = [s for s in slots if not s.disqualified]
        result = EngineResult(
            slots=good, all_slots=slots, avoid=avoid[:40]
        )
        result.notes.append(
            f"Scored {len(slots)} venue-hour combinations; {len(good)} passed the hard constraints."
        )
        return result

    @staticmethod
    def _sunset_for(daylight: dict[str, Any], date_iso: str) -> tuple[str | None, str | None]:
        dates = daylight.get("dates") or []
        sunrises = daylight.get("sunrise") or []
        sunsets = daylight.get("sunset") or []
        for i, d in enumerate(dates):
            if str(d)[:10] == date_iso and i < len(sunsets):
                return (sunrises[i] if i < len(sunrises) else None), sunsets[i]
        if sunsets:
            return (sunrises[0] if sunrises else None), sunsets[0]
        return None, None


def timedelta_minutes(minutes: int):
    from datetime import timedelta

    return timedelta(minutes=minutes)
