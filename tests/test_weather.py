"""Unit tests for the weather source (live-shape contract)."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from app.sources.base import app_cache
from app.sources.weather import WeatherSource


@pytest.fixture(autouse=True)
def clear_cache():
    app_cache.clear()
    yield


@pytest.mark.asyncio
async def test_weather_returns_one_payload_per_coordinate():
    source = WeatherSource()
    mock = {
        "latitude": 19.1,
        "hourly": {"temperature_2m": [25.0], "time": ["2026-10-21T00:00"]},
    }

    with respx.mock:
        respx.get("https://api.open-meteo.com/v1/forecast").mock(
            return_value=Response(200, json=[mock, mock])
        )

        result = await source.get_forecast([19.1, 19.2], [72.9, 73.0], 24)

    assert result.ok is True
    # Open-Meteo returns one object per coordinate, and the engine relies on
    # that order to give every venue its own forecast.
    assert isinstance(result.data, list)
    assert len(result.data) == 2
    assert result.data[0]["hourly"]["temperature_2m"] == [25.0]


@pytest.mark.asyncio
async def test_weather_single_coordinate_is_wrapped_in_a_list():
    source = WeatherSource()
    mock = {"latitude": 19.1, "hourly": {"temperature_2m": [25.0]}}

    with respx.mock:
        respx.get("https://api.open-meteo.com/v1/forecast").mock(
            return_value=Response(200, json=mock)
        )

        result = await source.get_forecast([19.1], [72.9], 24)

    assert result.ok is True
    assert isinstance(result.data, list)
    assert len(result.data) == 1


@pytest.mark.asyncio
async def test_weather_failure_is_reported_not_raised():
    source = WeatherSource()

    with respx.mock:
        respx.get("https://api.open-meteo.com/v1/forecast").mock(
            return_value=Response(500)
        )

        result = await source.get_forecast([19.1], [72.9], 24)

    assert result.ok is False
    assert result.error is not None
