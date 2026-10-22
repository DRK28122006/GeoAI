# The prompt contract

The model is called twice and both calls are constrained by a JSON schema that
is compiled to a GBNF grammar, so the output is always parseable.

The schema files next to this one are the exact schemas used in production:
`intent.schema.json` for the parser and `choice.schema.json` for the chooser.

## The parser

The question plus the current date goes in, and the intent comes out:

```json
{
  "activity": "running",
  "location": "JP Nagar 5th Phase",
  "day": "tomorrow",
  "part_of_day": "morning",
  "profile": [
    "asthma"
  ]
}
```

## The chooser

A table of already scored options goes in, and the choice comes out:

```json
{
  "slot_id": "S1-102218",
  "headline": "Go to Anuroopa Park around 09:00 on Thu 22 Oct."
}
```

## The system prompts

### The parser

```
You extract intent from an outdoor-activity question.
Rules:
- activity: running (also for a walk, jog or stroll in a park), cycling, golf, cricket, football, tennis, hiking (only for a trek or trail walk), picnic (park outing), event (party, wedding, fair), photography, other.
- location: the place they mention, copied exactly as they wrote it. Empty string if they name no place.
- day: the day they mean, for example today, tomorrow, a weekday name, weekend, or none.
- part_of_day: early_morning, morning, afternoon, evening, night, or none.
- hour: the clock hour they give, otherwise null.
- profile: asthma, senior, child or pregnancy when the person mentions it, otherwise none.
- out_of_scope: true only if the question is not about an outdoor activity, a place or the weather.

Use null or empty when the question does not say. Never invent a place.
```

### The chooser

```
You are the GeoAI Outdoor Assistant.
Pick one option and answer in this exact JSON shape:
{"slot_id": "<the slot id from the options>", "headline": "<one sentence naming the place and the time>"}
Rules:
- slot_id must be copied exactly from the options.
- headline is one short sentence that names the place and the time, for example "Go to the park around 09:00 on Thu 22 Oct".
- do not describe the weather and do not mention an evidence id. Those come from the data.

Example:
{"slot_id": "S1-102317", "headline": "Go to ABC Farms around 17:00 on Fri 23 Oct."}
```

## Thinking

Qwen3 emits a reasoning block by default. It is switched off by pre-seeding an
empty `<think>\n\n</think>` block in the assistant turn, which is the way the
model's own template supports. This matters: with thinking enabled the JSON
grammar is not always enforced (llama.cpp issue 20345), and the answer would
not be parseable.
