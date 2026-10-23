# GeoAI Outdoor Assistant

An AI assistant, powered by a small language model, that tells a person
**where and when to go outdoors** and what to take, what to watch out for and
when to stay home.

**Live API:** <https://85-211-194-173.sslip.io> — interactive docs at
<https://85-211-194-173.sslip.io/docs>.

```
"I have mild asthma, where's good for a morning walk near JP Nagar tomorrow?"

Go to B R Ambedkar Park around 10:00 on Fri 23 Oct.
Preparation: Carry a mask as air quality is moderate.
- feels like 28C with 16% chance of rain
- US-AQI 42 locally
- local exposure index 1.04
- about 11 min away
Carry: Water bottle, Your inhaler.
Watch out for: Limits were tightened because of: asthma.

Data provided by © OpenStreetMap contributors and Open-Meteo.
```

Every number in that answer came from a live API call, and every one of them
is traceable in the response's `proof` block.

This is a submission for the **GeoAI Hackathon at CSRE, IIT Bombay**
(deadline 24 October 2026, 11:59 PM IST). The rulebook the submission is
judged against is the one the organisers published with the problem
statement; the last section of this file maps it point by point.


---

## The problem, and the idea

Weather apps tell you the weather. They do not tell you **which nearby place,
at which hour, for this particular person** is the right answer. The rulebook's
own example:

> A ground that lies downwind of an expressway at 8 AM may be pleasant by
> evening once the wind shifts, and a venue forty minutes away at rush hour
> may not be worth the trip.

So the assistant reasons over three things at once: **place**, **hour** and
**person**.

### The division of labour

This is the core design decision, and everything else follows from it.

| | Who | What |
| --- | --- | --- |
| Understand the question | the model | "running", "JP Nagar", "morning", "asthma" |
| Find the facts | live APIs | venues, roads, hourly weather, air quality, travel times, sunrise |
| **Do the reasoning** | **deterministic code** | which venue, which hour, and why |
| Write it up | the model | one sentence |
| Check it | deterministic code | every number, time, place name and citation |

The model never computes anything and never recalls a fact. It receives an
already-scored table of options and may only pick from it. That is deliberate:
a 1.7B model asked to reason about wind and traffic will eventually state a
number that was never fetched, which the rulebook forbids. A rule-based
verifier re-checks the model's wording against the data that was fetched, and
if anything fails the answer is produced by code instead.

The result is that the model contributes exactly the two things a small model
is good at, and the two things it is bad at are handled by code.

---

## Architecture

```
question
  |
  |  [1] SLM call 1 - intent parser, grammar-constrained JSON
  |      { activity, location, day, part_of_day, profile }
  |      the question's own wording overrides the model for the
  |      activity, the place and the profile
  |
  |  [2] gate - code
  |      no location -> ask one question.  out of scope -> decline.
  |
  |  [3] live data - six APIs, in parallel
  |      Nominatim / Open-Meteo   -> where is it, and when
  |      Overpass                 -> the venues, and the roads,
  |                                  industry, water and green cover
  |      Open-Meteo forecast      -> hourly weather, per venue
  |      Open-Meteo CAMS          -> hourly air quality, per venue
  |      OSRM                     -> how far every venue is
  |      Open-Meteo daily         -> sunrise and sunset
  |
  |  [4] exposure engine - deterministic, no model
  |      every venue against every hour of the window:
  |      downwind exposure, local PM2.5, hard limits, a 0-100 score
  |
  |  [5] SLM call 2 - chooser
  |      input : the top three options as a small table
  |      output: { slot_id, headline }
  |
  |  [6] verifier - code
  |      every number, clock time, place name and evidence citation
  |      in the model's sentence is checked against the fetched data
  |
  +--> answer + proof + GeoJSON + evidence + limitations
```

```
app/
  main.py             FastAPI: /ask, /live, /ready, /health, /sources, /context
  config.py           settings, TTLs, mirrors, per-source User-Agents
  planner.py          "tomorrow evening" -> concrete hours in IST
  slm.py              the local model: grammar-constrained JSON, thinking off
  context.py          gather_context(): live data, evidence, degradation
  exposure_engine.py  deterministic scoring of every venue against every hour
  verifier.py         grounding checks and the fact table
  pipeline.py         orchestration and the response contract
  sources/            geocode, overpass, weather, airquality, routing,
                      daylight, elevation, geofabrik
  configs/            activities, profiles, hazards, weights
tests/                126 unit tests
scripts/              eval_queries.py, check_grammar.py, build_wheel.sh
docs/                 API.md for the frontend, RUNBOOK.md, PRD.md, TODO.md
HANDOFF.md            live deployment, VM access and operational commands
hf_repo/              the model card, the prompt contract, the upload script
```

