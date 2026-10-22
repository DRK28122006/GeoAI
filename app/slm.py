"""The small language model: a local Qwen3-1.7B served by llama.cpp.

Design rules (from the PRD):

* The model **never** computes anything and never invents a fact. It parses
  the question and picks/explains from an already-scored table.
* Every call is constrained by a JSON schema compiled to a GBNF grammar, so
  the output is always parseable JSON with the exact fields we expect.
* Qwen3 thinks by default. We pre-seed an empty ``<think>`` block in the
  assistant turn, which is the officially supported way to switch thinking
  off, and it keeps the answers short enough for CPU inference.
* The model is loaded at process start, not on the first request, so the
  first user of the judging window does not pay the load cost.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger("geoai.slm")

ACTIVITIES = [
    "running",
    "cycling",
    "golf",
    "cricket",
    "football",
    "tennis",
    "hiking",
    "picnic",
    "event",
    "photography",
]

INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "activity": {"type": "string", "enum": ACTIVITIES + ["other"]},
        "location": {"type": "string"},
        "day": {
            "type": "string",
            "enum": [
                "today",
                "tomorrow",
                "day_after",
                "weekend",
                "next_weekend",
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
                "none",
            ],
        },
        "part_of_day": {
            "type": "string",
            "enum": [
                "early_morning",
                "morning",
                "afternoon",
                "evening",
                "night",
                "none",
            ],
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
        "activity",
        "location",
        "day",
        "part_of_day",
        "hour",
        "profile",
        "out_of_scope",
    ],
}

CHOICE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "slot_id": {"type": "string", "maxLength": 24},
        "headline": {"type": "string", "maxLength": 120},
        "why": {
            "type": "array",
            "items": {"type": "string", "maxLength": 110},
            "maxItems": 3,
        },
        "carry": {
            "type": "array",
            "items": {"type": "string", "maxLength": 70},
            "maxItems": 4,
        },
        "watch_for": {
            "type": "array",
            "items": {"type": "string", "maxLength": 80},
            "maxItems": 4,
        },
        "avoid_note": {"type": "string", "maxLength": 110},
    },
    "required": ["slot_id", "headline", "why", "carry", "watch_for", "avoid_note"],
}

PARSER_SYSTEM = """You turn a question about an outdoor activity into structured data.

activity: the outdoor activity the person wants to do.
location: the place they mention, copied exactly as they wrote it. Empty string if they name no place.
day: the day they mean, for example today, tomorrow, a weekday name, weekend, or none.
part_of_day: early_morning, morning, afternoon, evening, night, or none.
hour: the clock hour they give, otherwise null.
profile: asthma, senior, child or pregnancy when the person mentions it, otherwise none.
out_of_scope: true only if the question is not about an outdoor activity, a place or the weather.

Use null or empty when the question does not say. Never invent a place."""

CHOOSER_SYSTEM = """You are the GeoAI Outdoor Assistant. You are given options that were scored from live weather, air-quality and map data.

Pick one slot_id from the options. Then write:
headline: one sentence naming the place and the time.
why: two reasons, each starting with the evidence id it relies on, for example "E_AQ: US-AQI 42 is good".
carry: two or three things to take.
watch_for: two or three things to watch out for.
avoid_note: one sentence on when to avoid going, or an empty string.

