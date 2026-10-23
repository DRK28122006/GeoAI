# GeoAI Outdoor Assistant: Product Requirements Document

GeoAI Hackathon @ CSRE, IIT Bombay | Prepared 21 Oct 2026 | Submission deadline: 24 Oct 2026, 11:59 PM IST | Status: v1.0, decisions locked except where marked OPEN

## 1. Summary

We are building an assistant that answers free-form questions such as "I have mild asthma, where's good for a morning walk near Powai in the next two days?" with a specific **place + time** recommendation, practical advice (what to carry, what to watch for, when to avoid), and evidence for every claim.

The assistant fetches everything live (maps, weather, air quality, routing, daylight), scores every venue and hour with a deterministic geo-exposure engine, and uses a small open language model (Qwen3-1.7B) for three narrow jobs: parsing the question, choosing and explaining from a pre-scored shortlist, and writing the advice. A verifier rejects any number, place, or time in the text that is not in the fetched evidence.

**One-line pitch:** the SLM plans and explains; the tools compute and supply the facts; the verifier guarantees nothing is invented.

## 2. Goals, non-goals, success criteria

### Goals

1. A live HTTP endpoint that handles messy free-form queries for at least 10 activities and stays up through the judging window.
2. Recommendations that visibly depend on **both place and hour** (the wind-shift and downwind-of-expressway example in the rulebook).
3. Personal profiles (asthma, senior, child, pregnancy) that change the outputs, not just the disclaimers.
4. Groundedness: every claim traces to fetched data with a source and timestamp.
5. Graceful behaviour on missing location, vague time, and out-of-scope requests.
6. All rulebook deliverables: endpoint, HF model repo, code repo, README, 4 to 6 slides, live demo.

### Non-goals

- Training a model from scratch, or any large hosted model anywhere in the running assistant.
- Live traffic data (no free source we trust; we use a time-of-day proxy and say so).
- Medical advice. Profile handling tightens thresholds; the response carries a clear "not medical advice" note.
- A polished consumer UI. A simple map page is a stretch goal; the API is the product.
- Pre-baked city data. Caching of live responses is allowed; shipping city datasets is not.

### Success criteria (what we will demonstrate)

| # | Criterion | Target |
| --- | --- | --- |
| S1 | Parse accuracy on our 40-query test set (activity, location, window, profile) | 90% or better on fields that are present in the query |
| S2 | Groundedness: numeric, place, and time claims traceable to evidence | 100% after verifier (fallback template if needed) |
| S3 | Same query with and without a health profile produces a different top slot or different advice | Demonstrated live |
| S4 | Same query at two different hours or venues shows a score change driven by wind direction | Demonstrated live |
| S5 | End-to-end latency, warm cache | p50 under 20 s, p95 under 45 s |
| S6 | Endpoint availability from 21 Oct to end of judging | No manual restarts needed |

## 3. Rulebook traceability

| Rulebook requirement | Where it is met |
| --- | --- |
| Identify activity, area or radius, time range, personal detail | FR-1 (intent parser) |
| Consider several nearby places across upcoming hours | FR-4, FR-7 (venue shortlist and venue-by-hour grid) |
| Wind direction, roads and traffic, industry, water, green cover | FR-6 (geo-exposure engine) |
| Recommendation plus carry, watch for, avoid | FR-9, FR-10 |
| Every claim supported by live data, nothing invented | FR-11 (evidence IDs and verifier) |
| Plain text or richer output | FR-12 (text, JSON, GeoJSON) |
| At least 10 activities | Section 8, activity profiles |
| Missing location, vague time, out of scope | FR-2 (gate) |
| Model of at most 3B parameters, open weights, on Hugging Face, loads with from\_pretrained | Section 6 and 14 |
| No paid or private data, no hard-coded answers, no memory answers | Sections 5, 9, 11 |
| Credit "© OpenStreetMap contributors"; disclose models, sources, tools | Section 15 |

## 4. Users and key scenarios

**Primary user:** an urban Indian resident planning an outdoor activity in the next 1 to 7 days, often with a health or family consideration.

