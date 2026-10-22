"""Unit tests for the geocoder.

The tricky part is that Indian place names are shared: there is a village
called Andheri in Punjab and a suburb of Mumbai called Andheri, and a
"Whitefield" in Greater Manchester and one in Bengaluru. Getting the wrong
one sends the whole recommendation to the wrong part of the world.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.sources.base import app_cache
from app.sources.geocode import GeocodeSource

NOMINATIM = "https://nominatim.openstreetmap.org/search"


@pytest.fixture(autouse=True)
def clear_cache():
    app_cache.clear()
    yield


def _item(name, kind, city=None, display=None, importance=0.2, cc="in"):
    address = {"country_code": cc, "country": "India"}
    if city:
        address["city"] = city
    return {
        "name": name,
        "type": kind,
        "importance": importance,
        "display_name": display or f"{name}, Somewhere, India",
        "address": address,
        "lat": "12.9",
        "lon": "77.5",
    }


@pytest.mark.asyncio
async def test_a_place_in_a_known_city_beats_a_village_with_the_same_name():
    source = GeocodeSource()
    with respx.mock:
        respx.get(NOMINATIM).mock(
            return_value=Response(
                200,
                json=[
                    _item("Andheri", "village", display="Andheri, Sangrur, Punjab, India"),
                    _item(
                        "Andheri",
                        "station",
                        city="Mumbai",
                        importance=0.4,
                        display="Andheri, Andheri West, Mumbai, Maharashtra, India",
                    ),
                ],
            )
        )
        result = await source.geocode("Andheri")

    assert result.ok
    assert result.data[0].short_name.startswith("Andheri")
    assert result.data[0].city == "Mumbai"


@pytest.mark.asyncio
async def test_the_result_is_labelled_with_the_words_the_user_used():
    source = GeocodeSource()
    with respx.mock:
        respx.get(NOMINATIM).mock(
            return_value=Response(
                200,
                json=[
                    _item(
                        "Symbiosis Institute Of International Business School",
                        "educational_institution",
                        city="Pimpri-Chinchwad",
                        display=(
                            "Symbiosis Institute Of International Business School, "
                            "Phase 1, Hinjewadi, Pimpri-Chinchwad, Pune"
                        ),
                    )
                ],
            )
        )
        result = await source.geocode("Hinjewadi")

    assert result.data[0].short_name.startswith("Hinjewadi")


@pytest.mark.asyncio
async def test_an_unknown_place_is_reported_not_invented():
    source = GeocodeSource()
    with respx.mock:
        respx.get(NOMINATIM).mock(return_value=Response(200, json=[]))
        respx.get("https://geocoding-api.open-meteo.com/v1/search").mock(
            return_value=Response(200, json={"results": []})
        )
        respx.get("https://photon.komoot.io/api").mock(
            return_value=Response(200, json={"features": []})
        )
        result = await source.geocode("Nowhereatall")

    assert result.ok is False
    assert result.data is None


@pytest.mark.asyncio
async def test_a_venue_search_ignores_the_wrong_osm_type():
    """A road must not be offered as a cricket pitch."""
    source = GeocodeSource()
    with respx.mock:
        respx.get(NOMINATIM).mock(
            return_value=Response(
                200,
                json=[
                    {
                        "name": "Mumbai Coastal Road",
                        "type": "primary",
                        "display_name": "Mumbai Coastal Road, Bandra, Mumbai",
                        "lat": "19.05",
                        "lon": "72.84",
                    },
                    {
                        "name": "Willow Cricket Ground",
                        "type": "pitch",
                        "display_name": "Willow Cricket Ground, Bandra, Mumbai",
                        "lat": "19.06",
                        "lon": "72.83",
                    },
                ],
            )
        )
        result = await source.search_venues(
            ["cricket pitch"], "Bandra", 19.05, 72.84, 10.0, accept_types=["pitch"]
        )

    names = [v["name"] for v in result.data]
    assert "Willow Cricket Ground" in names
    assert "Mumbai Coastal Road" not in names
