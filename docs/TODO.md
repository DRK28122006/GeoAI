# TODO — production hardening

Scope: the engine and the AI side. No slides, no marketing.
Deadline: 24 Oct, 11:59 PM IST (we treat 24 Oct 18:00 as ours).

## 1. Diagnose and fix the flaws

Done, from two independent audits of the code:

- [x] 1.1 Dead code: unused functions, unused config keys, unused imports
      - removed: LOCATION_CUES, SourceUnavailable, TimeWindow.dates, MAX_WORKERS,
        SLM_MAX_TOKENS, TTL_STATION_OBS, the hazards_cfg load, Slot.venue_id,
        EngineResult.alternatives, EngineResult.notes, Venue.area_m2,
        GeocodeCandidate.confidence, _raw_question, bundle["candidates"],
        the unused loggers, _model_text, _profile_note
      - every key in every config file is read by the code
      - the hazard selectors moved from the source into hazards.yaml
- [x] 1.2 The config keys that are read nowhere - all now used or removed
- [x] 1.3 Error handling: every path returns a usable answer
- [x] 1.4 Concurrency: the model lock times out, the cache is behind a lock,
      the rate pacer is atomic, the in-flight limit matches the bottleneck
- [x] 1.5 Configuration through the environment, not hardcoded
- [x] 1.6 Logging: a request id on every request, in the log and the body
- [x] 1.7 Security: the token is redacted everywhere, the input is bounded

## 2. Clean the tree

- [x] 2.1 Removed the slides (not our job)
- [x] 2.2 Removed the superseded scripts (rulebook_examples, verify_model,
      smoke_test)
- [x] 2.3 Removed the build artifacts that were committed (build/, egg-info)
- [x] 2.4 Nothing in the repo that should not be published

## 3. Docker

- [x] 3.1 The image builds from a clean machine
- [x] 3.2 The model is in the image, the app starts with no network
- [x] 3.3 The health check points at /live and the readiness at /ready
- [x] 3.4 It runs as a non-root user
- [x] 3.5 It stops cleanly on SIGTERM, with a graceful shutdown timeout
- [x] 3.6 Two stages, so the compilers do not ship with the service
- [x] 3.7 The build is portable: no instructions that only work on one CPU
- [x] 3.8 docker-compose.yml with a restart policy, a memory limit and a
      cache volume

## 4. Hugging Face

- [x] 4.1 The model card names the base model, the licence and the limits
- [x] 4.2 The repository loads with from_pretrained (Llama.from_pretrained
      on the GGUF; the unquantized upstream is linked, not duplicated)
- [x] 4.3 The GGUF we serve is uploaded
- [x] 4.4 The upload script checks the repository before and after
- [x] 4.5 The card documents the prompt contract, and the schemas are
      published next to it
- [x] 4.6 The repository is GGUF-only: card, load snippet and upload script
      match, and the base weights are not duplicated

## 5. Final

- [x] 5.1 The whole test suite passes
- [x] 5.2 The lint is clean
- [x] 5.3 A live request works end to end
- [x] 5.4 The README is accurate
- [x] 5.5 Committed

---

## Still open, and it needs a person

- [x] The hosting decision and the endpoint URL in the README
      - Azure VM `geoai-api`, Malaysia West, `Standard_B2als_v2`
        (2 vCPU / 4 GB, x64), Ubuntu 24.04
      - `https://85-211-194-173.sslip.io` (Caddy + Let's Encrypt)
      - API bound to `127.0.0.1:7860`; only Caddy is public
- [ ] Push `5a90499` and the latest local docs/handoff commit to GitHub. The
      VM runs the current local tree; the GitHub repo is two commits behind.
- [ ] Set an Azure budget alert (for example $20 / $50 / $80).
- [ ] The Geofabrik regional extract, to remove the Overpass dependency.
      The public mirrors are slow and one of them serves a single request at
      a time, which is now the biggest source of latency
