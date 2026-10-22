"""Central configuration.

Two User-Agent rules were established empirically and must not be merged:

* Nominatim (OSM Foundation) 403s a short contact-style UA. It only answers a
  browser-shaped UA that still carries a contact address.
* Overpass 406s **any** UA that starts with "Mozilla/". It only answers a
  short, honest, non-browser UA.

See docs/RUNBOOK.md for the raw evidence.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings

REPO_ROOT = Path(__file__).resolve().parent.parent
# The configs are shipped inside the package so an installed copy works, and
# the repository copy is used when running from a checkout.
CONFIG_DIR = Path(__file__).resolve().parent / "configs"
if not CONFIG_DIR.is_dir():
    CONFIG_DIR = REPO_ROOT / "configs"


class Settings(BaseSettings):
    # --- API keys (optional; the assistant degrades gracefully without them) ---
    WAQI_TOKEN: str = os.getenv("WAQI_TOKEN", "")
    OPENAQ_API_KEY: str = os.getenv("OPENAQ_API_KEY", "")
    DATA_GOV_IN_KEY: str = os.getenv("DATA_GOV_IN_KEY", "")
    ORS_API_KEY: str = os.getenv("ORS_API_KEY", "")

    # NOTE: the contact string ends up in the User-Agent. Overpass blocks any
    # UA containing a placeholder domain ("example.com"): it answers 403 on
    # overpass.openstreetmap.fr and 406 on the .de mirrors. Use a real
    # contact (email or repository URL) or leave it empty.
    CONTACT_EMAIL: str = os.getenv("CONTACT_EMAIL", "")
    REPO_URL: str = os.getenv("REPO_URL", "https://github.com/DRK28122006/GeoAI")

    # --- App settings ---
    CACHE_DIR: str = os.getenv("CACHE_DIR", str(REPO_ROOT / ".cache"))
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Kolkata")
    MAX_WORKERS: int = int(os.getenv("MAX_WORKERS", "1"))

    # --- SLM ---
    SLM_MODEL_PATH: str = os.getenv(
        "SLM_MODEL_PATH", str(REPO_ROOT / "models" / "Qwen3-1.7B-Q4_K_M.gguf")
    )
    SLM_BASE_MODEL: str = os.getenv("SLM_BASE_MODEL", "Qwen/Qwen3-1.7B")
    SLM_N_CTX: int = int(os.getenv("SLM_N_CTX", "4096"))
    SLM_N_THREADS: int = int(os.getenv("SLM_N_THREADS", "4"))
    SLM_N_BATCH: int = int(os.getenv("SLM_N_BATCH", "256"))
    SLM_MAX_TOKENS: int = int(os.getenv("SLM_MAX_TOKENS", "220"))
    # Load the model at process start instead of on the first request.
    SLM_EAGER_LOAD: bool = os.getenv("SLM_EAGER_LOAD", "true").lower() in {
        "1",
        "true",
        "yes",
    }
    # "inprocess" (llama-cpp-python in the API process) or "server" (llama-server HTTP)
    SLM_BACKEND: str = os.getenv("SLM_BACKEND", "inprocess")
    SLM_SERVER_URL: str = os.getenv("SLM_SERVER_URL", "http://127.0.0.1:8080")

    # --- Networking ---
    DEFAULT_TIMEOUT: float = float(os.getenv("DEFAULT_TIMEOUT", "12.0"))
    OVERPASS_TIMEOUT: float = float(os.getenv("OVERPASS_TIMEOUT", "18.0"))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "2"))

    # --- Cache TTLs (seconds) ---
    # Bump this when a source changes shape, so stale entries are not reused.
    CACHE_VERSION: str = os.getenv("CACHE_VERSION", "v3")
    TTL_GEOCODE: int = 30 * 24 * 3600
    TTL_OVERPASS: int = 6 * 3600
    TTL_WEATHER_FORECAST: int = 30 * 60
    TTL_AQ_FORECAST: int = 30 * 60
    TTL_STATION_OBS: int = 15 * 60
    TTL_ROUTING: int = 15 * 60
    TTL_DAYLIGHT: int = 6 * 3600
    TTL_ELEVATION: int = 30 * 24 * 3600

    # --- Overpass mirrors (first healthy one wins) ---
    # overpass-api.de and its lz4/z siblings are often at capacity and answer
    # 502 quickly; the .fr instance is the fast one but whitelists UAs.
    OVERPASS_MIRRORS: list[str] = [
        "https://overpass.openstreetmap.fr/api/interpreter",
        "https://z.overpass-api.de/api/interpreter",
        "https://lz4.overpass-api.de/api/interpreter",
        "https://overpass-api.de/api/interpreter",
    ]
    # Total budget for one Overpass round trip before we give up and answer
    # with a limitation instead of hanging the request.
    OVERPASS_BUDGET_S: float = float(os.getenv("OVERPASS_BUDGET_S", "15"))

    @property
    def user_agent(self) -> str:
        """Short, honest UA for Overpass and the other open APIs."""
        contact = self.CONTACT_EMAIL or self.REPO_URL
        return f"GeoAI-Hackathon/1.0 ({contact})"

    @property
    def browser_user_agent(self) -> str:
        """Nominatim only answers a browser-shaped UA that carries a contact."""
        contact = self.CONTACT_EMAIL or self.REPO_URL
        return f"Mozilla/5.0 (compatible; GeoAI-Hackathon/1.0; +{contact})"

    def user_agent_for(self, source: str) -> str:
        if source == "geocode":
            return self.browser_user_agent
        return self.user_agent


settings = Settings()
