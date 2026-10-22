"""Geocoding: place name -> coordinates, and a venue search used as a backup.

Two geocoders are asked at the same time and their answers are merged:

* **Nominatim** matches Indian localities well, but it throttles hard and
  sometimes refuses us for a while.
* **Open-Meteo** has no strict rate limit and covers the same places, but it
  is less precise about neighbourhoods.

Because the two run in parallel, one of them being unavailable does not stop
the request. Every source here is free and open.
"""

from __future__ import annotations

import asyncio
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
    "village": 3.5,
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
    "station": 0.8,
    "commercial": 0.8,
    "address": 0.8,
    "building": 0.2,
}

# When a place name is shared by a village and a city district, the one in
# the well known city is what the user means. "Andheri" is a village in
# Punjab and a suburb of Mumbai.
MAJOR_CITIES = {
    "mumbai", "pune", "bengaluru", "bangalore", "delhi", "new delhi", "chennai",
    "hyderabad", "kolkata", "nagpur", "jaipur", "ahmedabad", "surat", "indore",
    "lucknow", "bhopal", "patna", "noida", "gurugram", "gurgaon", "kochi",
    "coimbatore", "visakhapatnam", "varanasi", "agra", "kanpur", "thiruvananthapuram",
    "mysuru", "mysore", "hubballi", "hubli", "dharwad", "kolhapur", "nasik",
    "nashik", "vadodara", "rajkot", "ludhiana", "amritsar", "dehradun", "shimla",
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


def _distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


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
    city: str = ""

    def rank(self) -> float:
        score = KIND_WEIGHT.get(self.kind, 1.0) + 1.5 * math.log10(1 + self.importance)
        # A place in a well known city beats a village with the same name.
        # The city has to be the address's own city, not a district name
        # that happens to contain it.
        if self.city and self.city.lower() in MAJOR_CITIES:
            score += 4.0
        # "Whitefield" exists in the UK and in Bengaluru. When the question
        # does not name a country, prefer the Indian match - this assistant
        # is aimed at Indian cities - and the response states the assumption.
        if self.country_code == "in":
            score += 2.0
        return score


class GeocodeSource(BaseSource):
    def __init__(self):
        # Nominatim's usage policy allows at most 1 request per second and
        # answers 429 when it is pushed harder than that.
        super().__init__("geocode", rate_limit=1, min_interval=1.2)

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
        add(re.sub(r"\s*\b\d+(st|nd|rd|th)\b.*$", "", head, flags=re.IGNORECASE))
        return out[:2]

    async def geocode(self, query: str) -> SourceResult[list[GeocodeCandidate]]:
        query = (query or "").strip()
        if not query:
            return SourceResult(
                source=self.name, ok=False, params={"q": query}, error="empty location"
            )

        params = {"q": query, "format": "json", "addressdetails": 1, "limit": 20}
        cache_key = f"geo:{query.lower()}"

        async def _fetch(self_src: BaseSource):
            nominatim, meteo = await asyncio.gather(
                self_src._nominatim_candidates(query, params),
                self_src._meteo_candidates(query),
                return_exceptions=True,
            )
            candidates: list[GeocodeCandidate] = []
            for part in (nominatim, meteo):
                if isinstance(part, BaseException):
                    logger.warning("geocoder failed: %s", part)
                    continue
                candidates.extend(part)
            if not candidates:
                raise RuntimeError(f"No geocode match for '{query}'")

            # Prefer candidates whose name contains the words the user typed:
            # "Andheri Mumbai" should not resolve to a village called Andheri
            # in another state.
            query_tokens = {t for t in re.split(r"\W+", query.lower()) if len(t) > 2}

            def overlap(c: GeocodeCandidate) -> float:
                name_tokens = set(re.split(r"\W+", c.display_name.lower()))
                return 0.9 * len(query_tokens & name_tokens)

            # One ranking: the OSM kind, the importance and the words the
            # user typed all count. Sorting twice would throw the first
            # ranking away.
            candidates.sort(key=lambda c: c.rank() + overlap(c), reverse=True)

            unique: list[GeocodeCandidate] = []
            for c in candidates:
                if all(
                    abs(c.lat - u.lat) > 0.01 or abs(c.lon - u.lon) > 0.01
                    for u in unique
                ):
                    unique.append(c)

            if not _mentions_country(query):
                indian = [c for c in unique if c.country_code == "in"]
                if indian and unique and unique[0].country_code != "in":
                    unique.remove(indian[0])
                    unique.insert(0, indian[0])
            return unique[:5]

        return await self.fetch(cache_key, settings.TTL_GEOCODE, _fetch, params)

    async def _nominatim_candidates(
        self, query: str, params: dict[str, Any]
    ) -> list[GeocodeCandidate]:
        candidates: list[GeocodeCandidate] = []
        for variant in self._variants(query):
            try:
                resp = await self._request(
                    "GET",
                    "https://nominatim.openstreetmap.org/search",
                    params={**params, "q": variant},
                    headers={"User-Agent": settings.browser_user_agent},
                )
                for item in resp.json() or []:
                    address = item.get("address") or {}
                    display = item.get("display_name", "")
                    name = (item.get("name") or "").strip()
                    if not name:
                        name = display.split(",")[0].strip()
                    locality = (
                        address.get("city")
                        or address.get("town")
                        or address.get("village")
                        or address.get("municipality")
                        or address.get("county")
                        or address.get("state")
                        or ""
                    )
                    # Label the result with the part of the address the user
                    # actually asked for. Searching "Hinjewadi" otherwise
                    # answers with the name of a college in Hinjewadi.
                    label = None
                    for part in (p.strip() for p in display.split(",")):
                        if part and part.lower() in query.lower():
                            label = part
                            break
                    if not label and query.lower() in display.lower():
                        label = query.strip()
                    if not label:
                        label = (
                            address.get("suburb")
                            or address.get("neighbourhood")
                            or address.get("city_district")
                            or name
                        )
                    short = (
                        f"{label}, {locality}"
                        if locality and locality != label
                        else label
                    )
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
                            city=(
                                address.get("city")
                                or address.get("town")
                                or address.get("municipality")
                                or ""
                            ),
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - fall through
                logger.warning("Nominatim failed for %r: %s", variant, exc)
        return candidates

    async def _meteo_candidates(self, query: str) -> list[GeocodeCandidate]:
        candidates: list[GeocodeCandidate] = []
        try:
            resp = await self._request(
                "GET",
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": query, "count": 5},
            )
            for item in resp.json().get("results", []):
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
        return candidates

    async def search_venues(
        self,
        terms: list[str],
        place: str,
        lat: float,
        lon: float,
        radius_km: float,
        accept_types: list[str] | None = None,
    ) -> SourceResult[list[dict[str, Any]]]:
        """Find named venues with Nominatim's POI search.

        This runs in parallel with the Overpass lookup. Overpass has the best
        tag coverage but can be slow or at capacity; Nominatim answers in about
        a second, so merging the two keeps the shortlist good either way.

        Nominatim answers a place search with a mix of the thing we asked for
        and unrelated places nearby, so the results are filtered on the OSM
        type it reports. That is what stops a road or a bank showing up as a
        cricket pitch.
        """
        if not terms or not place:
            return SourceResult(source=self.name, ok=True, params={}, data=[])

        accept = {t.lower() for t in (accept_types or [])}
        cache_key = (
            f"venues_nom:{place.lower()}:{'|'.join(sorted(terms))}:"
            f"{radius_km}:{sorted(accept)}"
        )

        async def _fetch(self_src: BaseSource):
            found: list[dict[str, Any]] = []
            answered = 0
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
                    answered += 1
                    for item in resp.json() or []:
                        try:
                            vlat = float(item["lat"])
                            vlon = float(item["lon"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        if _distance_km((vlat, vlon), (lat, lon)) > radius_km:
                            continue
                        kind = (item.get("type") or item.get("category") or "").lower()
                        if accept and kind and kind not in accept:
                            continue
                        found.append(
                            {
                                "name": item.get("display_name", "").split(",")[0]
                                or item.get("name")
                                or term.title(),
                                "lat": vlat,
                                "lon": vlon,
                                "kind": kind or term,
                                "display_name": item.get("display_name", ""),
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Nominatim venue search failed for %r: %s", query, exc)
            if answered == 0 and terms:
                # Every search failed. Returning an empty list here would be
                # cached as a successful empty result for a month.
                raise RuntimeError("every Nominatim venue search failed")
            return found

        return await self.fetch(cache_key, settings.TTL_GEOCODE, _fetch, {"place": place})