| Scenario | Expected behaviour |
| --- | --- |
| "Where can I go running tomorrow evening near \<area>?" | Top 1 venue and time slot, 2 alternatives, avoid windows, advice |
| "I have mild asthma, morning walk near \<area> in the next two days" | Stricter air thresholds; recommends venue and hour with lowest local exposure; says why |
| "Is it okay to cycle to work at 8 AM?" | Missing location: ask a clarifying question, do not guess |
| "Best time this weekend for kids' cricket within 15 km of \<area>" | Resolves "this weekend" to explicit dates, states the assumption, applies child limits, travel penalty for 15 km |
| "Write me a poem about the monsoon" | Out of scope: polite decline, offer what we can do |
| "Golf on Saturday within 40 km" | Larger radius, few venues, travel time weighs more |

## 5. Locked decisions

| Area | Decision | Notes |
| --- | --- | --- |
| Language and framework | Python 3.11, FastAPI, httpx (async), Pydantic | Team is comfortable with these |
| Model | **Qwen3-1.7B** (about 1.7B parameters, 2.03B including embeddings; Apache 2.0) in non-thinking mode | Verify the licence tag on the exact repo. Challenger in bake-off: Qwen3.5-2B (2.3B with vision encoder). Fallback: Qwen2.5-1.5B-Instruct. Rejected: Sarvam-1 (base-only, non-commercial licence) |
| Serving | llama.cpp (llama-cpp-python or llama-server), Q4\_K\_M GGUF, JSON-schema constrained decoding for the parser | Keep schemas small; grammar guarantees syntax, not meaning, so validate in code |
| Geocoding | Nominatim, fallback Photon, then Open-Meteo geocoding | 1 req/s limit on Nominatim; cache 30 days |
| Places, roads, industry, water, green | Overpass API with 2 to 3 mirror failover | One query for venues, one for hazards and mitigators |
| Weather | Open-Meteo forecast (multi-coordinate calls) | Attribution required |
| Air quality forecast | Open-Meteo air-quality (CAMS) | **Global CAMS grid is roughly 25 to 40 km outside Europe**; whole cities share one value. Attribution to CAMS and Open-Meteo required |
| Air quality ground truth | WAQI (token) and OpenAQ v3 (key); CPCB via data.gov.in as optional | Used for a bias-correction factor |
| Routing | OSRM public `/table` (car); ORS optional fallback | Fair-use only; cache |
| Daylight | Open-Meteo daily sunrise and sunset; `astral` library offline fallback | Golden hour derived |
| Elevation (hiking) | Open-Meteo elevation | Optional |
| Traffic | Time-of-day multiplier by road class (documented proxy) | Optional later: TomTom or HERE free tier, only after verifying current terms |
| Hosting | **OPEN**: see Section 13 | Docker Spaces on HF appear to need a paid plan |

## 6. System architecture

```
Question
  -> [SLM call 1] Intent parser -> strict JSON
  -> Gate: clarify / out-of-scope / proceed
  -> Geocode -> venue shortlist (Overpass) -> hazards and mitigators (Overpass)
  -> Live data in parallel: weather, air quality (+ station bias), routing, daylight, elevation
  -> Geo-exposure engine: score every (venue x hour), hard constraints first
  -> Shortlist: top K slots, alternatives, avoid windows, evidence bundle (IDs E1..En)
  -> [SLM call 2] Chooser and explainer: picks among shortlisted slots, writes why and advice, cites IDs
  -> Verifier: all numbers, places, times must exist in the bundle; regenerate once; else template fallback
  -> Response: text + JSON + GeoJSON + sources + limitations
```

**Design principle.** The SLM never computes geometry or recalls facts. It receives a small, already-scored table and returns structured choices and explanations. This keeps the model's work real (it selects and justifies) while making hallucination detectable.

## 7. Functional requirements

Priority: P0 = must ship, P1 = should ship, P2 = stretch.

### FR-1 Intent parsing (P0)

The parser returns strict JSON validated against a Pydantic schema, produced with grammar-constrained decoding at low temperature.

