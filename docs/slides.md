# GeoAI Outdoor Assistant — 6 slide deck

Format: 6 slides, matching the rulebook's brief (problem and approach,
architecture, a worked example, the model, limitations).
Everything below is copied from the running system, not written for effect.

---

## Slide 1 — The problem, and our approach

**Weather apps tell you the weather. They do not tell you where and when to go.**

- *"Where can I go running tomorrow evening near Koregaon Park?"*
- *"I have mild asthma, where's good for a morning walk near Powai in the next two days?"*

Both need three things at once: **the place**, **the hour**, and **the person**.

**Our approach: the model plans and explains, the tools compute the facts, a verifier guarantees nothing is invented.**

- A deterministic engine scores every (venue, hour) pair against live weather, air quality, roads, industry, water and green cover.
- A 1.7B open-weights model, running locally, reads the question and picks and explains from that scored table.
- A rule-based verifier re-checks every number, time, place name and citation in the model's answer against the data actually fetched.

The same venue can be a bad idea at 8 AM and a good one at 6 PM, because the wind and the traffic both change. That is the part a weather app cannot do.

---

## Slide 2 — Architecture

```
question
  -> [SLM 1] intent parser ........ grammar-constrained JSON
  -> gate ......................... clarify / decline / proceed
  -> geocode ...................... Nominatim -> Open-Meteo -> Photon
  -> venues ....................... Overpass (tags) + Nominatim (POI), merged
  -> hazards ...................... Overpass: roads, industry, water, green
  -> live data .................... Open-Meteo weather + air quality, OSRM, daylight
  -> exposure engine .............. scores every venue x hour
  -> [SLM 2] chooser ............... picks a slot, writes why / carry / watch-for
  -> verifier ..................... every claim must exist in the bundle
  -> response ..................... text + JSON + GeoJSON + evidence + limitations
```

- **Every source is free and open**, fetched live at query time. Nothing is bundled, nothing is pre-baked.
- The model is loaded **at start-up**, not on the first request, so the first caller of the judging window does not pay for it.
- A failing source degrades the answer with a limitation note. It never fails the request.

---

## Slide 3 — The engine: reasoning over place and time

For each venue and each hour, the exposure term is:

```
E(v,t) = sum over nearby hazard features h of
           class_weight(h) * exp(-distance(v,h) / length_scale(h))   spatial decay
         * max(0, cos(bearing(h->v) - wind_towards(t)))              downwind alignment
         * calm_factor(t)                                            calm air pools pollution
         * traffic_multiplier(h, t)                                  time-of-day proxy

PM2.5_local(v,t) = PM2.5_forecast(t) * station_bias * (1 + beta * E(v,t))
```

Water bodies and green cover within a few hundred metres reduce the term. The
wind direction and speed are the **live values for that hour**, so the same
venue scores differently at different times.

Hard constraints disqualify a slot outright: thunderstorm, rain above the
activity's limit, apparent heat above the profile's limit, PM2.5 above the
profile's limit, darkness in an unlit venue, and travel time over the cap.

**Profiles tighten the limits, not just the wording.** Asthma: PM2.5 limit 90 -> 60 ug/m3, exposure weighted 1.6x, and the advice adds an inhaler. Senior, child and pregnancy behave the same way.

This is a unit test, not a claim: `test_wind_direction_changes_the_ranking` scores the same venue with an easterly and a westerly wind and asserts the ranking flips.

---

## Slide 4 — A worked example (real output)

**Question:** *"I have mild asthma, where's a good place for a morning walk near JP Nagar 5th Phase in Bengaluru tomorrow?"*

```
Go to Anuroopa Park around 09:00 on Thu 22 Oct (score 92/100).
- E_WEATHER: feels like 26C with 12% chance of rain
- E_AQ: US-AQI 43 locally
- E_ROUTES: about 9 min away
Carry: Water bottle, Light layer for the weather, Inhaler.
Watch out for: Limits were tightened because of: asthma.
```

**The proof, factor by factor:**

