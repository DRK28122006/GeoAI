"""Smoke test across the ten required activities plus the tricky cases.

Usage:  .venv/bin/python scripts/eval_queries.py [--live]
Without --live it only prints the parsed intent (no live data calls).
"""

from __future__ import annotations

import asyncio
import sys
import time

from app.planner import now_ist, resolve

QUERIES = [
    ("running", "Where can I go running tomorrow evening near Koregaon Park in Pune?"),
    ("cycling", "Is it okay to cycle to work at 8 AM near HSR Layout Bengaluru?"),
    ("golf", "Good place and time for golf on Saturday within 40 km of Andheri Mumbai?"),
    ("cricket", "Best time this weekend for a kids' cricket match within 15 km of Whitefield Bengaluru?"),
    ("football", "Five-a-side football near Koramangala this evening?"),
    ("tennis", "Outdoor tennis after work today near Hinjewadi Pune?"),
    ("hiking", "Is Saturday or Sunday better for a hike near Lonavala?"),
    ("picnic", "Where can we have a picnic near Cubbon Park on Sunday afternoon?"),
    ("event", "Planning an outdoor birthday party on Sunday afternoon in Pune, what should I keep in mind?"),
    ("photography", "Where can I take sunset photos this evening around Bandra?"),
    ("asthma", "I have mild asthma, where's good for a morning walk near Powai in the next two days?"),
    ("senior", "When is it safe for my parents to walk outdoors tomorrow in Bengaluru?"),
    ("no-location", "Is it okay to cycle to work at 8 AM?"),
    ("vague-time", "Where can I go running this weekend near Jayanagar?"),
    ("out-of-scope", "Write me a poem about the monsoon"),
]


def show_window(question: str) -> None:
    w = resolve(raw_text=question, now=now_ist())
    print(
        f"   window: {w.label} | {w.start:%a %d %b %H:%M} -> {w.end:%H:%M} "
        f"({len(w.hours)} hours)"
    )
    for a in w.assumptions:
        print(f"   assumption: {a}")


async def main() -> None:
    live = "--live" in sys.argv
    from app.pipeline import pipeline

    if live:
        # The model is normally loaded by the FastAPI start-up hook. The
        # harness calls the pipeline directly, so load it here.
        pipeline.slm.load()

    for label, question in QUERIES:
        print(f"\n=== [{label}] {question}")
        show_window(question)
        if not live:
            continue
        t0 = time.perf_counter()
        result = await pipeline.run(question)
        dt = time.perf_counter() - t0
        rec = result.get("recommendation") or {}
        print(
            f"   -> status={result.get('status')} in {dt:.1f}s | "
            f"{rec.get('place')} @ {rec.get('time_label')} score={rec.get('score')}"
        )
        print("   answer:", (result.get("answer") or "").replace("\n", " | ")[:300])
        if result.get("limitations"):
            print("   limits:", "; ".join(result["limitations"])[:300])


if __name__ == "__main__":
    asyncio.run(main())