Fields: `activity` (enum of supported activities or `other`), `location_text`, `radius_km`, `time_window` (raw text), `profile` (list from asthma, senior, child, pregnancy, other), `intent_type` (recommend, compare\_options, safety\_check, event\_planning), `missing` (list of required fields not found).

Acceptance: handles typos, Hinglish phrasing, and relative times; never fabricates a location that is not in the text.

### FR-2 Gate: clarify, decline, or proceed (P0)

- Missing location and no `lat`/`lon` supplied: return `status: needs_clarification` with one specific question. Do not guess.
- Out of scope (non-outdoor, unsupported request): `status: out_of_scope` with a short explanation and what we can help with.
- Ambiguous geocode (multiple matches): return candidates and ask, or proceed with the top match and state the assumption if confidence is high.
- Vague time: resolve in Asia/Kolkata to explicit dates and hours, record under `assumptions` (for example "this weekend" means Sat and Sun, 06:00 to 21:00).
- Time beyond the forecast horizon: clip to what data covers and say so.

### FR-3 Geocoding (P0)

Nominatim first, Photon and Open-Meteo as fallbacks, with a global 1 req/s limiter, a descriptive User-Agent, and a 30-day cache.

### FR-4 Venue discovery (P0)

Overpass query by activity tags within the radius (capped by activity), using `out center tags`. Pre-score by tags (surface, lit, sport, size) and shortlist 8 to 12 venues. Examples: `leisure=pitch|track|golf_course|park|nature_reserve`, `sport=*`, `lit=yes`; tennis, cricket, football, running tracks and parks as appropriate; hiking uses `natural=peak`, `route=hiking`, `highway=path|footway` clusters; photography uses viewpoints, waterfronts, parks, and ridges with open sunset sightlines. If fewer than 3 venues are found, say so and widen the radius once with a stated assumption.

### FR-5 Live data fetch (P0)

Weather and air quality for all shortlisted venues in one multi-coordinate call each. Routing from the origin to all venues in one OSRM `/table` call. Per-source timeouts, 2 retries with backoff, a circuit breaker, and TTL caches. Partial failure degrades with a limitation note; it never crashes the request.

### FR-6 Geo-exposure engine (P0)

Deterministic, unit-tested, no SLM involvement. Detailed in Section 8.

### FR-7 Venue-by-hour scoring (P0)

Score every venue for every hour in the window. Output a ranked list of slots, alternatives, and avoid windows. Hard constraints disqualify a slot before soft scoring.

### FR-8 Travel penalty (P0)

OSRM car travel time multiplied by a rush-hour factor, weighted by activity duration. Short activities penalise long trips more.

### FR-9 Recommendation (P0)

The SLM chooses among the top K slots supplied by the engine (K about 5) and justifies the choice. The engine's ranking is the default; the SLM may reorder only within a small score margin and must say why. Final output includes top place, time, score, why, alternatives, avoid windows.

### FR-10 Practical advice (P0)

Carry list, watch-for list, and when-to-avoid, generated from structured facts (for example UV above threshold leads to sunscreen, PM2.5 elevated and asthma leads to inhaler and mask guidance). Advice items must be tied to evidence IDs or to the activity and profile config.

### FR-11 Grounding and verification (P0)

- Every fact carries an evidence ID with source, fetched\_at, and value.
- The verifier extracts numbers, times, place names, and units from the model text and checks each against the bundle (with tolerance for rounding).
- On failure: regenerate once with the offending claims listed; on a second failure, use a deterministic template built from the same data.
- Response includes `data_as_of` timestamps and the AQ station used for bias correction.

### FR-12 Output formats (P0 for JSON and text, P1 for GeoJSON, P2 for UI)

Plain-text summary, structured JSON, and GeoJSON of candidates and hazard sources (so a map can render them). A minimal static map page (Leaflet with OSM tiles, attribution shown) and an hourly score strip are stretch goals.

### FR-13 Activities (P0)

Support the ten reference activities with YAML configs (Section 8). Additional activities (for example yoga in the park, kite flying) only after all ten pass tests.

### FR-14 Observability (P1)

Request ID, per-stage timings, per-source status, and cache hits in logs and in a `debug` field when requested.

