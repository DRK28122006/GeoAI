# TODO — the fixes from Rajiv's live test

Source: the live test of `"Hey I am Rajiv, I want to cycle across Rushikonda in
Visakhapatnam but I had a fracture in my foot okay tell me the best way and
safest way"`.

Status legend: [ ] todo · [x] done

---

## 1. The health condition was ignored  ← the important one

The question says "I had a fracture in my foot" and "the safest way". We
answered with a normal cycling recommendation and never mentioned the injury.

- [ ] 1.1 Add an `injury` / `recovery` profile to `app/configs/profiles.yaml`
      (fracture, injury, injured, surgery, cast, sprain, pain, recovery,
      rehab, physio, crutches, wheelchair)
- [ ] 1.2 Decide what the profile does. Recommended: it is not a silent
      limit-tightening profile, it changes the *advice*:
        - the activity becomes "gentler" (walking instead of cycling, a flat
          route, a shorter distance)
        - the advice says plainly that cycling is not advisable with this
          injury and suggests the alternative
        - the limits are tightened too (heat and AQI, because a person
          recovering cannot push themselves)
- [ ] 1.3 Add the profile words to `PROFILE_KEYWORDS` in `app/pipeline.py`
- [ ] 1.4 Detect "safest", "safe", "best way", "is it safe", "advised" and
      set the intent type to `safety_check`
- [ ] 1.5 Make the answer open with the person, not the place, when a
      health condition is present. Right now it opens with "Go to X".
- [ ] 1.6 Add the safety note to the proof block for this profile
- [ ] 1.7 Test: the Rajiv question must mention the fracture, must not
      recommend cycling as the primary answer, and must give a gentler
      alternative

## 2. The model never wrote the answer

`slm_choose_ms` was missing from the timings and only one "SLM call" was
logged. The "nothing is ideal" branch returns before the chooser runs, so
`answer_source` was `template`.

- [ ] 2.1 Let the model phrase the "nothing is ideal" answer as well
- [ ] 2.2 It must still be verified; on failure the template takes over
- [ ] 2.3 The timings must always include `slm_choose_ms`
- [ ] 2.4 Test: a question where every slot is disqualified must still have
      `answer_source: "model"` and a `slm_choose_ms` in the timings

## 3. The avoid reasons are in the wrong field

`"51% chance of rain"` and `"thunderstorm risk"` were in `limitations`
instead of `avoid`.

- [ ] 3.1 `limitations` holds only data caveats (a source failed, an
      assumption, a coarse grid)
- [ ] 3.2 The avoid reasons stay in `avoid`
- [ ] 3.3 Check the same on all four response paths
- [ ] 3.4 Test: no entry in `limitations` may be a weather condition

## 4. The response is 48 KB, 35 KB of it GeoJSON

45 road lines with 1,027 vertices flooded the terminal.

- [ ] 4.1 Simplify the road lines (Douglas-Peucker, a small tolerance)
- [ ] 4.2 Cap the number of hazard lines in the GeoJSON (keep the ones
      nearest to the recommended venue)
- [ ] 4.3 Round the coordinates
- [ ] 4.4 Target: the whole response under 25 KB
- [ ] 4.5 Test: the response size stays under the limit

## 5. Geocode noise

A car wash called "Detailing Wolves - Visakhapatnam" was a candidate for
"Rushikonda in Visakhapatnam", and the pick was the rural mandal
"Rushikonda, Vammivanipalem" rather than the area the user meant.

- [ ] 5.1 Drop candidates whose kind is a business (car_wash, fuel, bank,
      cafe, restaurant, pharmacy, hairdresser, clothes)
- [ ] 5.2 Prefer a candidate that is inside the city or district the user
      named
- [ ] 5.3 Test: "Rushikonda in Visakhapatnam" must not return a car wash

## 6. Smaller items

- [ ] 6.1 The self-test log line reads `self test ok: </think>` because the
      model echoes the empty think block. Use a prompt that does not.
- [ ] 6.2 `status` should say `partial` only when there is a real
      limitation, not when the only caveat is "no station token"
- [ ] 6.3 The README must show the new profile in the list of four

## 7. After the fixes

- [ ] 7.1 Re-run `scripts/rulebook_examples.py` and check nothing regressed
- [ ] 7.2 Re-run the 15 query set in `scripts/eval_queries.py`
- [ ] 7.3 Re-check the Rajiv question by hand
- [ ] 7.4 Update the slides if the answer format changed
- [ ] 7.5 Commit