---

## The exposure engine

This is where "place and hour together" actually happens. For a venue `v` at
an hour `t`:

```
E(v,t) = sum over nearby features h of
           class_weight(h) * exp(-distance(v,h) / length_scale(h))   decay
         * max(0, cos(bearing(h->v) - wind_towards(t)))              alignment
         * calm_factor(t)                                            calm air pools
         * traffic_multiplier(h, t)                                  hour of the day

PM2.5_local(v,t) = PM2.5_forecast(t) * station_bias * (1 + beta * E(v,t))
```

* `wind_towards(t)` is the live wind direction for that hour, turned around.
  A venue only picks up the exposure of a road that is **downwind** of it.
* Roads decay over 400 to 800 m depending on their class, industry over 2 km.
* Water and green cover within a few hundred metres **reduce** the term.
* `station_bias` corrects the coarse forecast grid with a nearby monitoring
  station, when one is configured.

The score that comes out of that is a weighted sum of comfort, air quality,
wind and rain, daylight and travel time, with a penalty for the residual
exposure. Each part is returned in the breakdown, so the explanation can
quote it.

**Hard limits** disqualify a slot outright: thunderstorm, rain above the
activity's limit, apparent heat above the person's limit, PM2.5 or ozone above
the limit, darkness in a venue with no lighting, and a trip that is too long
for the radius that was asked for.

**Personal profiles** tighten those limits: asthma, senior, child, pregnancy,
and injury or recovery. An injury is different on purpose: it does not only
tighten the limits, it rules the activity out, and the answer says plainly
that cycling is not advisable with a fractured foot, points at the doctor's
advice, and only then names the calmest nearby spot for when the person is
cleared.

This is a unit test, not a claim. `test_wind_direction_changes_the_ranking`
scores the same venue with an easterly and a westerly wind and asserts the
ranking flips.

---

## Quick start

### Locally

```bash
cd ~/Projects/GEOai/deepesh-repo

# a Python 3.12 environment. The llama-cpp-python wheels published for
# linux are musl builds and cannot be loaded on glibc, so it is compiled.
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install cmake ninja
CMAKE_ARGS="-DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_F16C=ON \
           -DGGML_FMA=ON -DLLAMA_CURL=OFF" \
    pip install --no-binary llama-cpp-python "llama-cpp-python==0.3.19"
uv pip install -e ".[dev]"

# the model, 1.1 GB
mkdir -p models
curl -L -o models/Qwen3-1.7B-Q4_K_M.gguf \
  "https://huggingface.co/unsloth/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf"

uvicorn app.main:app --host 0.0.0.0 --port 7860
```

`GGML_NATIVE=OFF` is deliberate. A build compiled for the build machine's CPU
dies with an illegal instruction on any other one, so the image uses a
baseline instruction set instead.

The model is loaded at start-up, not on the first request, so the first
caller of the judging window does not pay for it.

### With Docker

```bash
docker compose up -d --build
curl -s localhost:7860/ready | python3 -m json.tool
curl -s -X POST localhost:7860/ask -H 'Content-Type: application/json' \
  -d '{"question":"Where can I go running tomorrow evening near Koregaon Park?"}'
```

The image is built in two stages, so the compilers do not ship with the
service. The model weights are baked in, so the container serves answers
without needing the network to boot. It runs as a non-root user and waits for
the in-flight requests before it stops.

If the package mirrors are slow, build the wheel on the host first and the
image will use it:

```bash
./scripts/build_wheel.sh
docker compose up -d --build
```

---

## The API

**Live base URL:** `https://85-211-194-173.sslip.io`  
**Interactive docs:** `https://85-211-194-173.sslip.io/docs`