## 8. Geo-exposure engine specification

All thresholds below are **design defaults to be tuned and documented in the README**; they are heuristics, not clinical guidance. Verify AQI breakpoints against the current CPCB table before the demo.

### 8.1 Per-slot inputs

For venue v and hour t: apparent temperature, humidity, wind speed, wind direction (from), gusts, precipitation probability and amount, cloud cover, UV, visibility, CAPE or thunderstorm weather code, PM2.5, PM10, NO2, O3, boundary layer height (if available), daylight state, travel time.

### 8.2 Downwind exposure from hazard sources

For each hazard h near v (roads by class, industrial land use, power plants, landfills if tagged):

- Bearing from h to v is theta.
- Wind blows toward phi = (wind\_from + 180) mod 360.
- alignment = max(0, cos(theta minus phi)).
- decay = exp(minus d / L), where d is distance and L is a class length scale (motorway and trunk about 800 m, primary about 400 m, industrial about 2 km; tunable).
- calm\_factor = 1 + k \* (1 minus min(wind\_speed / 5 m/s, 1)); calm air pools pollution. A low boundary layer raises it further when data is present.
- traffic\_multiplier(t) by road class and hour (rush hours up, night down; a documented proxy).
- Exposure E(v,t) = sum over h of class\_weight \* decay \* alignment \* calm\_factor \* traffic\_multiplier.

Mitigators: water bodies and green cover within about 300 m reduce E by up to 15% (tunable). Cap E to a bounded range so one bad road cannot dominate.

### 8.3 Local air quality estimate

PM2.5\_local = PM2.5\_forecast \* bias\_factor \* (1 + beta \* E), where bias\_factor = observed\_now / forecast\_now from the nearest station, clamped to 0.5 to 2.0 and reported in the response. If no station is within range, bias\_factor = 1 and a limitation is added. The forecast grid is coarse outside Europe, so cross-venue differences come mainly from E, not from the API.

### 8.4 Hard constraints (disqualify the slot)

- Thunderstorm risk or lightning indicators (all activities; golf and hiking strictest).
- Precipitation probability above the activity limit, or heavy rain.
- Dark with no lighting evidence (daylight-only activities; photography handles low light separately).
- Apparent temperature above the limit for the profile.
- PM2.5 above the profile and ventilation limit.
- Travel time beyond a cap for the requested radius.

Default limits (tunable): heat limit 38 C apparent for exertion, 34 C for senior, child, pregnancy. PM2.5 (CPCB-style bands, verify): default high-ventilation hard limit above 90 ug/m3; sensitive profiles (asthma, senior, child, pregnancy) above 60 ug/m3. Precipitation probability limits: 60% for sports, 40% for picnic and events.

### 8.5 Soft score (0 to 100)

Weighted sum of comfort (temperature and humidity fit), air (local PM2.5, O3, scaled by ventilation weight), wind and rain fit, daylight fit, and travel penalty. Weights are per activity in YAML. The breakdown is returned so the explanation can cite it.

### 8.6 Activity profiles (YAML, defaults)

| Activity | Comfort band (apparent C) | Wind sensitivity | Rain tolerance | Ventilation weight | Daylight and venue notes |
| --- | --- | --- | --- | --- | --- |
| Running or jogging | 12 to 24 | Medium | Low | 3.0 | Daylight or lit track; avoid downwind of busy roads |
| Cycling (leisure or commute) | 14 to 28 | High (gusts, headwind) | Low | 2.5 | Daylight or lights; commute adds road exposure |
| Golf | 15 to 30 | Very high | Low; lightning hard stop | 1.2 | Daylight only; large radius |
| Cricket | 18 to 32 | Medium | Very low | 2.0 | Daylight or floodlit |
| Football or five-a-side | 15 to 28 | Low to medium | Low | 2.5 | Daylight or lit pitch; surface tag matters |
| Outdoor tennis | 15 to 30 | High | Very low | 1.8 | Wind degrades play; lit courts after dark |
| Hiking or nature walk | 12 to 28 | Medium | Low | 1.8 | Needs sunset buffer; elevation and trail tags |
| Picnic or park outing | 18 to 30 | Low to medium | Very low | 1.0 | Shade, water, green cover bonus |
| Outdoor event or gathering | 20 to 32 | Medium (tents, decor) | Very low | 1.0 | Rain plan, road access, crowd timing |
| Outdoor photography | Wide | Low | Medium | 1.0 | Golden and blue hour, cloud 20 to 60%, visibility and haze, sunset direction |

