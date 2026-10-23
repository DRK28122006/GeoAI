# The API — for the frontend team

**You call our service. You do not call the model.**

The Hugging Face repository (`som-doe2007/geoai-outdoor-assistant`) is a
deliverable for the judges: it proves the project ships a real open-weights
model with a model card and a licence, and it loads with `from_pretrained`.
It is **not** the app. The model is one step inside a pipeline, and that
pipeline is what answers questions.

```
frontend  ──POST /ask──▶  our service  ──▶ geocode, venues, weather, air quality
                                                          │
   ◀──answer, proof, GeoJSON──  exposure engine ──▶ verifier
```

## The base URL

```
https://<the deployed host>
```

It will be a real address by the time we submit. For local development:

```bash
cd ~/Projects/GEOai/deepesh-repo
uvicorn app.main:app --host 127.0.0.1 --port 7860
```

CORS is already enabled for every origin, so a browser on any port can call it.

## The one endpoint the UI needs

```http
POST /ask
Content-Type: application/json

{ "question": "Where can I go running tomorrow evening near Koregaon Park in Pune?" }
```

Optional: `"lat"` and `"lon"` (numbers) to start from the user's location
instead of a place name, and `"debug": true`.

### What comes back

```json
{
  "status": "ok",
  "answer": "Go to ABC Farms around 17:00 on Fri 23 Oct.\n- E_WEATHER: feels like 30C with 0% chance of rain\n- E_AQ: US-AQI 126 locally\n- E_HAZARDS: local exposure index 0.26\n- E_ROUTES: about 3 min away\nCarry: Water bottle, Sunscreen and a hat.\nWatch out for: Elevated local PM2.5 (52 ug/m3).",
  "request_id": "d17c486ec390",
  "answer_source": "model",
  "parsed": {
    "activity": "running",
    "activity_label": "Running or jogging",
    "location": "Koregaon Park, Pune",
    "radius_km": 5.0,
    "window": { "start": "2026-10-23T17:00:00+05:30", "end": "2026-10-23T21:00:00+05:30", "label": "tomorrow evening" },
    "profile": [],
    "assumptions": []
  },
  "recommendation": {
    "slot_id": "S1-102317",
    "place": "ABC Farms",
    "lat": 18.5366, "lon": 73.8932,
    "time": "2026-10-23T17:00:00+05:30",
    "time_label": "Fri 23 Oct, 17:00",
    "score": 53.9,
    "why": ["E_WEATHER: feels like 30C with 0% chance of rain"],
    "breakdown": { "comfort": 61.2, "air": 100.0, "wind_rain": 92.0, "daylight": 100.0, "travel": 92.0 },
    "weather": { "apparent_temp_c": 30.0, "precip_prob": 0, "wind_kmh": 9.0, "wind_from": "W", "uv_index": 4.0 },
    "air": { "us_aqi": 126, "local_pm2_5": 52.0, "exposure": 0.26 },
    "travel_min": 3.0,
    "evidence": ["E_WEATHER", "E_AQ", "E_HAZARDS", "E_ROUTES"]
  },
  "alternatives": [ { "place": "Aditi Garden", "time_label": "Fri 23 Oct, 17:00", "score": 51.2, "lat": 18.52, "lon": 73.90 } ],
  "avoid": [ { "window": "Fri 23 Oct, 19:00", "venue": "Some Ground", "reason": "86% chance of rain", "evidence": ["E_WEATHER"] } ],
  "advice": { "carry": ["Water bottle", "Sunscreen and a hat"], "watch_for": ["Elevated local PM2.5 (52 ug/m3)"], "note": [] },
  "evidence": { "E_WEATHER": { "source": "Open-Meteo forecast", "value": "Hourly forecast for 13 locations" } },
  "proof": {
    "headline": "ABC Farms at Fri 23 Oct, 17:00 scored 53.9 out of 100",
    "factors": [
      { "factor": "weather", "value": "feels like 30C, 0% chance of rain, wind 9.0 km/h from W", "source": "Open-Meteo forecast", "evidence": "E_WEATHER" },
      { "factor": "air quality", "value": "US-AQI 126, local PM2.5 estimated 52 ug/m3, bias x1.0", "source": "Open-Meteo air quality (CAMS ENSEMBLE)", "evidence": "E_AQ" },
      { "factor": "local exposure", "value": "index 0.26 (no busy roads downwind)", "source": "OpenStreetMap (Overpass API)", "evidence": "E_HAZARDS" },
      { "factor": "travel", "value": "about 3 min by car, 1.8 km", "source": "OSRM", "evidence": "E_ROUTES" },
      { "factor": "daylight", "value": "sunrise 06:10 / sunset 17:56 (IST)", "source": "Open-Meteo daily", "evidence": "E_DAYLIGHT" }
    ],
    "scoring": { "comfort": 61.2, "air": 100.0, "wind_rain": 92.0, "daylight": 100.0, "travel": 92.0 }
  },
  "map_geojson": { "type": "FeatureCollection", "features": [] },
  "data_as_of": "2026-10-22T18:02:11+05:30",
  "sources": [ { "name": "© OpenStreetMap contributors (Overpass API, Nominatim)", "role": "venues, hazards, roads, water, green cover", "license": "ODbL" } ],
  "attribution": "© OpenStreetMap contributors (Overpass, Nominatim); Open-Meteo; CAMS ENSEMBLE data (Copernicus/ECMWF); OSRM",
  "limitations": ["No air-quality station token configured; forecast grid used as-is."],
  "timings_ms": { "geocode_ms": 3077, "venues_ms": 4549, "hazards_ms": 22185, "slm_parse_ms": 9771, "slm_choose_ms": 10330, "total_ms": 29812 }
}
```