Full details, with a request, a response and a JavaScript example, are in
[`docs/API.md`](docs/API.md).

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/ask` | the main endpoint: a question in, a grounded answer out |
| GET | `/live` | liveness, always 200 while the process is up |
| GET | `/ready` | readiness, 503 when the model is not loaded |
| GET | `/health` | both of the above |
| GET | `/sources` | every data source, its role and its licence |
| GET | `/context` | the raw evidence bundle, for debugging |

The response carries the answer, the parsed intent, the recommendation with
its score breakdown, alternatives, the hours to avoid with the reason for
each, the practical advice, the evidence, a `proof` block, a GeoJSON feature
collection for the map, the data timestamp, the sources and the limitations.

`status` is one of `ok`, `partial`, `needs_clarification`, `out_of_scope` or
`busy`. The frontend should show a progress state: a question fetches live
data from six services, scores every venue against every hour and then writes
the answer, so it takes 20 to 40 seconds on a laptop and **60 to 90 seconds**
on the 2 vCPU deployment. Use a **120-second** client timeout and retry on
`status: "busy"`.

---

## Configuration

Everything is an environment variable and every one has a working default.
Copy `.env.example` to `.env` to set them.

| Variable | Default | What it does |
| --- | --- | --- |
| `WAQI_TOKEN` | empty | a nearby air-quality station for the grid correction. Without it the answer says so |
| `CONTACT_EMAIL` | empty | goes in the OSM User-Agent. Do not use a placeholder domain: Overpass refuses one |
| `SLM_MODEL_PATH` | `models/Qwen3-1.7B-Q4_K_M.gguf` | the weights to serve |
| `SLM_N_THREADS` | 4 | the threads the model uses. Set it to the cores you have |
| `SLM_N_CTX` | 4096 | the context window |
| `MAX_IN_FLIGHT` | 2 | how many questions may be in flight. The model is the bottleneck |
| `REQUEST_DEADLINE_S` | 90 | the ceiling for a whole request |
| `CONTEXT_DEADLINE_S` | 30 | the ceiling for the live data stage |
| `GEOFABRIK_ENABLED` | false | the local OpenStreetMap extract source. Off by default, see the limitations |
| `LOG_LEVEL` | INFO | the log level |

---

## Data sources

Every source is free and open, and everything is fetched live at query time.
Nothing is bundled and no city data is pre-baked.

| Source | Used for | Licence |
| --- | --- | --- |
| OpenStreetMap, via the Overpass API and Nominatim | venues, roads, industry, water, green cover, geocoding | © OpenStreetMap contributors, ODbL |
| Open-Meteo forecast | hourly weather | CC BY 4.0 |
| Open-Meteo air quality, CAMS ENSEMBLE | PM2.5, PM10, ozone, NO2, US-AQI | CC BY 4.0 |
| WAQI | a nearby station reading, to correct the grid | CC BY 4.0 |
| OSRM | travel time | BSD, the demo server is fair use |
| Astral and Open-Meteo daily | sunrise and sunset | MIT and CC BY 4.0 |

**© OpenStreetMap contributors** appears in every response, in `/sources` and
in this file, as the rulebook requires.

---

## The model

| | |
| --- | --- |
| Base model | [`Qwen/Qwen3-1.7B`](https://huggingface.co/Qwen/Qwen3-1.7B) |
| Licence | Apache-2.0 |
| Parameters | 2.03B including embeddings, 1.72B without. Under the 3B limit |
| Published as | [`som-doe2007/geoai-outdoor-assistant`](https://huggingface.co/som-doe2007/geoai-outdoor-assistant) |
| Served by | llama.cpp through `llama-cpp-python`, Q4_K_M, CPU, four threads |
| Speed here | 167 tokens per second prefill, 17 per second generation, 5 to 9 under the JSON grammar |
| Fine-tuning | none. The base model is used unchanged, as the rulebook allows |

The repository on the Hub holds the GGUF that the service actually loads,
the prompt contract, the two JSON schemas the model is constrained to, and
the serving configuration, so the contract is inspectable rather than
described:

```python
from llama_cpp import Llama

