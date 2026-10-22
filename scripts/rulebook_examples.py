#!/usr/bin/env python3
"""Run the ten example questions from the rulebook and print the results.

    python scripts/rulebook_examples.py
"""

from __future__ import annotations

import asyncio
import time

from app.pipeline import pipeline

# The ten examples the rulebook lists. The rulebook also says the real
# evaluation queries will be different, so scripts/eval_queries.py keeps a
# wider set with typos, missing locations and out of scope requests.
EXAMPLES = [
    "Where can I go running tomorrow evening near Koregaon Park?",
    "Best time this weekend for a kids' cricket match within 15 km of Whitefield?",
    "I have mild asthma, where's good for a morning walk near Powai in the next two days?",
    "Is it okay to cycle to work at 8 AM?",
    "Planning an outdoor birthday party on Sunday afternoon in Pune, what should I keep in mind?",
    "Good place and time for golf on Saturday within 40 km of Andheri?",
    "Where can I take sunset photos this evening around Bandra?",
    "Is Saturday or Sunday better for a hike near Lonavala?",
    "When is it safe for my parents to walk outdoors tomorrow in Bengaluru?",
    "Outdoor tennis after work today near Hinjewadi, any good spots?",
]


async def main() -> None:
    pipeline.slm.load()
    for question in EXAMPLES:
        started = time.perf_counter()
        result = await pipeline.run(question)
        took = time.perf_counter() - started
        rec = result.get("recommendation") or {}
        print("=" * 74)
        print(f"Q: {question}")
        print(
            f"   status={result.get('status')}  in {took:.0f}s  "
            f"source={result.get('answer_source')}"
        )
        if rec:
            print(
                f"   -> {rec.get('place')}  |  {rec.get('time_label')}  |  "
                f"score {rec.get('score')}"
            )
        print("   " + (result.get("answer") or "").replace("\n", "\n   "))
        if result.get("limitations"):
            print("   limits: " + "; ".join(result["limitations"])[:200])


if __name__ == "__main__":
    asyncio.run(main())