Be brief. Use only numbers that appear in the options. Do not add facts."""

class SLMUnavailable(RuntimeError):
    """Raised when the model cannot be loaded or served."""


class SLM:
    """Thread-safe wrapper around a locally served Qwen3-1.7B."""

    def __init__(self) -> None:
        self.model_path = settings.SLM_MODEL_PATH
        self.backend = settings.SLM_BACKEND
        self._llm = None
        self._lock = threading.Lock()
        self._loaded_at: float | None = None
        self._load_error: str | None = None
        self._http: httpx.Client | None = None
        # Repeated questions (the judging demo) should not pay for inference
        # twice. The cache holds parsed results, never the raw model output.
        self._cache: dict[tuple, dict[str, Any]] = {}
        self._cache_max = 128

    # ------------------------------------------------------------- lifecycle
    def load(self) -> bool:
        """Load the model eagerly. Returns True when it is ready."""
        if self._llm is not None or self.backend == "server":
            if self.backend == "server":
                return self._server_ready()
            return True
        if not os.path.exists(self.model_path):
            self._load_error = f"model file not found: {self.model_path}"
            logger.error(self._load_error)
            return False
        try:
            from llama_cpp import Llama

            logger.info("Loading SLM from %s", self.model_path)
            self._llm = Llama(
                model_path=self.model_path,
                n_ctx=settings.SLM_N_CTX,
                n_threads=settings.SLM_N_THREADS,
                n_batch=settings.SLM_N_BATCH,
                n_gpu_layers=0,
                verbose=False,
            )
            self._loaded_at = time.time()
            self._load_error = None
            logger.info("SLM ready in %.1fs", time.time() - self._loaded_at)
            return True
        except Exception as exc:  # noqa: BLE001
            self._load_error = str(exc)
            logger.error("Failed to load SLM: %s", exc)
            return False

    def _server_ready(self) -> bool:
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(f"{settings.SLM_SERVER_URL}/health")
            return resp.status_code == 200
        except Exception as exc:  # noqa: BLE001
            self._load_error = str(exc)
            return False

    @property
    def ready(self) -> bool:
        if self.backend == "server":
            return self._server_ready()
        return self._llm is not None

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "model": settings.SLM_BASE_MODEL,
            "model_path": self.model_path if self.backend == "inprocess" else None,
            "loaded": self.ready,
            "loaded_at": self._loaded_at,
            "error": self._load_error,
            "params": "2.03B (1.7B non-embedding) - under the 3B limit",
        }

    # ------------------------------------------------------------ generation
    @staticmethod
    def _chatml(system: str, user: str) -> str:
        """ChatML prompt with an empty think block, i.e. Qwen3 non-thinking."""
        return (
            f"<|im_start|>system\n{system}<|im_end|>\n"
            f"<|im_start|>user\n{user}<|im_end|>\n"
            f"<|im_start|>assistant\n<think>\n\n</think>\n\n"
        )

    def _complete_json(
        self, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        key = (system, user, max_tokens)
        cached = self._cache.get(key)
        if cached is not None:
            logger.info("SLM cache hit for %s...", user[:60])
            return cached
        prompt = self._chatml(system, user)
        if self.backend == "server":
            result = self._complete_json_server(prompt, schema, max_tokens)
        else:
            result = self._complete_json_inprocess(prompt, schema, max_tokens)
        if len(self._cache) > self._cache_max:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = result
        return result

    def _complete_json_inprocess(
        self, prompt: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        if self._llm is None:
            raise SLMUnavailable(self._load_error or "model not loaded")
        from llama_cpp import LlamaGrammar

        grammar = LlamaGrammar.from_json_schema(json.dumps(schema))
        started = time.perf_counter()
        with self._lock:
            out = self._llm.create_completion(
                prompt=prompt,
                grammar=grammar,
                max_tokens=max_tokens,
                temperature=0.1,
                top_p=0.9,
                stop=["<|im_end|>"],
            )
        usage = out.get("usage", {})
        elapsed = time.perf_counter() - started
        completion = usage.get("completion_tokens", 0)
        logger.info(
            "SLM call: prompt=%s completion=%s in %.1fs (%.1f tok/s)",
            usage.get("prompt_tokens"),
            completion,
            elapsed,
            (completion / elapsed) if elapsed and completion else 0.0,
        )
        text = out["choices"][0]["text"].strip()
        return self._parse(text)

    def _complete_json_server(
        self, prompt: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        url = f"{settings.SLM_SERVER_URL}/v1/chat/completions"
        payload = {
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": ""},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "out", "schema": schema},
            },
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        with httpx.Client(timeout=120) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            body = resp.json()
        return self._parse(body["choices"][0]["message"]["content"])

    @staticmethod
    def _parse(text: str) -> dict[str, Any]:
        text = text.strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"model did not return JSON: {text[:120]}")
        return json.loads(text[start : end + 1])

    # ------------------------------------------------------------------ API
    def parse_intent(self, question: str, now_iso: str) -> dict[str, Any]:
        user = f"Today is {now_iso} (IST). Question: {question}"
        data = self._complete_json(PARSER_SYSTEM, user, INTENT_SCHEMA, max_tokens=220)
        data["_raw_question"] = question
        return data

    def choose(
        self, question: str, options: list[dict[str, Any]], evidence: dict[str, Any]
    ) -> dict[str, Any]:
        table = "\n".join(
            f"{o['slot_id']} | {o['place']} | {o['time']} | score {o['score']} | "
            f"temp {o.get('apparent_temp_c')}C | AQI {o.get('us_aqi')} | "
            f"precip {o.get('precip_prob')}% | wind {o.get('wind_kmh')}km/h from "
            f"{o.get('wind_dir')} | travel {o.get('travel_min')} min | "
            f"exposure {o.get('exposure')}"
            for o in options
        )
        # Keep the prompt small: on CPU every extra token costs time.
        ev = "\n".join(
            f"{eid}: {str(val.get('value'))[:90]}"
            for eid, val in list(evidence.items())[:8]
        )
        user = f"Options:\n{table}\n\nEvidence:\n{ev}\n\nQuestion: {question[:300]}"
        return self._complete_json(CHOOSER_SYSTEM, user, CHOICE_SCHEMA, max_tokens=260)


slm = SLM()
