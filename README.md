# GeoAI Outdoor Assistant

An AI assistant that answers free-form questions like *"I have mild asthma, where's good
for a morning walk near Powai in the next two days?"* with a **specific place and time**,
practical advice, and evidence for every claim.

Everything is grounded in **live** data fetched at query time: OpenStreetMap (venues,
roads, industry, water, green cover), Open-Meteo (weather + air quality), WAQI
(station ground truth), OSRM (travel time) and Open-Meteo/Astral (sunrise, sunset).
The language model is a **locally hosted 1.7B open-weights model** (Qwen3-1.7B, Apache-2.0)
run through llama.cpp. No hosted large model is called at request time.

---

## Quick start

```bash
# 1. Python 3.12 environment (the llama-cpp-python wheels are built for 3.12)
uv venv --python 3.12 .venv
source .venv/bin/activate

# 2. Install the app. On Arch the prebuilt wheel is a musl build, so build from source:
uv pip install cmake ninja
CMAKE_ARGS="-DGGML_NATIVE=ON -DLLAMA_CURL=OFF" \
  uv pip install --no-binary llama-cpp-python "llama-cpp-python==0.3.19"
uv pip install -e ".[dev]"

# 3. Model weights (1.1 GB)
mkdir -p models
curl -L -o models/Qwen3-1.7B-Q4_K_M.gguf \
  "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf"

# 4. Run
uvicorn app.main:app --host 0.0.0.0 --port 7860
```

```bash
curl -s -X POST localhost:7860/ask -H 'Content-Type: application/json' \
  -d '{"question":"Where can I go running tomorrow evening near Koregaon Park in Pune?"}'
```

**Live API:** `https://<your-host>/ask` — set this to the hosted endpoint once it
is deployed.
**Model on Hugging Face:** `https://huggingface.co/<your-org>/geoai-outdoor-assistant`

Then:

```bash
curl -s -X POST localhost:7860/ask -H 'Content-Type: application/json' \
  -d '{"question":"Where can I go running tomorrow evening near Koregaon Park in Pune?"}'
```

The model is loaded **at start-up**, not on the first request, so the first caller of
the judging window does not pay for it.

---

## How it works

```
question
  -> [SLM call 1] intent parser ......... grammar-constrained JSON (activity, place, profile)
  -> gate .................. clarify / decline / proceed
  -> geocode ............... Nominatim -> Open-Meteo -> Photon
  -> venues ................ Overpass (tag search) + Nominatim (POI search), merged
  -> hazards ............... Overpass: roads, industry, power, landfill, water, green
  -> live data ............. Open-Meteo weather + air quality, OSRM travel, daylight
  -> exposure engine ....... scores every (venue x hour): wind alignment, decay, calm
                             factor, traffic proxy, local PM2.5, hard constraints
  -> [SLM call 2] chooser ... picks a slot, writes why / carry / watch-for
  -> verifier .............. every number, time and place must exist in the bundle
  -> response .............. text + JSON + GeoJSON + evidence + limitations
```

**The model never computes and never invents.** It receives an already-scored table and
may only choose from it. A rule-based verifier re-checks every number, clock time and
place name in the model's wording against the fetched data; on failure the model is
asked once more, and if that fails the answer is produced by a deterministic template
built from the same data. This is what makes "support every claim with the live data it
actually used" true rather than aspirational.

### The part that makes place and time matter

The exposure term for a venue at an hour is:

```
E(v,t) = sum over hazard features h of
         class_weight(h) * exp(-distance(v,h) / length_scale(h))    # spatial decay
         * max(0, cos(bearing(h->v) - wind_towards(t)))            # downwind alignment
         * calm_factor(t)                                          # calm air pools pollution
         * traffic_multiplier(h, t)                                # time-of-day proxy

PM2.5_local(v,t) = PM2.5_forecast(t) * station_bias * (1 + beta * E(v,t))
```