### 8.7 Personal profile multipliers (config)

- **Asthma:** PM2.5 and O3 limits tightened, exposure term weighted up, advice includes inhaler and pre-activity checks.
- **Senior and pregnancy:** heat limit lowered, AQI limit tightened, shorter duration and shade preference.
- **Child:** AQI and UV limits tightened, hydration and shade advice, avoid peak heat.
- Every profile adjustment is echoed in the response ("limits tightened because: asthma").

## 9. Model usage specification

| Call | Input | Output | Decoding |
| --- | --- | --- | --- |
| Parser | User query plus today's date and timezone | Intent JSON (FR-1) | JSON-schema grammar, temperature about 0.1, non-thinking |
| Chooser and explainer | Compact table of top K slots with scores and breakdown, profile, evidence IDs | JSON: chosen slot ID, reasons (each citing evidence IDs), carry list, watch-for list, avoid summary | JSON-schema grammar, low temperature, non-thinking |
| Verifier | Model output and evidence bundle | Pass or list of violations | Pure code (regex and lookup), no model |

Rules:

- The model is told it may only use values from the provided table and must cite IDs.
- Prompts stay short (small context, CPU latency).
- Qwen3.5-2B and Qwen2.5-1.5B are evaluated in the bake-off; the winner is chosen by the metrics in Section 11.
- **Fine-tuning (P2):** an optional LoRA for the parser only, trained on queries generated programmatically from templates (no large hosted model, no copying of the rulebook examples). Ship the unmodified base first.

## 10. API specification

### Endpoints

- `POST /ask`: main endpoint.
- `GET /health`: liveness plus model-loaded and per-source status.
- `GET /context?q=...`: debug; returns the evidence bundle without the SLM.
- `GET /sources`: list of sources, versions, and attributions.

### Request

```json
{ "query": "string", "lat": null, "lon": null, "debug": false }
```

### Response

```json
{
  "status": "ok | needs_clarification | out_of_scope | partial",
  "clarification_question": null,
  "parsed": {"activity": "", "location": "", "radius_km": 10, "window": {"start": "", "end": ""}, "profile": [], "assumptions": []},
  "recommendation": {"place": "", "time": "", "score": 0, "why": "", "evidence": []},
  "alternatives": [],
  "avoid": [{"window": "", "reason": "", "evidence": []}],
  "advice": {"carry": [], "watch_for": []},
  "evidence": {"E1": {"source": "", "fetched_at": "", "value": ""}},
  "map_geojson": {},
  "data_as_of": "",
  "sources": [],
  "limitations": [],
  "timings_ms": {}
}
```

Attribution strings ("© OpenStreetMap contributors", CAMS and Open-Meteo) are always present in `sources`.

## 11. Evaluation plan

A mini harness (`scripts/eval.py`) runs about 40 queries we write ourselves (not copies of the rulebook examples and not hard-coded) across: all ten activities, three or more cities, each profile, missing location, vague time, typos, Hinglish, out-of-scope, large and small radii.

| Metric | How measured |
| --- | --- |
| Parse accuracy | Compare parsed fields to hand labels |
| JSON validity and schema pass rate | Automatic |
| Groundedness | Share of numeric, time, and place claims in the output found in the evidence bundle; target 100% after verifier |
| Place validity | Every named place exists in the Overpass result |
| Gate correctness | Clarify or decline cases handled correctly |
| Personalization delta | Same query with and without a profile differs in slot or advice |
| Place x time sensitivity | Rotating wind direction in a fixture changes the ranking as expected (unit test) |
| Latency | p50 and p95, cold and warm |
| Model bake-off | Parse accuracy, tokens per second on the target host, explainer faithfulness before verifier |

