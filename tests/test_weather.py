import pytest
import respx
from httpx import Response
from app.sources.weather import WeatherSource
from app.sources.base import app_cache

@pytest.fixture(autouse=True)
def clear_cache():
    app_cache.clear()
    yield

@pytest.mark.asyncio
async def test_weather_success():
    source = WeatherSource()
    mock_data = {"hourly": {"temperature_2m": [25.0]}}

    with respx.mock:
        respx.get("https://api.open-meteo.com/v1/forecast").mock(
            return_value=Response(200, json=mock_data)
        )

        result = await source.get_forecast([19.1], [72.9], 24)
        assert result.ok is True
        assert result.data == mock_data
        assert result.cached is False

@pytest.mark.asyncio
async def test_weather_failure():
    source = WeatherSource()

    with respx.mock:
        respx.get("https://api.open-meteo.com/v1/forecast").mock(
            return_value=Response(500)
        )

        result = await source.get_forecast([19.1], [72.9], 24)
        assert result.ok is False
        assert result.error is not None