so a park that lies downwind of an expressway at 08:00 can score well at 18:00 when the
wind has shifted and the rush-hour multiplier drops. Green cover and water bodies near
the venue reduce the term. Every slot also carries its score breakdown, so the
explanation can cite it.

---

## API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/ask` | the main endpoint - free-form question in, grounded answer out |
| GET | `/health` | liveness, model status, cache size |
| GET | `/context` | debug: the evidence bundle and top slots without the SLM in the loop |
| GET | `/sources` | every data source, its role and the attributions |

### Request

```json
{ "question": "string", "lat": null, "lon": null, "debug": false }
```

`lat`/`lon` are optional; when given they are used as the starting point for the
travel-time calculation.

### Response

```json
{
  "status": "ok | needs_clarification | out_of_scope | partial",
  "clarification_question": null,
  "parsed":      { "activity": "", "location": "", "radius_km": 5.0, "window": {}, "profile": [], "assumptions": [] },
  "recommendation": { "place": "", "time": "", "time_label": "", "score": 0,
                      "why": [], "breakdown": {}, "weather": {}, "air": {}, "evidence": [] },
  "alternatives": [ { "place": "", "time": "", "score": 0 } ],
  "avoid":        [ { "window": "", "venue": "", "reason": "", "evidence": [] } ],
  "advice":       { "carry": [], "watch_for": [], "note": [] },
  "evidence":     { "E1": { "source": "", "value": "", "fetched_at": 0 } },
  "map_geojson":  { "type": "FeatureCollection", "features": [] },
  "data_as_of":   "",
  "sources":      [ { "name": "", "role": "", "license": "" } ],
  "limitations":  [],
  "timings_ms":   {},
  "answer":       "the same recommendation as readable text"
}
```

`map_geojson` contains the origin, the recommended and alternative venues, and the
nearest hazard lines, so a map can render the answer without another lookup. The road
geometries are simplified and capped, so the whole response stays small.

---

## Activities

All ten required activities are supported, each with its own comfort band, wind
sensitivity, rain tolerance, ventilation weight, daylight rules and OSM tag profile
(`app/configs/activities.yaml`):

running, cycling, golf, cricket, football, tennis, hiking, picnic, outdoor event,
outdoor photography.

Personal profiles tighten the limits and change the advice
(`app/configs/profiles.yaml`): asthma, senior, child, pregnancy, and **injury or
recovery** (a fracture, a sprain, an operation). Every profile-adjusted answer says
which limits were tightened and carries a "not medical advice" note.

The injury profile is different on purpose. It does not only tighten limits: it rules
the activity out. Ask about cycling with a fractured foot and the answer says plainly
that cycling is not advisable, points at the doctor's advice, and then names the
calmest nearby spot for when you are cleared. It never prescribes.

---

## Data sources and attribution

| Source | Used for | Licence / credit |
| --- | --- | --- |
| OpenStreetMap (Overpass API, Nominatim) | venues, roads, industry, water, green cover, geocoding | © OpenStreetMap contributors, ODbL |
| Open-Meteo forecast | hourly weather | CC BY 4.0 |
| Open-Meteo air quality (CAMS ENSEMBLE) | PM2.5, PM10, ozone, NO2, US-AQI | CC BY 4.0, Copernicus/ECMWF |
| WAQI | nearest station PM2.5 for the grid bias factor | CC BY 4.0 |
| OSRM (`router.project-osrm.org`) | travel time | BSD, demo server, fair use |
| Astral / Open-Meteo daily | sunrise and sunset | MIT / CC BY 4.0 |

**© OpenStreetMap contributors** must appear wherever OSM data is shown.

### Known limitations (stated in every response)

- The global air-quality grid is roughly 25-40 km wide outside Europe, so venues in one
  city usually share a forecast value. The venue-to-venue difference comes from the
  exposure engine, not from the API.
- There is no live traffic feed. Travel time uses OSRM car routing and a documented
  time-of-day traffic multiplier per road class.
- A hazard's distance is measured to the closest point of its mapped geometry, but the
  road class and traffic profile are coarse.
