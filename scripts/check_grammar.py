"""Check that the JSON grammar stays active and no thinking leaks out.

The empty <think></think> block we pre-seed in the assistant turn is the
guard: ggml issue 20345 reports that grammar enforcement is silently not
applied when thinking is enabled, and Qwen3 likes to think.

Run it directly (it loads the model, so it is not part of pytest):

    python scripts/check_grammar.py
"""

from __future__ import annotations

import json
import time

from llama_cpp import Llama, LlamaGrammar

from app.slm import CHOICE_SCHEMA, INTENT_SCHEMA, SHARED_SYSTEM

MODEL = "models/Qwen3-1.7B-Q4_K_M.gguf"

PARSER_QUESTIONS = [
    "Where can I go running tomorrow evening near Koregaon Park?",
    "Best time this weekend for a kids' cricket match within 15 km of Whitefield?",
    "I have mild asthma, where's good for a morning walk near Powai in the next two days?",
    "Is it okay to cycle to work at 8 AM?",
    "Planning an outdoor birthday party on Sunday afternoon in Pune?",
    "Good place and time for golf on Saturday within 40 km of Andheri?",
    "Where can I take sunset photos this evening around Bandra?",
    "Is Saturday or Sunday better for a hike near Lonavala?",
    "When is it safe for my parents to walk outdoors tomorrow in Bengaluru?",
    "Outdoor tennis after work today near Hinjewadi?",
    "Write me a poem about the monsoon",
    "hey i wanna go for a jog tmrw near jayanagar",
]

OPTIONS = [
    {
        "slot_id": "S1-102218",
        "place": "Anuroopa Park",
        "time": "Thu 22 Oct, 09:00",
        "score": 92.0,
        "apparent_temp_c": 26.0,
        "us_aqi": 43.0,
        "precip_prob": 12.0,
    },
    {
        "slot_id": "S2-102218",
        "place": "BBMP Park",
        "time": "Thu 22 Oct, 11:00",
        "score": 88.0,
        "apparent_temp_c": 27.0,
        "us_aqi": 50.0,
        "precip_prob": 15.0,
    },
]


def chatml(system: str, user: str, think: bool = True) -> str:
    base = (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n"
    )
    return base + ("<think>\n\n</think>\n\n" if think else "")


def check(out, schema, label: str) -> tuple[bool, str]:
    raw = out["choices"][0]["text"].strip()
    if "<think>" in raw or "</think>" in raw:
        return False, "thinking leaked into the output"
    try:
        parsed = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return False, f"not valid JSON: {exc}"
    missing = set(schema["required"]) - set(parsed)
    if missing:
        return False, f"missing fields: {sorted(missing)}"
    return True, "ok"


def main() -> int:
    llm = Llama(MODEL, n_ctx=4096, n_threads=4, n_batch=256, verbose=False)
    failures = 0

    for think in (True, False):
        label = "with the empty think block" if think else "without it"
        print("=" * 70)
        print(f"the intent parser, {label}")
        print("=" * 70)
        grammar = LlamaGrammar.from_json_schema(json.dumps(INTENT_SCHEMA))
        for question in PARSER_QUESTIONS:
            started = time.perf_counter()
            out = llm.create_completion(
                prompt=chatml(
                    SHARED_SYSTEM,
                    f"TASK: parse\nToday is 2026-10-21 21:30 (IST). "
                    f"Question: {question}",
                    think,
                ),
                grammar=grammar,
                max_tokens=160,
                temperature=0.1,
                top_p=0.9,
                stop=["<|im_end|>"],
            )
            took = time.perf_counter() - started
            good, why = check(out, INTENT_SCHEMA, "parse")
            failures += 0 if good else 1
            activity = ""
            try:
                activity = json.loads(out["choices"][0]["text"])["activity"]
            except (KeyError, TypeError, ValueError):
                activity = "?"
            print(
                f"   {'ok  ' if good else 'FAIL'} {took:4.1f}s {activity:>12s} "
                f":: {question[:44]} {'' if good else why}"
            )

    print()
    print("=" * 70)
    print("the chooser")
    print("=" * 70)
    grammar = LlamaGrammar.from_json_schema(json.dumps(CHOICE_SCHEMA))
    for _ in range(3):
        out = llm.create_completion(
            prompt=chatml(
                SHARED_SYSTEM,
                "TASK: choose\nOptions:\n"
                + "\n".join(
                    f"{o['slot_id']} | {o['place']} | {o['time']} | score {o['score']} | "
                    f"{o['apparent_temp_c']}C | AQI {o['us_aqi']} | rain {o['precip_prob']}%"
                    for o in OPTIONS
                )
                + "\n\nQuestion: Where can I go running tomorrow near Jayanagar?",
            ),
            grammar=grammar,
            max_tokens=110,
            temperature=0.1,
            top_p=0.9,
            stop=["<|im_end|>"],
        )
        good, why = check(out, CHOICE_SCHEMA, "choose")
        failures += 0 if good else 1
        print(f"   {'ok  ' if good else 'FAIL'} {out['choices'][0]['text'].strip()[:90]} {'' if good else why}")

    print()
    print(f"{'all checks passed' if not failures else f'{failures} failures'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