```
weather        feels like 26C, 12% chance of rain, wind 9.5 km/h from E   [Open-Meteo / E_WEATHER]
air quality    US-AQI 43, local PM2.5 estimated 8 ug/m3, bias x1.0         [Open-Meteo CAMS / E_AQ]
local exposure index 0.00 (no busy roads downwind)                        [OpenStreetMap / E_HAZARDS]
travel         about 9 min by car, 6.6 km                                   [OSRM / E_ROUTES]
daylight       sunrise 06:10 / sunset 17:57 IST                            [Open-Meteo / E_DAYLIGHT]
scoring        comfort 84.8  air 100.0  wind_rain 85.1  daylight 92.0  travel 93.7
```

**When the weather is against you, it says so and still helps:**

```
Nothing is ideal in that window. The best of the options is Football Turf around
17:00 on Thu 22 Oct (score 79/100), but it still has problems: 100% chance of rain.
- avoid this because of 100% chance of rain
- avoid this because of 86% chance of rain
```

---

## Slide 5 — The model, and how it is kept honest

| | |
|---|---|
| Model | **Qwen3-1.7B** (`Qwen/Qwen3-1.7B`), 2.03B params, Apache-2.0 |
| Under the 3B cap | yes, 2.03B including embeddings |
| Served by | llama.cpp (`llama-cpp-python` 0.3.19), Q4_K_M, CPU, 4 threads |
| Speed here | 167 tok/s prompt, 17 tok/s generation, 5-9 tok/s under a JSON grammar |
| Fine-tuning | **none** — the base model is used unchanged, as the rulebook allows |

**Two calls, nothing else.** The parser extracts activity, place, day, part of day and profile. The chooser picks a slot and writes the reason.

**Three things keep it honest:**
1. **Grammar-constrained decoding** — the JSON schema is compiled to a GBNF grammar, so the output is always structurally valid.
2. **A rule-based verifier** — every number, clock time, place name and evidence citation in the model's wording is checked against the fetched bundle. A failure triggers one regeneration, then a deterministic template built from the same data.
3. **The user's wording wins** — the calendar, the activity and the profile match are all computed in code from the question itself, so when the model misreads "in the next two days" as "next weekend", or a "morning walk" as a football match, or invents a health condition nobody mentioned, the code overrides it. There is a test for each of those.

The model has no memory of weather or geography, and it is never allowed to state a fact it was not given.

---

## Slide 6 — Honest limitations

- **The air-quality grid is coarse.** Outside Europe the CAMS grid is roughly 25 to 40 km wide, so every venue in a city usually shares one forecast value. The venue-to-venue difference comes from our exposure engine, not from the API. A nearby station corrects the level, not the shape.
- **No live traffic feed.** Travel time is OSRM car routing plus a documented time-of-day multiplier per road class. It is an approximation and we label it as one.
- **OSM coverage is uneven.** In smaller towns there may be no mapped venues at all. We say so and suggest widening the radius instead of inventing a place.
- **Overpass can be at capacity.** The public mirrors throttle under load. We run a second venue source in parallel and cache failures briefly. The proper fix is a Geofabrik regional extract, which the rulebook allows, and it is the next thing we would build.
- **Latency is 20 to 60 seconds** on a laptop CPU, depending on how the free APIs are behaving. The live data takes 8 to 25 seconds of that and the model 8 to 20. We cache aggressively and every source has its own deadline, so a slow upstream degrades the answer instead of stalling it.
- **The model is small.** It misreads unusual phrasings occasionally. We compensate deterministically where we can, but a stronger parser would help.
- **Profiles are conservative thresholds, not medical guidance.** Every profile-adjusted answer says so.

**What we would add next:** a fine-tuned LoRA for the parser only, a regional OSM extract for reliability, and CPCB station data via data.gov.in for Indian AQI.

---

## Presenter notes

- **Slide 3** is the differentiator. Say the line: *"the same ground can be a bad idea at 8 AM and a good one at 6 PM, because the wind and the traffic both change"*, then show that it is a unit test.
- **Slide 4** is best shown live. Run the question in front of the judges and let the proof block do the talking. The "nothing is ideal" version is the more impressive one, because it shows the system reasoning about bad weather rather than pretending.
- **Slide 6** is worth doing properly. The rulebook asks for an honest note, and the judges will check whether the limitations are real. Ours are.
- **Live demo script:** (1) a normal question, (2) the same question with asthma added, to show the profile changing the answer, (3) a question in a place with heavy rain to show the avoid windows.
