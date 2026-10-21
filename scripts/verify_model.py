"""Terminal verification of the local model.

    source .venv/bin/activate
    python scripts/verify_model.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from llama_cpp import Llama, LlamaGrammar

MODEL = os.environ.get(
    "SLM_MODEL_PATH", "models/Qwen3-1.7B-Q4_K_M.gguf"
)

PARSER_SCHEMA = {
    "type": "object",
    "properties": {
        "activity": {
            "type": "string",
            "enum": [
                "running", "cycling", "golf", "cricket", "football", "tennis",
                "hiking", "picnic", "event", "photography", "other",
            ],
        },
        "location": {"type": "string"},
        "day": {
            "type": "string",
            "enum": [
                "today", "tomorrow", "day_after", "weekend", "next_weekend",
                "monday", "tuesday", "wednesday", "thursday", "friday",
                "saturday", "sunday", "none",
            ],
        },
        "part_of_day": {
            "type": "string",
            "enum": ["early_morning", "morning", "afternoon", "evening", "night", "none"],
        },
        "hour": {"type": ["integer", "null"]},
        "profile": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["asthma", "senior", "child", "pregnancy", "none"],
            },
        },
        "out_of_scope": {"type": "boolean"},
    },
    "required": [
        "activity", "location", "day", "part_of_day", "hour", "profile", "out_of_scope",
    ],
}

SYSTEM = """Extract intent from an outdoor-activity question. Rules:
- activity: running (also for a walk, jog or stroll in a park), cycling, golf, cricket, football, tennis, hiking (only for a trek or trail walk), picnic (park outing), event (party, wedding, fair), photography, other.
- location: the place name copied from the question without the city. Empty string if the question names no place.
- day: today, tomorrow, day_after, weekend, next_weekend, a weekday name, or none.
- part_of_day: early_morning, morning, afternoon, evening, night, or none.
- hour: the clock hour if the question gives one, otherwise null.
- profile: asthma, senior, child or pregnancy when the person mentions it, otherwise ["none"].
- out_of_scope: true only if the question is not about an outdoor activity, place or weather.
Never invent a place that is not written in the question."""


def chatml(system: str, user: str) -> str:
    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )


def main() -> None:
    print("=" * 68)
    print("model file")
    sys.stdout.flush()
    subprocess.run(["ls", "-lh", MODEL], check=False)
    subprocess.run(["sha256sum", MODEL], check=False)
    sys.stdout.flush()

    print("=" * 68)
    print("llama-bench on cpu (same config the app uses)")
    sys.stdout.flush()
    env = dict(os.environ, GGML_VK_VISIBLE_DEVICES="-1")
    subprocess.run(
        ["llama-bench", "-m", MODEL, "-t", "4", "-p", "256", "-n", "64", "-r", "2"],
        check=False,
        env=env,
    )
    sys.stdout.flush()

    llm = Llama(MODEL, n_ctx=4096, n_threads=4, n_batch=256, verbose=False)

    print("=" * 68)
    print("grammar constrained intent parse")
    sys.stdout.flush()
    question = (
        "I have mild asthma, where is a good place for a morning walk near "
        "JP Nagar 5th Phase in Bengaluru tomorrow?"
    )
    grammar = LlamaGrammar.from_json_schema(json.dumps(PARSER_SCHEMA))
    t0 = time.perf_counter()
    out = llm.create_completion(
        prompt=chatml(SYSTEM, f"Today is 2026-10-21 21:10 (IST). Question: {question}"),
        grammar=grammar,
        max_tokens=140,
        temperature=0.1,
        top_p=0.9,
        stop=["<|im_end|>"],
    )
    dt = time.perf_counter() - t0
    usage = out["usage"]
    raw = out["choices"][0]["text"].strip()
    print(raw)
    parsed = json.loads(raw)
    print("json.loads ok:", parsed)
    print("thinking tokens present:", "<think>" in raw and "</think>" in raw)
    print(
        f"prompt {usage['prompt_tokens']} tok, completion {usage['completion_tokens']} tok, "
        f"{dt:.1f}s, {usage['completion_tokens'] / dt:.1f} tok/s"
    )

    print("=" * 68)
    print("free form answer with live engine values")
    sys.stdout.flush()
    t0 = time.perf_counter()
    out2 = llm.create_completion(
        prompt=chatml(
            "You are the GeoAI Outdoor Assistant. Recommend a place and time in under "
            "3 sentences. Use only the numbers you are given.",
            "Venue: Anuroopa Park | Time: Thu 22 Oct 09:00 | Apparent temp 26C | "
            "US-AQI 43 | Precip 23% | Wind 9km/h from W | Travel 9 min | Exposure 0.07\n"
            "Question: Where can I go running tomorrow morning near Jayanagar?",
        ),
        max_tokens=160,
        temperature=0.3,
        top_p=0.9,
        stop=["<|im_end|>"],
    )
    dt = time.perf_counter() - t0
    usage2 = out2["usage"]
    print(out2["choices"][0]["text"].strip())
    print(
        f"prompt {usage2['prompt_tokens']} tok, completion {usage2['completion_tokens']} tok, "
        f"{dt:.1f}s, {usage2['completion_tokens'] / dt:.1f} tok/s"
    )
    print("=" * 68)


if __name__ == "__main__":
    main()