Recorded API responses are used as **unit-test fixtures only**; the runtime always fetches live.

## 12. Non-functional requirements

- **Latency:** p50 under 20 s and p95 under 45 s warm; hard overall timeout 60 s with partial results.
- **Reliability:** per-source timeouts, retries, circuit breakers, mirror failover, graceful degradation, template fallback if the model fails.
- **Politeness:** descriptive User-Agent; Nominatim at 1 req/s; Overpass concurrency of 1 to 2; OSRM demo server used sparingly; caches with TTLs (geocode 30 days, venues and hazards 24 h, forecast 30 min, station observations 15 min, routing 15 min).
- **Concurrency:** single model worker with a request queue; reject with a friendly 429 when the queue is long.
- **Secrets:** API keys in environment variables, never committed.
- **Privacy:** no storage of user queries beyond logs needed for debugging; state this in the README.
- **Safety:** health advice is generic and conservative; "not medical advice" note on every profile-adjusted response.

## 13. Hosting plan (OPEN item, decide by end of Day 1)

Hugging Face's current docs say Docker and Gradio Spaces need a paid plan to create (PRO for personal accounts), with Static Spaces free and up to two Gradio Spaces on ZeroGPU free. CPU Basic is 2 vCPU and 16 GB RAM; free hardware sleeps when idle; outbound traffic is limited to ports 80, 443, and 8080 (fine for our APIs).

| Option | Status | Notes |
| --- | --- | --- |
| 1. HF PRO Docker Space on CPU Basic | **Preferred** | Simplest; add a keep-alive ping or set sleep time; confirm price |
| 2. Free ZeroGPU Gradio Space with FastAPI routes | Unverified | Test whether a custom `/ask` route and queue behaviour work |
| 3. Oracle Always Free ARM VM | Possible | Reported at 2 OCPU and 12 GB after a recent cut; signup friction and capacity shortages |
| 4. Own machine plus tunnel | Last resort | Must stay online through judging |

Action: whoever has a PRO account (or can subscribe) creates the Space on Day 1 and runs the smoke test from inside it. Confirm with the organisers if any doubt exists about paid hosting; the rulebook bans paid data and APIs, and does not mention hosting.

## 14. Deliverables checklist

| Deliverable | Owner | Done when |
| --- | --- | --- |
| Live API endpoint |  | `/ask` answers fresh queries from outside our network |
| HF model repo (public) |  | GGUF Q4\_K\_M weights, model card (upstream model, changes, intended use, limitations), Apache 2.0, loads with `Llama.from_pretrained` |
| Code repository |  | Full source, tests, eval harness, configs |
| README |  | How to run, base model, data sources, API URL, disclosures, limitations |
| Slides (4 to 6) |  | Problem and approach, architecture, worked example, model and fine-tuning, honest limitations |
| Live demo script |  | Two demos: wind-shift example and asthma-versus-no-profile |
| Google Form submission |  | Submitted before the deadline (form link pending from organisers) |

## 15. Attribution and disclosure

- "© OpenStreetMap contributors" on every response and in the README.
- Open-Meteo attribution and the required credit to CAMS ENSEMBLE data.
- List every source with its role, plus rate-limit handling.
- Disclose the model (exact name and size), any fine-tuning, and that AI coding assistants (Claude) were used to write code.
- Library licences listed in the README.

## 16. Schedule

Today is 21 Oct. The deadline is 24 Oct at 11:59 PM IST; treat **24 Oct 6 PM** as our real deadline to leave a buffer.

| Day | Goals | Exit criteria |
| --- | --- | --- |
| **Day 1 (21 Oct)** | Register keys (WAQI, OpenAQ, data.gov.in, optional ORS); smoke test all endpoints; repo skeleton; shared infrastructure (SourceResult envelope, cache, retry, circuit breaker, rate limiters); clients for weather, geocode, air quality, Overpass, routing, daylight; `gather_context()`; hosting decision and trial deploy | `GET /context` returns real data for a messy location, deployed from the chosen host |
| **Day 2 (22 Oct)** | Exposure engine and activity and profile YAMLs with unit tests; model bake-off (Qwen3-1.7B vs Qwen3.5-2B, optional Sarvam-1 parser-only test); parser with schema; gate; explainer; verifier; `/ask` end to end; deploy | `/ask` works on 10 varied queries on the live host |
| **Day 3 (23 Oct)** | Eval harness and fixes; HF model repo and card; GeoJSON and optional map page; README; slides; load and failure testing (kill a source, confirm degradation) | All deliverables drafted; eval report produced |
| **Day 4 (24 Oct)** | Fresh-query test on the live endpoint; final fixes only; submit by 6 PM IST | Form submitted |