llm = Llama.from_pretrained(
    "som-doe2007/geoai-outdoor-assistant",
    filename="Qwen3-1.7B-Q4_K_M.gguf",
    n_ctx=4096,
)
```

The unquantized upstream model, `Qwen/Qwen3-1.7B`, loads with the standard
transformers API if you want to inspect or re-quantize it.

### Two details that matter

**The grammar is always active.** The model is asked for JSON and the schema
is compiled to a GBNF grammar, so the output is always parseable. Qwen3 emits
a reasoning block by default, and with thinking enabled the grammar is not
always enforced, which would make the answer unparseable. We disable
thinking by pre-seeding an empty `<think></think>` block in the assistant
turn, which is how the model's own template supports it.
`scripts/check_grammar.py` checks this against the real model, and
`tests/test_prompt_contract.py` checks the contract that makes it work.

**The model is never the source of a fact.** The reasons, the advice and the
avoid windows are composed in code from the fetched data. The model writes
one sentence, and the verifier checks that sentence.

---

## Grounding

The rulebook asks the assistant to support every claim with the live data it
actually used, without inventing weather or places. That is enforced, not
hoped for:

1. The parser's output is **overruled by code** for the activity, the place
   and the profile, because the question is the ground truth for those. A
   model that invents a health condition cannot tighten anyone's limits.
2. The chooser may only pick a `slot_id` from the table it was given.
3. A **verifier** extracts every number, clock time, place name and
   evidence citation from the model's wording and checks each against the
   facts that were fetched, with a tolerance for rounding. A citation to
   evidence that does not exist is rejected too.
4. If anything fails, the model is asked once more. If it fails again, the
   answer is produced by a deterministic template built from the same data,
   and the response says which one it was in `answer_source`.

The test suite contains the case that matters: with the network cut, the
service **cannot answer at all**, because there is nothing to fall back on.
Nothing is pre-baked.

---

## Activities and profiles

All ten activities from the rulebook, each with its own comfort band, wind
sensitivity, rain tolerance, ventilation weight, daylight rules and map
selectors: running, cycling, golf, cricket, football, tennis, hiking, picnic,
outdoor event and outdoor photography.

Five personal profiles: asthma, senior, child, pregnancy and injury or
recovery. Every profile-adjusted answer says which limits were tightened and
carries a note that it is general guidance from open data, not medical
advice.

---

## Tests

```bash
.venv/bin/pytest -q          # 77 passed
.venv/bin/ruff check app tests scripts
.venv/bin/python scripts/eval_queries.py --live    # the live question set
.venv/bin/python scripts/check_grammar.py          # the grammar contract
```

The suite covers the planner, the geocoder, the exposure engine, the
verifier, the API contract and the production behaviour. The engine tests
are the important ones: they assert that the wind changes the ranking, that
the hard limits disqualify a slot, that a profile tightens the limit, and
that a private venue is never recommended.

---

## Known limitations

These are real, and the assistant says the same things in its own responses.

- **The air-quality forecast is coarse.** Outside Europe the CAMS grid is
  roughly 25 to 40 km wide, so every venue in a city usually shares one
  forecast value. The venue-to-venue difference comes from our exposure
  model, not from the API. A nearby station corrects the level, not the
  shape.
- **The exposure model is not calibrated against measurements.** The weights
  are documented engineering defaults, chosen to be conservative. The
  reasoning is explicit and inspectable, but it is a model of pollution, not
  a measurement of it.
- **There is no live traffic feed.** Travel time is OSRM car routing plus a
  documented time-of-day multiplier per road class. It is an approximation
  and it is labelled as one.
- **OpenStreetMap coverage is uneven.** In a small city there may be one
  mapped pitch or none. The assistant says so instead of inventing a place.
- **The public Overpass mirrors are slow and throttled.** They are the
  reason the answer takes 60 to 90 seconds on the 2 vCPU deployment. A
  regional OpenStreetMap extract removes the dependency and the code is there
  (`app/sources/geofabrik.py`), but it needs a download that cannot be
  guaranteed before a demonstration, so it is off by default.
- **The model is small.** It misreads an unusual phrasing now and then.
  The code overrides it wherever the question is clear.
- **A question takes 60 to 90 seconds on the 2 vCPU deployment** (20 to 40
  seconds on a laptop). The live data is the larger part of that; the model is
  10 to 30 seconds.

---

## Disclosure

- **Models.** The only language model used at request time is
  `Qwen/Qwen3-1.7B`, Apache-2.0, run locally through llama.cpp. No
  GPT-4-class, Claude or Gemini model is called anywhere in the running
  assistant.
- **AI coding assistants** were used to write and refactor the code. They
  are not used by the service at request time.
- **Data.** Every source is free and open, and is listed above with its
  licence. No paid or private API is used, and nothing is scraped behind a
  login.
- **Privacy.** User questions are not stored. Only aggregate counters and
  debug logs are kept, and the local cache holds public API responses.

---

## The rulebook, point by point

| Requirement | Where it is met |
| --- | --- |
| Identify the activity, the area or radius, the time range and any personal detail | `app/planner.py`, `app/pipeline.py` |
| Consider several nearby places across the upcoming hours | `app/exposure_engine.py` |
| Account for wind, roads and traffic, industry, water and green cover | `app/exposure_engine.py` and `app/configs/hazards.yaml` |
| A clear recommendation, what to carry, what to watch for, when to avoid | `app/pipeline.py` |
| Every claim backed by the live data, nothing invented | `app/verifier.py` |
| A response that can carry maps and images | `map_geojson` in every response |
| Ten activities | `app/configs/activities.yaml` |
| Profiles that tighten the limits | `app/configs/profiles.yaml` |
| Missing location, vague time and out of scope handled gracefully | `app/pipeline.py`, the gate |
| Live data from free and open sources, disclosed, rate limited, cached | `app/sources/`, the table above |
| A model of at most 3B, open weights, run by us | `Qwen3-1.7B`, 2.03B, llama.cpp |
| The model on Hugging Face, with a card and an open licence | `som-doe2007/geoai-outdoor-assistant` |
| A live API endpoint | `POST https://85-211-194-173.sslip.io/ask` |
| A code repository | this repository |
| A README with how to run, the model, the sources and the endpoint URL | this file |
| A presentation | the team owns the slides |
| © OpenStreetMap contributors | in every response and here |
