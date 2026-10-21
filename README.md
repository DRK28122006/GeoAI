# GeoAI Outdoor Assistant

A FastAPI-based backend for suggesting the best outdoor activities grounded in live geospatial, weather, and air quality data.

## Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
   (Or use `pip install .` if using the provided `pyproject.toml`)

2. **Environment Variables**:
   Copy `.env.example` to `.env` and fill in your API keys.
   - `WAQI_TOKEN`: Required for air quality bias correction.
   - `OPENAQ_API_KEY`: Required for air quality bias correction.
   - `CONTACT_EMAIL`: Required for OSM/Nominatim User-Agent policy.

3. **Run the App**:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 7860
   ```

## Data Sources & Attributions

- **Geocoding**: Nominatim (OpenStreetMap), Photon.
- **Venues & Hazards**: Overpass API (OpenStreetMap).
- **Weather & AQ**: Open-Meteo (CAMS global grid).
- **Routing**: OSRM.
- **Daylight**: Open-Meteo / Astral.

**Attributions**:
© OpenStreetMap contributors, Open-Meteo, CAMS.

## Disclosure
This project was developed with the assistance of Claude Code by Anthropic.
