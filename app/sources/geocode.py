"""Geocoding: place name -> coordinates.

Nominatim first (it understands Indian localities well), then Open-Meteo's
geocoder. Photon is kept only as a last resort: it answers 403 from some
networks. Every source here is free and open.

Nominatim happily returns a POI called "Play on Fitness JP Nagar" for the
query "JP Nagar 5th Phase in Bengaluru", so candidates are ranked by OSM
*kind* (a suburb or neighbourhood beats a shop) and the query is tried in a
few variants.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from pydantic import BaseModel

from app.config import settings
from app.sources.base import BaseSource, SourceResult

logger = logging.getLogger("geoai.geocode")

# higher is better - a locality beats a point of interest
KIND_WEIGHT: dict[str, float] = {
    "suburb": 6.0,
    "neighbourhood": 6.0,
    "quarter": 5.5,
    "borough": 5.0,
    "village": 5.0,
    "municipality": 4.5,
    "district": 4.5,
    "town": 4.0,
    "city": 4.0,
    "hamlet": 3.5,
    "county": 3.0,
    "administrative": 2.5,
    "state": 2.0,
    "region": 2.0,
    "poi": 1.0,
    "address": 0.8,
    "building": 0.2,
}


COUNTRIES = {
    "india", "indian", "bengaluru", "bangalore", "mumbai", "delhi", "pune", "hyderabad",
    "chennai", "kolkata", "noida", "gurugram", "gurgaon", "ahmedabad", "jaipur", "kochi",
    "uk", "u.k.", "united kingdom", "england", "london", "scotland", "ireland", "usa",
    "u.s.", "america", "united states", "canada", "australia", "singapore", "dubai",
    "uae", "qatar", "malaysia", "sri lanka", "nepal", "myanmar",
}


def _mentions_country(query: str) -> bool:
    q = (query or "").lower()
    return any(c in q for c in COUNTRIES)


class GeocodeCandidate(BaseModel):
    display_name: str
    short_name: str
    lat: float
    lon: float
    confidence: float
    source: str
    kind: str = "unknown"
    importance: float = 0.0
    country_code: str = ""

    def rank(self) -> float:
        score = KIND_WEIGHT.get(self.kind, 1.0) + 1.5 * math.log10(1 + self.importance)
        # "Whitefield" exists in the UK and in Bengaluru. When the question
        # does not name a country, prefer the Indian match - this assistant
        # is aimed at Indian cities - and the response states the assumption.
        if self.country_code == "in":
            score += 2.0
        return score


class GeocodeSource(BaseSource):
    def __init__(self):
        # Nominatim's usage policy allows at most 1 request per second.
        super().__init__("geocode", rate_limit=1)

    @staticmethod
    def _variants(query: str) -> list[str]:
        """Query spellings to try, most specific first.

        "JP Nagar 5th Phase, Bengaluru" is usually mapped as the locality
        "JP Nagar", so the ordinal suffix is stripped as well.
        """
        out: list[str] = []

        def add(value: str) -> None:
            value = value.strip().strip(",")
            if value and value not in out and len(value) > 2:
                out.append(value)

        add(query)
        head = query.split(",")[0]
        add(head)
        without_ordinal = re.sub(r"\s*\b\d+(st|nd|rd|th)\b.*$", "", head, flags=re.IGNORECASE)
        add(without_ordinal)
        for cue in (" near ", " around ", " in ", " at ", " close to "):
            idx = query.lower().find(cue)
            if idx > 0:
                tail = query[idx + len(cue) :].split(",")[0]
                add(tail)
                add(re.sub(r"\s*\b\d+(st|nd|rd|th)\b.*$", "", tail, flags=re.IGNORECASE))
        return out[:4]

    async def geocode(self, query: str) -> SourceResult[list[GeocodeCandidate]]:
        query = (query or "").strip()
        if not query:
            return SourceResult(
                source=self.name, ok=False, params={"q": query}, error="empty location"
            )

        params = {"q": query, "format": "json", "addressdetails": 1, "limit": 8}
        cache_key = f"geo:{query.lower()}"

        async def _fetch(self_src: BaseSource):
            candidates: list[GeocodeCandidate] = []

            # ---- Nominatim, with a couple of query variants ----------------
            # Every variant is tried and the results are merged: a single
            # query often returns only POIs, while a shorter spelling of the
            # same place returns the neighbourhood itself.
            for variant in self_src._variants(query):
                try:
                    resp = await self_src._request(
                        "GET",
                        "https://nominatim.openstreetmap.org/search",
                        params={**params, "q": variant},
                        headers={"User-Agent": settings.browser_user_agent},
                    )
                    data = resp.json()
                    for item in data:
                        address = item.get("address") or {}
                        name = (item.get("name") or "").strip()
                        if not name:
                            name = item.get("display_name", "").split(",")[0].strip()
                        locality = (
                            address.get("city")
                            or address.get("town")
                            or address.get("village")
                            or address.get("municipality")
                            or address.get("county")
                            or address.get("state")
                            or ""
                        )
                        short = f"{name}, {locality}" if locality and locality != name else name
                        candidates.append(
                            GeocodeCandidate(
                                display_name=item.get("display_name", variant),
                                short_name=short or variant,
                                lat=float(item["lat"]),
                                lon=float(item["lon"]),
                                confidence=1.0,
                                source="nominatim",
                                kind=item.get("type") or item.get("category") or "unknown",
                                importance=float(item.get("importance") or 0.0),
                                country_code=(address.get("country_code") or "").lower(),
                            )
                        )
                except Exception as exc:  # noqa: BLE001 - fall through
                    logger.warning("Nominatim failed for %r: %s", variant, exc)

            # ---- Open-Meteo geocoding --------------------------------------
            if not candidates:
                try:
                    resp = await self_src._request(
                        "GET",
                        "https://geocoding-api.open-meteo.com/v1/search",
                        params={"name": query, "count": 5},
                    )
                    data = resp.json()
                    for item in data.get("results", []):
                        candidates.append(
                            GeocodeCandidate(
                                display_name=(
                                    f"{item.get('name', '')}, {item.get('admin1', '')}, "
                                    f"{item.get('country', '')}"
                                ).strip(", "),
                                short_name=item.get("name", query),
                                lat=float(item["latitude"]),
                                lon=float(item["longitude"]),
                                confidence=0.6,
                                source="open-meteo",
                                kind="place",
                                importance=0.5,
                                country_code=(item.get("country_code") or "").lower(),
                            )
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Open-Meteo geocoding failed: %s", exc)

            # ---- Photon (often blocked; last resort) ----------------------
            if not candidates:
                try:
                    resp = await self_src._request(
                        "GET",
                        "https://photon.komoot.io/api",
                        params={"q": query, "limit": 5},
                    )
                    for f in resp.json().get("features", []):
                        candidates.append(
                            GeocodeCandidate(
                                display_name=(
                                    f"{f['properties'].get('name', '')}, "
                                    f"{f['properties'].get('city', '')}"
                                ).strip(", "),
                                short_name=f["properties"].get("name", query),
                                lat=float(f["geometry"]["coordinates"][1]),
                                lon=float(f["geometry"]["coordinates"][0]),
                                confidence=0.5,
                                source="photon",
                                kind="place",
                                importance=0.3,
                            )
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Photon failed: %s", exc)

            if not candidates:
                raise RuntimeError(f"No geocode match for '{query}'")

            # best first, and drop near-duplicates
            candidates.sort(key=lambda c: c.rank(), reverse=True)

            # Prefer candidates whose name actually contains the words the user
            # typed: "Andheri Mumbai" should not resolve to a village called
            # Andheri in another state.
            query_tokens = {
                t for t in re.split(r"[^\w]+", query.lower()) if len(t) > 2
            }
            if query_tokens:
                def overlap(c: GeocodeCandidate) -> float:
                    name_tokens = set(re.split(r"[^\w]+", c.display_name.lower()))
                    return 0.9 * len(query_tokens & name_tokens)

                candidates.sort(key=overlap, reverse=True)

            unique: list[GeocodeCandidate] = []
            for c in candidates:
                if all(
                    abs(c.lat - u.lat) > 0.01 or abs(c.lon - u.lon) > 0.01
                    for u in unique
                ):
                    unique.append(c)

            # "Whitefield" exists in Greater Manchester and in Bengaluru. If the
            # question names no country and an Indian match exists, use it - and
            # the response states the assumption.
            if not _mentions_country(query):
                indian = [c for c in unique if c.country_code == "in"]
                if indian and unique and unique[0].country_code != "in":
                    unique.remove(indian[0])
                    unique.insert(0, indian[0])
            return unique[:5]

        return await self.fetch(cache_key, settings.TTL_GEOCODE, _fetch, params)

    async def search_venues(
        self, terms: list[str], place: str, lat: float, lon: float, radius_km: float
    ) -> SourceResult[list[dict[str, Any]]]:
        """Find named venues with Nominatim's POI search.

        This runs in parallel with the Overpass lookup. Overpass has the best
        tag coverage but can be slow or at capacity; Nominatim answers in about
        a second, so merging the two keeps the shortlist good either way.
        """
        if not terms or not place:
            return SourceResult(source=self.name, ok=True, params={}, data=[])

        cache_key = f"venues_nom:{place.lower()}:{'|'.join(sorted(terms))}:{radius_km}"

        async def _fetch(self_src: BaseSource):
            found: list[dict[str, Any]] = []
            for term in terms:
                query = f"{term} in {place}"
                try:
                    resp = await self_src._request(
                        "GET",
                        "https://nominatim.openstreetmap.org/search",
                        params={
                            "q": query,
                            "format": "json",
                            "addressdetails": 1,
                            "limit": 20,
                        },
                        headers={"User-Agent": settings.browser_user_agent},
                    )
                    for item in resp.json() or []:
                        try:
                            vlat = float(item["lat"])
                            vlon = float(item["lon"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        if _distance_km((vlat, vlon), (lat, lon)) > radius_km:
                            continue
                        found.append(
                            {
                                "name": item.get("display_name", "").split(",")[0]
                                or item.get("name")
                                or term.title(),
                                "lat": vlat,
                                "lon": vlon,
                                "kind": item.get("type") or item.get("category") or term,
                                "display_name": item.get("display_name", ""),
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Nominatim venue search failed for %r: %s", query, exc)
            return found

        return await self.fetch(cache_key, settings.TTL_GEOCODE, _fetch, {"place": place})


def _distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math

    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))