## The status values, and what the UI should do with them

| `status` | What it means | What the UI does |
|---|---|---|
| `ok` | a recommendation | show it, with the proof |
| `partial` | a recommendation, but with a caveat | show it, and show the caveat |
| `needs_clarification` | we need a place | show `clarification_question` as an input prompt |
| `out_of_scope` | not an outdoor question | show the message |
| `busy` | too many questions at once, or a source timed out | show "try again in a few seconds" and retry |

## The other endpoints

```http
GET /ready      200 when the model is loaded, 503 when it is not
GET /live       always 200 while the process is up
GET /sources    the data sources, their roles and their licences
GET /context    the raw evidence bundle, for debugging
```

## A plain JavaScript example

```js
const API = "https://<the deployed host>";

async function ask(question) {
  const response = await fetch(`${API}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json();
}

// then
const result = await ask("Where can I go running tomorrow evening near Koregaon Park?");
document.getElementById("answer").textContent = result.answer;

// the map
const map = new maplibregl.Map({ container: "map" });
for (const feature of result.map_geojson.features) {
  new maplibregl.Marker()
    .setLngLat(feature.geometry.coordinates)
    .setPopup(new maplibregl.Popup().setText(feature.properties.name))
    .addTo(map);
}
```

## Things worth knowing

- **Times are Indian time.** Every timestamp in the response carries `+05:30`.
- **Show the proof.** `proof.factors` is the part that makes the answer
  trustworthy: each line says what the condition was, which source it came
  from, and which evidence key it is. A judge or a user can check any of it.
- **Show the credit.** `attribution` and `sources` are in every response.
  The rulebook asks for "© OpenStreetMap contributors", so render it.
- **`answer_source` is worth showing in a debug panel.** It is `model` when the
  model wrote the sentence and `template` when the verifier rejected the
  model's wording and the engine produced it instead. Both are correct; the
  flag is there so nobody is misled.
- **`limitations` are not errors.** They are the honest caveats, for example
  "the forecast grid is coarse" or "the places were spread over 5 km".
- **A question takes 20 to 40 seconds.** It fetches live data from six
  services, scores every venue against every hour and then writes the answer.
  Please show a progress state, and do not set a client timeout below 60
  seconds.
- **Nothing here is fabricated.** If a live source is down, the answer says
  which one, instead of inventing a number.
