import asyncio
import time
from typing import Any

from pydantic import BaseModel

from app.sources.airquality import AQSource
from app.sources.daylight import DaylightSource
from app.sources.elevation import ElevationSource
from app.sources.geocode import GeocodeSource
from app.sources.overpass import OverpassSource
from app.sources.routing import RoutingSource
from app.sources.weather import WeatherSource


class Intent(BaseModel):
    activity: str
    location_text: str
    lat: float | None = None
    lon: float | None = None
    radius_km: float = 5.0
    start: str | None = None
    end: str | None = None
    profile: list[str] = []

async def gather_context(intent: Intent) -> dict[str, Any]:
    # Initialize sources
    geo_src = GeocodeSource()
    weather_src = WeatherSource()
    aq_src = AQSource()
    overpass_src = OverpassSource()
    route_src = RoutingSource()
    day_src = DaylightSource()
    elev_src = ElevationSource()

    evidence = {}
    source_status = {}
    limitations = []

    # 1. Geocode
    geo_res = await geo_src.geocode(intent.location_text)
    source_status["geocode"] = geo_res.ok
    if not geo_res.ok:
        return {"error": f"Geocoding failed: {geo_res.error}", "source_status": source_status}

    # Use best candidate
    origin = geo_res.data[0]
    intent.lat, intent.lon = origin.lat, origin.lon
    evidence["E1"] = {"source": "geocode", "value": origin.display_name, "fetched_at": geo_res.fetched_at}

    # 2. Venues
    venue_res = await overpass_src.get_venues(intent.activity, origin.lat, origin.lon)
    source_status["overpass_venues"] = venue_res.ok
    if not venue_res.ok:
        return {"error": f"Venue search failed: {venue_res.error}", "source_status": source_status}

    venues = venue_res.data
    evidence["E2"] = {"source": "overpass", "value": f"{len(venues)} venues found", "fetched_at": venue_res.fetched_at}

    # 3. Parallel Data Gathering
    v_lats = [v.lat for v in venues]
    v_lons = [v.lon for v in venues]

    # Define parallel tasks
    tasks = {
        "weather": weather_src.get_forecast([origin.lat] + v_lats, [origin.lon] + v_lons, 48),
        "aq": aq_src.get_aqi([origin.lat] + v_lats, [origin.lon] + v_lons, 48),
        "hazards": overpass_src.get_hazards(venues),
        "routes": route_src.get_travel_times((origin.lat, origin.lon), [(v.lat, v.lon) for v in venues]),
        "daylight": day_src.get_daylight(origin.lat, origin.lon),
        "elevation": elev_src.get_elevation(v_lats, v_lons) if intent.activity == "hiking" else asyncio.sleep(0, result=None)
    }

    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    res_map = dict(zip(tasks.keys(), results))

    # Process Results
    for key, res in res_map.items():
        if isinstance(res, Exception) or (hasattr(res, 'ok') and not res.ok):
            source_status[key] = False
            limitations.append(f"Source {key} failed")
        else:
            source_status[key] = True
            # Add actual data values to evidence for SLM reasoning
            ev_id = f"E{len(evidence)+1}"
            val = "fetched"

            if key == "weather" and res and res.data:
                # Summarize forecast for evidence
                data = res.data[0] if isinstance(res.data, list) else res.data
                w_data = data.get("hourly", {})
                t_list = w_data.get("temperature_2m", [])
                temp = t_list[0] if isinstance(t_list, list) and t_list else (t_list.get(0) if isinstance(t_list, dict) else "unknown")

                c_list = w_data.get("weather_code", [])
                code = c_list[0] if isinstance(c_list, list) and c_list else (c_list.get(0) if isinstance(c_list, dict) else "unknown")
                val = f"Current temp: {temp}C, Weather code: {code}"
            elif key == "aq" and res and res.data:
                # Handle list of results for multiple coordinates
                data = res.data[0] if isinstance(res.data, list) else res.data
                forecast = data.get("forecast", data) if isinstance(data, dict) else data
                final_data = forecast[0] if isinstance(forecast, list) else forecast
                aq_data = final_data.get("hourly", {}).get("us_aqi", [])
                aqi = aq_data[0] if isinstance(aq_data, list) and aq_data else (aq_data.get(0) if isinstance(aq_data, dict) else "unknown")
                val = f"Current US-AQI: {aqi}"
            elif key == "hazards" and res and res.data:
                val = f"Hazards found: {len(res.data.get('elements', []))}"
            elif key == "daylight" and res and res.data:
                val = f"Sunrise: {res.data.get('sunrise')}, Sunset: {res.data.get('sunset')}"
            elif key == "elevation" and res and res.data:
                val = f"Avg elevation: {sum(res.data)/len(res.data) if res.data else 'unknown'}m"
            elif key == "routes" and res and res.data:
                val = "Travel times calculated for venues"

            evidence[ev_id] = {"source": key, "fetched_at": getattr(res, 'fetched_at', time.time()), "value": val}


    return {
        "intent": intent,
        "origin": origin,
        "venues": venues,
        "weather": res_map["weather"].data if hasattr(res_map["weather"], 'ok') and res_map["weather"].ok else None,
        "air_quality": res_map["aq"].data if hasattr(res_map["aq"], 'ok') and res_map["aq"].ok else None,
        "hazards": res_map["hazards"].data if hasattr(res_map["hazards"], 'ok') and res_map["hazards"].ok else None,
        "routes": res_map["routes"].data if hasattr(res_map["routes"], 'ok') and res_map["routes"].ok else None,
        "daylight": res_map["daylight"].data if hasattr(res_map["daylight"], 'ok') and res_map["daylight"].ok else None,
        "elevation": res_map["elevation"].data if hasattr(res_map["elevation"], 'ok') and res_map["elevation"].ok else None,
        "evidence": evidence,
        "source_status": source_status,
        "limitations": limitations,
        "attribution": "© OpenStreetMap contributors, Open-Meteo, CAMS"
    }
