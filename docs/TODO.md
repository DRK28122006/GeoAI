# TODO — production hardening

Scope: the engine and the AI side. No slides, no marketing.
Deadline: 24 Oct, 11:59 PM IST (we treat 24 Oct 18:00 as ours).

## 1. Diagnose and fix the flaws

- [ ] 1.1 Dead code: unused functions, unused config keys, unused imports
- [ ] 1.2 The config keys that are read nowhere
- [ ] 1.3 Error handling: every path returns a usable answer
- [ ] 1.4 Concurrency: the model worker, the request queue, the shutdown
- [ ] 1.5 Configuration through the environment, not hardcoded
- [ ] 1.6 Logging: request ids, no secrets in the log
- [ ] 1.7 Security: the input limits, the CORS story, the file paths

## 2. Clean the tree

- [ ] 2.1 Remove the slides (not our job)
- [ ] 2.2 Remove the ad-hoc scripts that are not needed
- [ ] 2.3 Remove the duplicate or superseded files
- [ ] 2.4 Nothing in the repo that should not be published

## 3. Docker

- [ ] 3.1 The image builds from a clean machine
- [ ] 3.2 The model is in the image, the app starts with no network
- [ ] 3.3 The health check fails when the model is not loaded
- [ ] 3.4 It runs as a non-root user
- [ ] 3.5 It stops cleanly on SIGTERM
- [ ] 3.6 The image is small enough to pull quickly
- [ ] 3.7 The build is portable: no instructions that only work on one CPU

## 4. Hugging Face

- [ ] 4.1 The model card names the base model, the licence and the limits
- [ ] 4.2 The repository loads with from_pretrained
- [ ] 4.3 The GGUF we serve is in the repository
- [ ] 4.4 The upload script checks the repository before it uploads
- [ ] 4.5 The card documents the prompt contract and the two calls

## 5. Final

- [ ] 5.1 The whole test suite passes
- [ ] 5.2 The lint is clean
- [ ] 5.3 A live request works end to end
- [ ] 5.4 The README is accurate
- [ ] 5.5 Committed
