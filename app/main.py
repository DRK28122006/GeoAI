from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from app.context import Intent, gather_context
from app.sources.base import app_cache
from app.pipeline import pipeline
import time

app = FastAPI(title="GeoAI Outdoor Assistant API")

class AskRequest(BaseModel):
    question: str

@app.get("/health")
async def health():
    cache_size = len(app_cache)
    return {
        "status": "ok",
        "cache_entries": cache_size,
        "timestamp": time.time()
    }

@app.get("/sources")
async def sources():
    return {
        "sources": [
            "Nominatim", "Photon", "Open-Meteo", "Overpass", "OSRM", "WAQI", "OpenAQ", "Astral"
        ],
        "attribution": "© OpenStreetMap contributors, Open-Meteo, CAMS"
    }

@app.post("/ask")
async def ask(request: AskRequest):
    try:
        result = await pipeline.run(request.question)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/context")
async def get_context(
    location: str = Query(..., description="Location text to geocode"),
    activity: str = Query("running", description="Activity type (e.g., running, hiking)"),
    radius_km: float = Query(5.0, description="Search radius in km"),
    hours_ahead: int = Query(36, description="Forecast window in hours"),
    profile: str = Query("", description="Comma-separated profiles (e.g., asthma,elderly)"),
):
    intent = Intent(
        activity=activity,
        location_text=location,
        radius_km=radius_km,
        profile=profile.split(",") if profile else [],
    )

    try:
        bundle = await gather_context(intent)
        if "error" in bundle:
            raise HTTPException(status_code=400, detail=bundle["error"])
        return bundle
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
