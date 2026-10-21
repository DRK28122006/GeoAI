import asyncio
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "hackathon-geoai@example.com")
HEADERS = {"User-Agent": f"GeoAI-Outdoor-Assistant ({CONTACT_EMAIL})"}

async def check_source(name, url, params=None):
    print(f"Checking {name}...", end=" ", flush=True)
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10.0) as client:
            resp = await client.get(url, params=params)
            latency = (time.perf_counter() - start) * 1000
            if resp.status_code == 200:
                sample = resp.text[:60].replace("\n", " ")
                print(f"PASS {latency:.1f}ms | Sample: {sample}...")
                return True
            else:
                print(f"FAIL {resp.status_code} | {resp.text[:50]}")
                return False
    except Exception as e:  # noqa: BLE001 - the smoke test reports every failure
        print(f"ERROR {type(e).__name__}: {e}")
        return False

async def main():
    sources = [
        ("Nominatim", "https://nominatim.openstreetmap.org/search", {"q": "Powai, Mumbai", "format": "json"}),
        ("Open-Meteo Weather", "https://api.open-meteo.com/v1/forecast", {"latitude": "19.12", "longitude": "72.91", "current_weather": "true"}),
        ("Open-Meteo AQ", "https://air-quality-api.open-meteo.com/v1/air-quality", {"latitude": "19.12", "longitude": "72.91", "current": "true"}),
        ("Overpass API", "https://overpass-api.de/api/interpreter", {"data": "[out:json];node(around:1000,19.12,72.91);out center;"}),
        ("OSRM Table", "http://router.project-osrm.org/table/v1/driving/72.91,19.12;72.92,19.13", {}),
    ]

    results = []
    for name, url, params in sources:
        results.append(await check_source(name, url, params))
        await asyncio.sleep(1) # Be polite

    print("\nSummary: " + " / ".join(["PASS" if r else "FAIL" for r in results]))

if __name__ == "__main__":
    asyncio.run(main())
