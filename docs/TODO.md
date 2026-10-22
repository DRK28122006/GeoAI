# TODO — the fixes from Rajiv's live test

Source: the live test of `"Hey I am Rajiv, I want to cycle across Rushikonda in
Visakhapatnam but I had a fracture in my foot okay tell me the best way and
safest way"`.

Status legend: [ ] todo · [x] done

---

## 1. The health condition was ignored  ← the important one

The question says "I had a fracture in my foot" and "the safest way". We
answered with a normal cycling recommendation and never mentioned the injury.

- [x] 1.1 Add an `injury` / `recovery` profile to `app/configs/profiles.yaml`
- [x] 1.2 The profile rules the activity out, it does not only tighten limits
- [x] 1.3 Add the profile words to `PROFILE_KEYWORDS` in `app/pipeline.py`
- [x] 1.4 Detect "safest", "safe way", "best way" as a `safety_check` intent
- [x] 1.5 The answer opens with the person, not the place
- [x] 1.6 The safety note is in the proof block and in `advice.note`
- [x] 1.7 Test: the Rajiv question mentions the fracture, does not recommend
      cycling, and gives a gentler alternative
- [x] 1.8 Extra: a private pitch is never recommended (`access=private`)

## 2. The model never wrote the answer

`slm_choose_ms` was missing from the timings and only one "SLM call" was
logged. The "nothing is ideal" branch returned before the chooser ran.

- [x] 2.1 The model phrases the "nothing is ideal" answer as well
- [x] 2.2 It is still verified; on failure the template takes over
- [x] 2.3 The timings always include `slm_choose_ms`
- [x] 2.4 The caveat always leads, so a slot ruled out by the weather is
      never presented as a recommendation

## 3. The avoid reasons were in the wrong field

`"51% chance of rain"` and `"thunderstorm risk"` were in `limitations`
instead of `avoid`.

- [x] 3.1 `limitations` holds only data caveats
- [x] 3.2 The avoid reasons stay in `avoid`
- [x] 3.3 Checked on all four response paths
- [x] 3.4 Test: no entry in `limitations` may be a weather condition

## 4. The response was 48 KB, 35 KB of it GeoJSON

45 road lines with 1,027 vertices flooded the terminal.

- [x] 4.1 The road lines are simplified (Douglas-Peucker)
- [x] 4.2 Capped to the 12 hazards nearest the recommendation
- [x] 4.3 The coordinates are rounded
- [x] 4.4 The response is now 10 to 21 KB
- [x] 4.5 Test: the size stays under the limit

## 5. Geocode noise

A car wash called "Detailing Wolves - Visakhapatnam" was a candidate, and the
other matches listed a place in Japan for an Indian query.

- [x] 5.1 Businesses are never candidates
- [x] 5.2 Only the matches from the same country are listed
- [x] 5.3 Test: a car wash is not a place

## 6. Smaller items

- [x] 6.1 The self test no longer echoes the empty think block
- [x] 6.2 The venue ranking prefers a known kind over an unclassified one
- [x] 6.3 A named venue gets a small edge, and our own placeholder does not
      count as a name
- [x] 6.4 The README lists the new profile and the new `advice.note` key
- [x] 6.5 The slides mention the injury profile

## 7. After the fixes

- [x] 7.1 Re-run `scripts/rulebook_examples.py` - 10/10 usable
- [x] 7.2 Re-run the 15 query set in `scripts/eval_queries.py` - 15/15 usable
- [x] 7.3 The Rajiv question checked by hand
- [x] 7.4 Committed

---

## Still open (not from this list)

- [ ] The Hugging Face model repository - needs an account
- [ ] Pushing the code to GitHub - needs a login
- [ ] The hosting decision
- [ ] The Geofabrik regional extract, to remove the Overpass dependency
- [ ] A WAQI token, for the station correction
