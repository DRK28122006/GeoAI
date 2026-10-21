from pydantic import BaseModel

from app.config import settings
from app.sources.base import BaseSource, SourceResult


class GeocodeCandidate(BaseModel):
    display_name: str
    lat: float
    lon: float
    confidence: float  # 0.0 to 1.0
    source: str

class GeocodeSource(BaseSource):
    def __init__(self):
        # Nominatim has strict 1 req/s limit
        super().__init__("geocode", rate_limit=1)

    async def geocode(self, query: str) -> SourceResult[list[GeocodeCandidate]]:
        params = {"q": query, "format": "json", "addressdetails": 1, "limit": 5}
        cache_key = f"geo:{query}"

        async def _fetch(self_src: BaseSource):
            # Pipeline: Nominatim -> Photon -> Open-Meteo

            # 1. Nominatim
            try:
                resp = await self_src._request("GET", "https://nominatim.openstreetmap.org/search", params=params)
                data = resp.json()
                if data:
                    return [
                        GeocodeCandidate(
                            display_name=item["display_name"],
                            lat=float(item["lat"]),
                            lon=float(item["lon"]),
                            confidence=1.0 if item.get("importance") is None else 0.8,
                            source="nominatim"
                        ) for item in data
                    ]
            except Exception:
                pass

            # 2. Photon
            try:
                photon_params = {"q": query, "limit": 5}
                resp = await self_src._request("GET", "https://photon.komoot.io/api", params=photon_params)
                data = resp.json()
                if data.get("features"):
                    return [
                        GeocodeCandidate(
                            display_name=f"{f['properties'].get('name', '')} {f['properties'].get('city', '')}",
                            lat=f["geometry"]["coordinates"][1],
                            lon=f["geometry"]["coordinates"][0],
                            confidence=0.7,
                            source="photon"
                        ) for f in data["features"]
                    ]
            except Exception:
                pass

            # 3. Open-Meteo Geocoding
            try:
                om_params = {"name": query}
                resp = await self_src._request("GET", "https://geocoding-api.open-meteo.com/v1/search", params=om_params)
                data = resp.json()
                if data.get("results"):
                    return [
                        GeocodeCandidate(
                            display_name=item["name"],
                            lat=item["latitude"],
                            lon=item["longitude"],
                            confidence=0.6,
                            source="open-meteo"
                        ) for item in data["results"]
                    ]
            except Exception:
                pass

            raise RuntimeError("No plausible geocode matches found")

        return await self.fetch(cache_key, settings.TTL_GEOCODE, _fetch, params)
