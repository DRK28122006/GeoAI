import os

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # API Keys
    WAQI_TOKEN: str = os.getenv("WAQI_TOKEN", "")
    OPENAQ_API_KEY: str = os.getenv("OPENAQ_API_KEY", "")
    DATA_GOV_IN_KEY: str = os.getenv("DATA_GOV_IN_KEY", "")
    ORS_API_KEY: str = os.getenv("ORS_API_KEY", "")
    CONTACT_EMAIL: str = os.getenv("CONTACT_EMAIL", "hackathon-geoai@example.com")

    # App Settings
    CACHE_DIR: str = os.getenv("CACHE_DIR", ".cache")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Overpass Configuration
    OVERPASS_MIRRORS: list[str] = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.private.coffee/api/interpreter"
    ]

    # Timeouts and TTLs
    DEFAULT_TIMEOUT: float = 10.0
    TTL_GEOCODE: int = 30 * 24 * 3600  # 30 days
    TTL_OVERPASS: int = 24 * 3600       # 24 hours
    TTL_WEATHER_FORECAST: int = 30 * 60 # 30 mins
    TTL_AQ_FORECAST: int = 30 * 60      # 30 mins
    TTL_STATION_OBS: int = 15 * 60       # 15 mins
    TTL_ROUTING: int = 15 * 60          # 15 mins

    @property
    def user_agent(self) -> str:
        return f"GeoAI-Outdoor-Assistant ({self.CONTACT_EMAIL})"

settings = Settings()