**Cut order if time slips:** LoRA, then map UI, then activities beyond ten, then GeoJSON, then station bias correction (keep as a limitation note). Never cut: verifier, gate, README, model repo, endpoint stability.

## 17. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Hosting needs a paid plan or egress is blocked | No live endpoint | Decide Section 13 on Day 1; run the smoke test from the host; keep fallbacks |
| Overpass slow or rate-limited | Latency and failures | Mirror failover, tight timeouts, capped radii, 24 h caches |
| Nominatim throttling | Geocode failures | 1 req/s limiter, cache, Photon fallback |
| Coarse AQ grid outside Europe | Weak place differences | Station bias correction plus exposure engine; state the limitation |
| SLM latency on 2 vCPU | Slow demo | Short prompts, constrained decoding, warm model at startup, measure early; consider Qwen2.5-1.5B fallback |
| SLM invents facts | Rule violation | Compact evidence table, verifier, template fallback |
| OSM gaps in smaller towns | Few or no venues | Say so; widen radius once; ask the user to broaden |
| Model count or licence challenged | Disqualification | Use Qwen3-1.7B (about 2.03B); state exact count and licence in the model card |
| Forecast uncertainty | Overconfident advice | Optional multi-model spread via Open-Meteo `models=`; show confidence |
| Public API fair-use limits during judging | Intermittent failures | Caches, backoff, circuit breakers, partial results |

## 18. Open items

1. Hosting path (Section 13): PRO account availability.
2. Team size and roles (announced later). Suggested split if three people: A for data layer and infrastructure, B for exposure engine and evaluation, C for model, API, HF repo, and slides.
3. Submission form link (pending from organisers).
4. Verify the Qwen3-1.7B licence tag, exact parameter count, and CPCB breakpoints before they go into the README.
5. Bake-off result: confirm or replace the primary model.

## Appendix A. Repository layout

```
geoai/
  app/
    main.py            # FastAPI: /ask, /health, /context, /sources
    config.py          # keys, TTLs, mirrors, User-Agent
    sources/           # base.py, geocode.py, overpass.py, weather.py, airquality.py, routing.py, daylight.py, elevation.py
    context.py         # gather_context() orchestrator
    engine/            # exposure.py, scoring.py, constraints.py, timeparse.py
    llm/               # serve.py, parser.py, explainer.py, schemas.py, prompts.py
    verify.py          # evidence verifier and template fallback
  configs/             # activities.yaml, profiles.yaml, hazards.yaml
  tests/               # unit tests plus recorded fixtures (tests only)
  scripts/             # smoke_test.py, model_bakeoff.py, eval.py
  Dockerfile
  README.md
```

## Appendix B. Worked example (for the demo)

Query: a morning run near a neighbourhood that borders an expressway, with a westerly morning wind.

1. Parser extracts running, the neighbourhood, a default radius (assumption stated), tomorrow morning and evening, no profile.
2. Engine finds 10 venues; at 7 to 9 AM the venue east of the expressway has high exposure (wind carries traffic emissions toward it, rush-hour multiplier on); the same venue scores well at 6 PM when the wind has shifted and traffic has eased.
3. A park with water nearby but farther away scores lower on travel but wins on air; the explainer states the trade-off with evidence IDs.
4. The same query with asthma added tightens PM2.5 limits, drops the downwind venue's morning slots into the avoid list, and changes the top recommendation.
5. The verifier confirms every number and place name appears in the evidence bundle; the response lists data timestamps, the AQ station used, and limitations (no live traffic, coarse AQ grid, OSM coverage).
