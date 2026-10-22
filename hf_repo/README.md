---
license: apache-2.0
library_name: transformers
pipeline_tag: text-generation
tags:
  - qwen3
  - text-generation
  - geoai
  - outdoor-activity-planning
  - small-language-model
  - hackathon
base_model: Qwen/Qwen3-1.7B
---

# GeoAI Outdoor Assistant — intent parser and slot chooser (Qwen3-1.7B)

This repository is the model that runs inside **GeoAI Outdoor Assistant**, a
GeoAI Hackathon @ CSRE, IIT Bombay submission. It answers free-form questions
like *"I have mild asthma, where's good for a morning walk near Powai in the
next two days?"* with a specific place, a specific time, and evidence for
every claim.

## Base model

| | |
|---|---|
| Base model | [`Qwen/Qwen3-1.7B`](https://huggingface.co/Qwen/Qwen3-1.7B) |
| Licence | Apache-2.0 |
| Parameters | 2.03B including embeddings (1.72B non-embedding) — under the 3B limit |
| Context length | 40,960 (we serve 4,096) |
| Weights in this repo | **Unchanged.** No fine-tuning was performed for this submission. |

The weights here are byte-identical to the base model. What this repository
adds is the **configuration and documentation for how we use it**: the prompt
contract, the JSON schemas the outputs are constrained to, and the GGUF build
we serve in production.

## How the model is used

The model does **two** narrow jobs and nothing else. All facts come from live
API calls, and the model never recalls weather or geography.

**1. Intent parsing.** Input is the user's question plus the current date.
Output is a JSON object constrained to a schema:

```json
{
  "activity": "running",
  "location": "JP Nagar 5th Phase",
  "day": "tomorrow",
  "part_of_day": "morning",
  "hour": null,
  "profile": ["asthma"],
  "out_of_scope": false
}
```

**2. Slot choosing.** Input is a table of already-scored options (place,
time, score, temperature, air quality, rain chance). Output is a JSON object
with two fields:

```json
{
  "slot_id": "S1-102218",
  "headline": "Go to Anuroopa Park around 09:00 on Thu 22 Oct."
}
```

The model picks the slot and writes that one sentence. Everything else in the
answer - the reasons with their evidence, the carry list, the watch-outs and
the avoid windows - is composed in code from the same fetched data, so the
model is never the source of a fact. That is deliberate: a small model that
is asked for the reasons will eventually state a number that was never
fetched.

The model is asked for JSON and the output is **constrained by a grammar**
compiled from the schema, so the structure is always valid.

The exact schemas are published next to this card, so the contract is
inspectable rather than described:

| File | What it is |
| --- | --- |
| `intent.schema.json` | the schema the parser is constrained to |
| `choice.schema.json` | the schema the chooser is constrained to |
| `prompts.md` | both system prompts, and how thinking is switched off |
| `serving_config.json` | how the model is served, and the measured speed | A separate
rule-based verifier then re-checks every number, clock time, place name and
evidence citation in the model's wording against the fetched data. If a check
fails the model is asked once more; if it fails again the answer is produced
by a deterministic template built from the same data. The model is never
allowed to state a fact that was not fetched.

## Intended use and limitations

* **Intended use.** Ranking and explaining shortlists of outdoor venues for
  an urban user in India, and extracting intent from free-form English
  questions about outdoor activities.
* **Not for medical advice.** Profile handling (asthma, senior, child,
  pregnancy) only tightens conservative thresholds. Every profile-adjusted
  answer carries a "not medical advice" note.
* **Not a knowledge base.** The model has no memory of weather, place names
  or AQI. It must be given the data.
* **English only.** The parser is tuned for English and light Hinglish.
  Other languages are out of scope.
* **Small-model limits.** The parser occasionally misreads an unusual
  phrasing. The production system compensates by deriving the calendar and
  the profile match deterministically in code, so the user's own wording
  always wins over the model's interpretation.

## Loading

The weights are the standard Qwen3 safetensors, so the repository loads with
the standard API:

```python
from transformers import AutoModelForCausalLM, AutoTokenizer

model = AutoModelForCausalLM.from_pretrained("<this-repo>")
tokenizer = AutoTokenizer.from_pretrained("<this-repo>")
```

For production we serve the same model through **llama.cpp** using the
`Qwen3-1.7B-Q4_K_M.gguf` file included in this repository:

```python
from llama_cpp import Llama

llm = Llama("Qwen3-1.7B-Q4_K_M.gguf", n_ctx=4096, n_threads=4, verbose=False)
```

Qwen3 emits a reasoning block by default. We disable it by pre-seeding an
empty `<think>\n\n</think>` block in the assistant turn, which is the
officially supported way to switch thinking off, and which keeps the answer
short enough for CPU inference.

This is not cosmetic. With thinking enabled the JSON grammar is not always
enforced (llama.cpp issue 20345), and the answer would not be parseable. The
guard is covered by a test in the application repository.

## The GGUF in this repository

`Qwen3-1.7B-Q4_K_M.gguf` is a **third-party quantisation** of the base model
by the [unsloth](https://huggingface.co/unsloth) organisation, Apache-2.0, and
it is the exact file the service loads. It is included so the repository is a
faithful mirror of what runs in production, not so it can be loaded with
`transformers`.

```python
from llama_cpp import Llama

llm = Llama.from_pretrained(
    "<this-repo>", filename="Qwen3-1.7B-Q4_K_M.gguf", n_ctx=4096
)
```

## Chat template note

The repository uses the base model's chat template. Qwen3's template accepts
`enable_thinking`; when it is `false` the template pre-seeds the empty think
block itself, so the same behaviour is obtained either way.

## Data sources

The model itself is trained on nothing from this project. The application
that uses it fetches its data live at query time from free and open sources:
OpenStreetMap (Overpass API, Nominatim), Open-Meteo (weather and air quality
via CAMS ENSEMBLE), WAQI, OSRM and Open-Meteo/Astral for daylight.

## Disclosure

* The only language model used at request time is this one, run locally.
  No GPT-4-class, Claude or Gemini model is called anywhere in the running
  assistant.
* AI coding assistants were used to write the surrounding application code.
  This model was not trained or fine-tuned.

© OpenStreetMap contributors for the map data used by the application that
serves this model.