- OSM coverage is uneven; in smaller towns there may be no mapped venues at all.

---

## Model

| | |
| --- | --- |
| Base model | **Qwen3-1.7B** (`Qwen/Qwen3-1.7B`), Apache-2.0, 2.03B parameters with embeddings - under the 3B limit |
| Serving | `llama-cpp-python` 0.3.19, Q4_K_M GGUF, CPU (AVX-512), 4 threads |
| Reasoning mode | non-thinking (an empty `<think>` block is pre-seeded in the assistant turn) |
| Decoding | JSON schema compiled to a GBNF grammar, temperature 0.1 |

The model is used for two narrow jobs - parsing the question and choosing/explaining
from a pre-scored table - so the small model never has to memorise weather or geography.
Fine-tuning is optional and was not required for the submission; the unmodified base
model is used.

The Hugging Face repository for the deliverable is published separately (weights or
LoRA adapter, model card, open licence) so that it loads with `from_pretrained(...)`.

---

## Disclosure

- **Models:** only `Qwen/Qwen3-1.7B` (Apache-2.0) is used, locally, through llama.cpp.
  No GPT-4-class, Claude or Gemini model is used anywhere in the running assistant.
- **AI coding assistants** were used to write and refactor the code (Claude Code and
  OpenCode). The model itself is only the small open-weights model above.
- **Data:** every source is free and open, and is listed above. No paid or private API
  is used.
- **Privacy:** user queries are not stored. Only aggregate counters and debug logs are
  kept, and the local cache holds public API responses.
- **Third-party libraries.** Every dependency is permissive and compatible with
  Apache-2.0:

  | Library | Licence | Used for |
  | --- | --- | --- |
  | FastAPI | MIT | the HTTP API |
  | Uvicorn | BSD-3 | the ASGI server |
  | httpx | BSD-3 | the HTTP client |
  | Pydantic, pydantic-settings | MIT | validation and settings |
  | Starlette | BSD-3 | the ASGI framework under FastAPI |
  | diskcache | Apache-2.0 | the response cache |
  | PyYAML | MIT | the activity and profile configuration |
  | Astral | Apache-2.0 | the offline sunrise and sunset fallback |
  | llama-cpp-python | MIT | running the model |
  | Jinja2 | BSD-3 | the chat template |
  | NumPy | BSD-3 | pulled in by llama-cpp-python |
  | anyio, certifi, httptools, idna, sniffio, urllib3 | MIT, MIT, MIT, MPL-2.0, MIT, MIT | transitive |
  | uvloop, watchfiles, websockets, python-dotenv | MIT, MIT, BSD-3, BSD-3 | server extras and configuration |
  | pytest, pytest-asyncio, respx, ruff | MIT, Apache-2.0, BSD-3, MIT | development only |

---

## Development

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 7860
pytest -q                                  # unit tests
.venv/bin/python scripts/eval_queries.py   # the 15-question smoke set
.venv/bin/python scripts/eval_queries.py --live
./scripts/model_server.sh status            # optional local llama-server
```

`docs/RUNBOOK.md` documents the environment quirks we hit (User-Agent rules for
Nominatim and Overpass, working mirrors, measured model speed, how to switch thinking
off).

### Layout

```
app/
  main.py             FastAPI: /ask, /health, /context, /sources
  config.py           keys, TTLs, mirrors, per-source User-Agents
  planner.py          "tomorrow evening" -> concrete hours in IST
  slm.py              local model: grammar-constrained JSON, non-thinking
  context.py          gather_context(): live data, evidence, degradation
  exposure_engine.py  deterministic scoring of every venue x hour
  verifier.py         grounding checks + fact table
  pipeline.py         orchestration and the response contract
  sources/            geocode, overpass, weather, airquality, routing, daylight, elevation
app/configs/          activities.yaml, profiles.yaml, hazards.yaml, exposure_weights.yaml
tests/                unit tests
scripts/              eval_queries.py, model_server.sh, smoke_test.py
```
