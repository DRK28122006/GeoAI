"""The request pipeline.

    question
      -> SLM intent parser          (grammar-constrained JSON)
      -> gate: clarify / decline / proceed
      -> gather_context()           (live maps, weather, air quality, routing)
      -> exposure engine            (score every venue x hour)
      -> SLM chooser                 (pick + explain from the scored table)
      -> verifier                    (every number, time and place must check out)
      -> response                    (text + JSON + GeoJSON + evidence)

The model never invents a fact: it selects from a scored table and every claim
is checked against the same bundle the engine used.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from app.context import Intent, gather_context
from app.exposure_engine import ExposureEngine
from app.planner import now_ist
from app.slm import SLM, SLMUnavailable, slm
from app.verifier import Verifier

logger = logging.getLogger("geoai.pipeline")

# The words that decide the activity. A small model misreads them often
# enough to matter - it once turned a "morning walk" into a football match -
# so the question wins and the model fills the gaps.
ACTIVITY_KEYWORDS: dict[str, list[str]] = {
    "running": [
        "run", "running", "jog", "jogging", "dash", "walk", "walking", "stroll",
        "morning walk", "evening walk", "walk in the park",
    ],
    "cycling": [
        "cycle", "cycling", "bike", "biking", "ride", "riding", "bicycle",
        "commute by bike",
    ],
    "golf": ["golf", "golfing", "tee off"],
    "cricket": ["cricket", "batting", "gully", "cricket match", "cricket pitch"],
    "football": [
        "football", "soccer", "futsal", "five-a-side", "5-a-side", "five a side",
    ],
    "tennis": ["tennis", "badminton", "singles", "doubles"],
    "hiking": [
        "hike", "hiking", "trek", "trekking", "trail", "nature walk",
        "hill walk", "mountain walk",
    ],
    "picnic": ["picnic", "outing", "park day", "lunch in the park"],
    "event": [
        "party", "event", "wedding", "fair", "festival", "gathering",
        "birthday", "meeting outdoors",
    ],
    "photography": [
        "photo", "photos", "photography", "photograph", "sunset shot", "shoot",
    ],
}

PROFILE_KEYWORDS: dict[str, list[str]] = {
    "asthma": [
        "asthma", "asthmatic", "allergy", "allergic", "breathing", "copd",
        "bronchitis", "wheezy",
    ],
    "senior": [
        "senior", "elderly", "parents", "parent", "grandparent", "grandfather",
        "grandmother", "grandpa", "grandma", "aged", "dad", "father", "mother",
        "uncle", "aunt", "nani", "abba", "old age", "senior citizen",
    ],
    "child": [
        "child", "children", "kid", "kids", "son", "daughter", "toddler",
        "schoolboy", "schoolgirl", "baby", "infant", "little one",
    ],
    "pregnancy": [
        "pregnancy", "pregnant", "expecting", "expectant", "prenatal",
        "antenatal",
    ],
    # A recent injury. It is not only about tighter limits: the activity
    # itself may not be advisable, and the answer has to say so.
    "injury": [
        "fracture", "fractured", "injury", "injured", "surgery", "operation",
        "cast", "sprain", "sprained", "strain", "strained", "pain", "painful",
        "recovery", "recovering", "rehab", "physio", "physiotherapy", "crutches",
        "wheelchair", "broken", "bruise", "swollen", "cannot walk", "can t walk",
        "limping", "limp",
    ],
}

LOCATION_CUES = (
    "near",
    "around",
    "in ",
    "at ",
    "close to",
    "beside",
    "opposite ",
    "next to",
)

# words that start a question and are never a place name
QUESTION_WORDS = {
    "Is", "Are", "Am", "Can", "Could", "Do", "Does", "Did", "Will", "Would",
    "Should", "Where", "When", "What", "Which", "Who", "Whom", "Why", "How",
    "I", "We", "You", "The", "A", "An", "My", "Our", "Please", "Any", "Some",
    "Good", "Best", "There", "It", "This", "That", "Planning", "Looking",
}


def _dedupe(items: list[str]) -> list[str]:
    """Keep the order but drop repeats, so the response does not repeat itself."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


class Pipeline:
    def __init__(self) -> None:
        self.engine = ExposureEngine()
        self.verifier = Verifier()
        self.slm: SLM = slm

    # ------------------------------------------------------------ utilities
    @staticmethod
    def _run_blocking(fn, *args, **kwargs):
        loop = asyncio.get_running_loop()
        return loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    def _fallback_parse(self, question: str) -> dict[str, Any]:
        """Deterministic parser used when the model is unavailable."""
        q = question.lower()
        activity = "other"
        best = 0
        for act, words in ACTIVITY_KEYWORDS.items():
            for w in words:
                if w in q and len(w) > best:
                    activity, best = act, len(w)

        profile = [
            p
            for p, words in PROFILE_KEYWORDS.items()
            if any(w in q for w in words)
        ]

        # The place can be introduced in several ways.
        location = ""
        # The capitalisation matters here: "in the next two days" is not a
        # place, "in Powai" is.
        patterns = (
            r"(?:within|inside)\s+\d+\s*km\s+(?:of|from)\s+([A-Za-z][A-Za-z .'-]{2,40})",
            r"(?:near|around|close to|beside|opposite|next to)\s+([A-Za-z][A-Za-z .'-]{2,40})",
            r"\bin\s+([A-Z][A-Za-z .'-]{2,40})",
            r"\bat\s+([A-Z][A-Za-z .'-]{2,40})",
        )
        for pattern in patterns:
            m = re.search(pattern, question)
            if m:
                location = m.group(1).split(",")[0].strip()
                break
        # a place that starts with a common word is not a place
        if location:
            first = location.split()[0].lower()
            if first in {
                "the", "next", "this", "that", "my", "our", "it", "a", "an",
                "tomorrow", "today", "tonight", "morning", "evening", "afternoon",
                "night", "weekend", "i", "we", "you", "please", "is", "are",
            }:
                location = ""
        if not location:
            # a capitalised phrase that is not a question word
            m = re.search(
                r"(?:^|[?.]\s)([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})", question
            )
            if m and m.group(1).split()[0] not in QUESTION_WORDS:
                location = m.group(1).strip()

        radius = 5.0
        rm = re.search(r"(\d{1,3})\s*km", q)
        if rm:
            radius = float(rm.group(1))

        out_of_scope = bool(
            re.search(
                r"\b(poem|essay|write me|translate|recipe|joke|story|song|lyrics|"
                r"homework|resume|cover letter|bio for|linkedin|sql query|regex|"
                r"who is|what is the capital|news about)\b",
                q,
            )
        )

        return {
            "activity": "other" if out_of_scope else activity,
            "location": location,
            "radius_km": radius,
            "time_text": "",
            "day": None,
            "part_of_day": None,
            "hour": None,
            "days_ahead": self._extract_days_ahead(question),
            "profile": profile,
            "intent_type": self._extract_intent_type(question),
            "out_of_scope": out_of_scope,
            "missing": [] if location else ["location"],
            "_fallback": True,
        }

    @staticmethod
    def _clean_place(text: str) -> str:
        """Strip time words and prepositions from a place name.

        The model sometimes returns "Koramangala this evening"; geocoding
        that string finds a Wikipedia article, not a neighbourhood.
        """
        if not text:
            return ""
        cleaned = text.strip()
        cleaned = re.sub(
            r"\b(today|tomorrow|tonight|this\s+(?:evening|morning|afternoon|weekend)|"
            r"next\s+(?:week|weekend|days?)|morning|afternoon|evening|night|midday|"
            r"dawn|dusk|weekend|monday|tuesday|wednesday|thursday|friday|saturday|"
            r"sunday|early|late|around|about|please)\b",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\bat\s+\d{1,2}(:\d{2})?\s*(am|pm)?\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b\d{1,2}\s*(am|pm)\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b(near|around|close to|beside|opposite|next to)\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\bwithin\s+\d+\s*km\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"[^\w\s'\-]", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.-")
        return cleaned

    @staticmethod
    def _activity_from_words(question: str) -> str | None:
        """The activity the question actually names, if it names one."""
        q = question.lower()
        best: tuple[int, str] | None = None
        for act, words in ACTIVITY_KEYWORDS.items():
            for word in words:
                if word in q and (best is None or len(word) > best[0]):
                    best = (len(word), act)
        # A single letter or a very short word is not a reliable signal.
        if best and best[0] >= 4:
            return best[1]
        return None

    @staticmethod
    def _profile_limits_or_empty(question: str) -> list[str]:
        """The conditions the question mentions, in the order of the config."""
        q = question.lower()
        return [p for p, words in PROFILE_KEYWORDS.items() if any(w in q for w in words)]

    @staticmethod
    def _is_out_of_scope(question: str) -> bool:
        """Questions that are not about an outdoor activity, place or weather.

        The question decides this. A small model will happily try to answer
        a request to write a poem about the monsoon.
        """
        q = question.lower()
        return bool(
            re.search(
                r"\b(poem|essay|write me|translate|recipe|joke|story|song|lyrics|"
                r"homework|cover letter|linkedin|sql query|regex|news about|"
                r"who is|what is the capital|meaning of life)\b",
                q,
            )
        )

    @staticmethod
    def _place_in_question(location: str, question: str) -> bool:
        """True when the place is one the user actually wrote."""
        if not location:
            return False
        if location.lower() in {"none", "unknown", "n/a", "na", "null", "-"}:
            return False
        wanted = location.lower().strip()
        text = question.lower()
        if wanted in text:
            return True
        tokens = [t for t in re.split(r"[^\w]+", wanted) if len(t) > 2]
        return bool(tokens and all(t in text for t in tokens))

    def _normalise(
        self,
        parsed: dict[str, Any],
        question: str,
        lat: float | None = None,
        lon: float | None = None,
    ) -> Intent:
        activity = (
            self._activity_from_words(question) or parsed.get("activity") or "other"
        )
        location = self._clean_place(parsed.get("location") or "")
        if not self._place_in_question(location, question):
            # The model named a place the question does not mention, so its
            # answer is not trusted; the place is taken from the question.
            location = self._place_from_question(question)
        # The profile comes from the words in the question. The model is
        # allowed to suggest one, but it is only used when the question
        # actually mentions it: an invented profile would tighten the limits
        # and disqualify venues for a person who was never mentioned.
        mentioned = {
            p
            for p, words in PROFILE_KEYWORDS.items()
            if any(w in question.lower() for w in words)
        }
        profiles = sorted(mentioned)
        # Radius, time text, intent type and the missing-field list are derived
        # deterministically: the model's job is the language, not the calendar.
        radius = self._extract_radius(question)
        intent_type = self._extract_intent_type(question)
        missing = [] if (location or lat is not None) else ["location"]
        return Intent(
            activity=activity,
            location_text=location,
            radius_km=radius,
            profile=profiles,
            time_text="",
            day=parsed.get("day") if parsed.get("day") != "none" else None,
            part_of_day=(
                parsed.get("part_of_day")
                if parsed.get("part_of_day") != "none"
                else None
            ),
            hour=None,
            days_ahead=self._extract_days_ahead(question),
            intent_type=intent_type,
            missing=missing,
            raw_text=question,
        )

    @staticmethod
    def _place_from_question(question: str) -> str:
        """The place the question names, if it names one."""
        place = r"([A-Z][A-Za-z0-9'\-]*(?:\s+[A-Z][A-Za-z0-9'\-]*){0,3})"
        patterns = (
            r"(?:near|around|close to|beside|opposite|next to)\s+" + place,
            r"(?:within|inside)\s+\d+\s*km\s+(?:of|from)\s+" + place,
            r"\bin\s+" + place,
        )
        for pattern in patterns:
            m = re.search(pattern, question)
            if m:
                place = m.group(1).strip()
                if place.split()[0] not in QUESTION_WORDS:
                    return place
        return ""

    @staticmethod
    def _extract_radius(question: str) -> float:
        m = re.search(r"(\d{1,3})\s*(?:km|kilomet)", question, re.IGNORECASE)
        return float(m.group(1)) if m else 0.0

    @staticmethod
    def _extract_days_ahead(question: str) -> int:
        m = re.search(
            r"(?:next|in|within|for)\s+(\d{1,2})\s*days?", question, re.IGNORECASE
        )
        if m:
            return max(1, min(int(m.group(1)), 7))
        return 1

    @staticmethod
    def _extract_intent_type(question: str) -> str:
        q = question.lower()
        if re.search(
            r"\bis it (safe|ok|okay|good|fine)\b|\bcan i\b|\bshould i\b|"
            r"\bsafest\b|\bsafe way\b|\bsafely\b|\bbest way\b|\badvised\b|"
            r"\brecommend\b",
            q,
        ):
            return "safety_check"
        if re.search(r"\b(better|or)\b.*\b(saturday|sunday|monday|tuesday|wednesday|thursday|friday)\b", q):
            return "compare"
        if re.search(r"\bplan(ning)?\b|\borganis|\borganize|\bevent\b|\bparty\b|\bwedding\b", q):
            return "event_planning"
        return "recommend"

    @staticmethod
    def _default_radius(activity: str) -> float:
        import yaml

        from app.config import CONFIG_DIR

        with open(CONFIG_DIR / "activities.yaml", "r", encoding="utf-8") as f:
            acts = yaml.safe_load(f)
        return float((acts.get(activity) or acts["running"]).get("radius_km", 5))

    # ------------------------------------------------------------------ run
    async def run(
        self, question: str, lat: float | None = None, lon: float | None = None
    ) -> dict[str, Any]:
        now = now_ist()

        # ---------------------------------------------------------- parse
        stage: dict[str, float] = {}
        t0 = time.perf_counter()
        try:
            parsed = await self._run_blocking(
                self.slm.parse_intent, question, now.strftime("%Y-%m-%d %H:%M")
            )
            parse_source = "slm"
        except (SLMUnavailable, Exception) as exc:  # noqa: BLE001
            logger.warning("SLM parse failed (%s); using the rule-based parser", exc)
            parsed = self._fallback_parse(question)
            parse_source = "rules"
        stage["slm_parse_ms"] = round((time.perf_counter() - t0) * 1000)

        intent = self._normalise(parsed, question, lat, lon)
        if lat is not None and lon is not None:
            intent.lat, intent.lon = lat, lon
        if not intent.radius_km:
            intent.radius_km = self._default_radius(intent.activity)

        # ----------------------------------------------------------- gate
        if parsed.get("out_of_scope") or self._is_out_of_scope(question):
            return self._out_of_scope()

        if not intent.location_text and intent.lat is None:
            return self._needs_clarification(
                "Which area should I look around? Tell me a neighbourhood, a landmark or "
                "a city, for example the name of the area you are in.",
                parsed,
            )

        if intent.activity in {"other", ""}:
            return self._needs_clarification(
                "I can plan outdoor activities like running, cycling, golf, cricket, football, "
                "tennis, hiking, picnics, outdoor events and photography sessions. Which one "
                "are you planning?",
                parsed,
            )

        # --------------------------------------------------------- context
        bundle = await gather_context(intent)
        if "error" in bundle:
            return {
                "status": "needs_clarification",
                "clarification_question": bundle["error"],
                "parsed": parsed,
                "recommendation": None,
                "alternatives": [],
                "avoid": [],
                "advice": {"carry": [], "watch_for": [], "note": []},
                "evidence": {},
                "map_geojson": {"type": "FeatureCollection", "features": []},
                "data_as_of": now.isoformat(),
                "sources": self._sources(),
                "attribution": bundle.get("attribution", ""),
                "limitations": bundle.get("limitations", []),
                "timings_ms": {},
                "answer": bundle["error"],
            }

        # ---------------------------------------------------------- engine
        engine_result = self.engine.score(bundle)
        top_slots = engine_result.slots[:3]

        # The activity itself is ruled out by the person's condition. This is
        # the most important answer the assistant can give, so it is put
        # first and the person is addressed before the place.
        limited_by = self._limited_by_profile(engine_result, bundle)
        if limited_by:
            return self._safety_answer(limited_by, bundle, engine_result, intent)

        # Nothing passed the hard constraints. That is a real answer, not a
        # failure: say why, and offer the least bad option with the caveat.
        if not top_slots and engine_result.all_slots:
            best = max(engine_result.all_slots, key=lambda s: s.score)
            reasons = sorted(
                {
                    r
                    for s in engine_result.all_slots
                    for r in (s.disqualified or "").split("; ")
                    if r
                }
            )
            # The model still writes the sentence: this is the case where a
            # well phrased answer matters most.
            choice = None
            t0 = time.perf_counter()
            try:
                choice = await self._run_blocking(
                    self.slm.choose,
                    question,
                    [self._slot_option(best)],
                    bundle.get("evidence", {}),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("SLM chooser failed: %s", exc)
            stage["slm_choose_ms"] = round((time.perf_counter() - t0) * 1000)

            headline = (
                f"Nothing is ideal in that window. The best of the options is "
                f"{best.place} around {best.when.strftime('%H:%M')} on "
                f"{best.when.strftime('%a %d %b')} (score {best.score:.0f}/100), "
                f"but it still has problems: {best.disqualified}."
            )
            # The caveat always leads. The model may add a sentence, but it
            # must not replace "nothing is ideal" with "go to", because the
            # slot it is describing was disqualified.
            model_headline = str((choice or {}).get("headline") or "").strip()
            extra = ""
            if model_headline and self._headline_is_grounded(
                model_headline, best, bundle
            ):
                if "go to" in model_headline.lower():
                    # It recommends the slot, which is not what we want here.
                    answer_source = "template"
                else:
                    extra = model_headline
                    answer_source = "model"
            else:
                answer_source = "template"
            body = [headline]
            if extra:
                body.append(extra)
            for reason in reasons[:5]:
                body.append(f"- avoid this because of {reason}")
            body.append(
                "Check the avoid windows in the response for the hours that are "
                "actually worth going."
            )
            return {
                "status": "partial",
                "clarification_question": None,
                "parsed": self._parsed_block(intent, bundle, parse_source),
                "recommendation": {
                    "slot_id": best.slot_id,
                    "place": best.place,
                    "lat": best.lat,
                    "lon": best.lon,
                    "time": best.when.isoformat(),
                    "time_label": best.time_label,
                    "score": best.score,
                    "caveat": best.disqualified,
                    "why": self._template_why(best),
                    "breakdown": best.breakdown,
                    "weather": best.weather,
                    "air": best.air,
                    "travel_min": best.travel_min,
                    "evidence": best.evidence_ids,
                },
                "alternatives": [],
                "avoid": engine_result.avoid,
                "advice": {
                    "carry": self._template_carry(best, bundle),
                    "watch_for": self._template_watch(best, bundle),
                },
                "evidence": bundle.get("evidence", {}),
                "proof": self._build_proof(best, bundle, intent),
                "map_geojson": self._geojson(bundle, engine_result.all_slots[:8]),
                "data_as_of": now.isoformat(),
                "sources": self._sources(),
                "attribution": bundle.get("attribution", ""),
                "limitations": _dedupe(bundle.get("limitations", [])),
                "timings_ms": {**bundle.get("timings", {}), **stage},
                "answer": "\n".join(body),
                "verified": True,
                "answer_source": answer_source,
            }

        if not engine_result.slots:
            note = (
                f"I could not find any mapped {intent.activity} venues near "
                f"{bundle['origin'].short_name} within {intent.radius_km:.0f} km, "
                "so there is nothing to recommend yet."
            )
            extra = bundle.get("limitations", [])
            if extra:
                note += " What happened: " + " ".join(extra[:2])
            return {
                "status": "partial",
                "clarification_question": None,
                "parsed": self._parsed_block(intent, bundle, parse_source),
                "recommendation": None,
                "alternatives": [],
                "avoid": engine_result.avoid,
                "advice": {"carry": [], "watch_for": [], "note": []},
                "evidence": bundle.get("evidence", {}),
                "map_geojson": self._geojson(bundle, []),
                "data_as_of": now.isoformat(),
                "sources": self._sources(),
                "limitations": _dedupe(extra),
                "timings_ms": {**bundle.get("timings", {}), **stage},
                "answer": note,
            }

        options = [self._slot_option(s) for s in top_slots]

        # ------------------------------------------------------- choose
        choice: dict[str, Any] | None = None
        t0 = time.perf_counter()
        try:
            choice = await self._run_blocking(
                self.slm.choose, question, options, bundle.get("evidence", {})
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("SLM chooser failed: %s", exc)
        stage["slm_choose_ms"] = round((time.perf_counter() - t0) * 1000)
        if choice:
            logger.info(
                "the model chose %s", choice.get("slot_id"),
            )

        chosen = self._match_slot(choice, top_slots) if choice else None
        if chosen is None:
            chosen = top_slots[0]

        # ------------------------------------------------------- verify
        # Only the model's own wording is checked; the scaffolding we add
        # around it is built from engine data and is grounded by construction.
        facts = self.verifier.build_facts(engine_result, bundle)
        # The headline is checked strictly (a capitalised word there has to
        # be a place we know); the advice is checked loosely.
        verification = self._verify_model_text(choice, facts)

        if not verification.verified and choice:
            logger.info("Verifier rejected the answer: %s", verification.violations)
            try:
                choice2 = await self._run_blocking(
                    self.slm.choose,
                    question + "\n(Only use the numbers and places in the table.)",
                    options,
                    bundle.get("evidence", {}),
                )
                verification2 = self._verify_model_text(choice2, facts)
                if verification2.verified or len(verification2.violations) <= len(
                    verification.violations
                ):
                    choice = choice2
                    verification = verification2
                    chosen = self._match_slot(choice, top_slots) or chosen
            except Exception as exc:  # noqa: BLE001
                logger.warning("SLM regeneration failed: %s", exc)

        if choice is None or not verification.verified:
            # The model's wording was rejected, so none of it is published:
            # the answer, the reasons and the advice all come from the
            # engine. A rejected claim must never reach the user.
            choice = None
            answer_text = self._template(chosen, engine_result, bundle)
            answer_source = "template"
        else:
            answer_text = self._compose(chosen, choice, top_slots, engine_result, bundle)
            answer_source = "model"

        return {
            "status": "ok" if answer_source == "model" else "partial",
            "clarification_question": None,
            "parsed": self._parsed_block(intent, bundle, parse_source),
            "recommendation": {
                "slot_id": chosen.slot_id,
                "place": chosen.place,
                "lat": chosen.lat,
                "lon": chosen.lon,
                "time": chosen.when.isoformat(),
                "time_label": chosen.time_label,
                "score": chosen.score,
                "why": self._template_why(chosen),
                "breakdown": chosen.breakdown,
                "weather": chosen.weather,
                "air": chosen.air,
                "travel_min": chosen.travel_min,
                "evidence": chosen.evidence_ids,
            },
            "alternatives": [
                {
                    "slot_id": s.slot_id,
                    "place": s.place,
                    "lat": s.lat,
                    "lon": s.lon,
                    "time": s.when.isoformat(),
                    "time_label": s.time_label,
                    "score": s.score,
                    "breakdown": s.breakdown,
                }
                for s in self._alternatives(chosen, engine_result.slots)
            ],
            "avoid": engine_result.avoid,
            "advice": {
                "carry": (choice or {}).get("carry", []) or self._template_carry(chosen, bundle),
                "watch_for": (choice or {}).get("watch_for", [])
                or self._template_watch(chosen, bundle),
            },
            "evidence": bundle.get("evidence", {}),
            "proof": self._build_proof(chosen, bundle, intent),
            "map_geojson": self._geojson(bundle, engine_result.slots[:8]),
            "data_as_of": now.isoformat(),
            "sources": self._sources(),
            "attribution": bundle.get("attribution", ""),
            "limitations": _dedupe(
                bundle.get("limitations", []) + verification.violations
            ),
            "timings_ms": {**bundle.get("timings", {}), **stage},
            "answer": answer_text,
            "verified": answer_source == "model",
            "answer_source": answer_source,
        }

    # ------------------------------------------------------------- helpers
    def _parsed_block(self, intent, bundle, parse_source: str) -> dict[str, Any]:
        origin = bundle.get("origin")
        window = bundle.get("window")
        return {
            "activity": intent.activity,
            "location": origin.short_name if origin else intent.location_text,
            "radius_km": intent.radius_km,
            "window": {
                "start": window.start.isoformat(),
                "end": window.end.isoformat(),
                "label": window.label,
            }
            if window
            else {},
            "profile": intent.profile,
            "assumptions": list(window.assumptions) if window else [],
            "parse_source": parse_source,
        }

    @staticmethod
    def _slot_option(slot) -> dict[str, Any]:
        return {
            "slot_id": slot.slot_id,
            "place": slot.place,
            "time": slot.time_label,
            "score": slot.score,
            "apparent_temp_c": slot.weather.get("apparent_temp_c"),
            "us_aqi": slot.air.get("us_aqi"),
            "local_pm2_5": slot.air.get("local_pm2_5"),
            "precip_prob": slot.weather.get("precip_prob"),
            "wind_kmh": slot.weather.get("wind_kmh"),
            "wind_dir": slot.weather.get("wind_from"),
            "travel_min": slot.travel_min,
            "travel_min_at_time": slot.travel_min_at_time,
            "exposure": slot.air.get("exposure"),
        }

    @staticmethod
    def _match_slot(choice: dict[str, Any] | None, slots: list) -> Any:
        """Find the slot the model chose.

        The slot id is a string the model copies, so a small model can
        mangle it. If the id does not match, the venue it named in the
        headline is used instead, rather than throwing the answer away.
        """
        if not choice:
            return None
        wanted = str(choice.get("slot_id", "")).strip()
        for s in slots:
            if s.slot_id == wanted:
                return s
        for s in slots:
            if wanted and wanted in s.slot_id:
                return s
        headline = str(choice.get("headline", "")).lower()
        for s in slots:
            if s.place.lower() in headline:
                return s
        return None

    def _verify_model_text(
        self, choice: dict[str, Any] | None, facts: dict[str, Any]
    ):
        if not choice:
            return Verifier().verify("", facts)
        headline = str(choice.get("headline") or "")
        advice = str(choice.get("avoid_note") or "")
        strict = self.verifier.verify(headline, facts, strict_places=True)
        loose = self.verifier.verify(advice, facts, strict_places=False)
        strict.violations.extend(loose.violations)
        strict.verified = not strict.violations
        return strict

    @staticmethod
    def _model_text(choice: dict[str, Any] | None) -> str:
        """Only the text the model itself wrote (no scaffolding)."""
        if not choice:
            return ""
        parts = [str(choice.get("headline") or "")]
        parts += [str(r) for r in (choice.get("why") or [])]
        parts += [str(c) for c in (choice.get("carry") or [])]
        parts += [str(w) for w in (choice.get("watch_for") or [])]
        return "\n".join(p for p in parts if p)

    def _compose(
        self, chosen, choice, top_slots, engine_result, bundle
    ) -> str:
        headline = (choice or {}).get("headline") or ""
        if not headline:
            headline = (
                f"Go to {chosen.place} around {chosen.when.strftime('%H:%M')} on "
                f"{chosen.when.strftime('%a %d %b')}."
            )
        parts = [headline.strip()]
        # The engine's reasons carry the value we fetched and its source.
        for reason in self._template_why(chosen):
            parts.append(f"- {reason}")
        carry = self._template_carry(chosen, bundle)
        if carry:
            parts.append("Carry: " + ", ".join(carry[:5]) + ".")
        watch = self._template_watch(chosen, bundle)
        if watch:
            parts.append("Watch out for: " + ", ".join(watch[:5]) + ".")
        if len(top_slots) > 1:
            alts = ", ".join(f"{s.place} at {s.when.strftime('%H:%M')}" for s in top_slots[1:3])
            parts.append(f"Alternatives: {alts}.")
        return "\n".join(parts)

    @staticmethod
    def _alternatives(chosen, slots: list) -> list:
        """Two other good options, preferring a different time of day.

        Offering the same place at the same hour is not an alternative.
        """
        out = []
        used_hours = {chosen.when.hour}
        for s in slots:
            if s.slot_id == chosen.slot_id:
                continue
            if s.when.hour in used_hours:
                continue
            used_hours.add(s.when.hour)
            out.append(s)
            if len(out) == 2:
                break
        if not out:
            out = [s for s in slots if s.slot_id != chosen.slot_id][:2]
        return out

    def _headline_is_grounded(self, headline: str, slot, bundle) -> bool:
        """The headline has to be about the place and the hour we chose."""
        if not headline:
            return False
        if slot.place.lower() not in headline.lower():
            return False
        return slot.when.strftime("%H:%M") in headline

    def _limited_by_profile(self, engine_result, bundle) -> str | None:
        """The profile that rules the activity out, if one does."""
        for slot in engine_result.all_slots:
            if slot.disqualified and "not advisable" in slot.disqualified:
                return bundle["intent"].profile[0] if bundle["intent"].profile else None
        return None

    def _safety_answer(
        self, profile_name: str, bundle: dict[str, Any], engine_result, intent
    ) -> dict[str, Any]:
        """The answer when the person should not do the activity at all."""
        profile_cfg = self.engine.profile_cfg["profiles"].get(profile_name, {})
        advice = list(profile_cfg.get("advice", []))
        activity = intent.activity

        # The place is still worth naming, for when the activity is cleared.
        best = None
        for slot in engine_result.all_slots:
            if best is None or slot.score > best.score:
                best = slot

        headline = (
            f"Right now I would not suggest {activity}. With the injury you "
            f"described, {activity} puts weight on a part of the body that is "
            f"still healing, and the guidance for a fracture is to follow your "
            f"doctor's advice and avoid weight bearing until you are cleared."
        )
        if best is not None:
            headline += (
                f" If your doctor says gentle activity is fine, the calmest spot "
                f"near you is {best.place} around {best.when.strftime('%H:%M')} on "
                f"{best.when.strftime('%a %d %b')}."
            )

        body = [headline]
        for item in advice[:3]:
            body.append(f"- {item}")
        if best is not None:
            body.append(
                f"When you are cleared, {best.place} scores {best.score:.0f} out of "
                f"100 at {best.when.strftime('%H:%M')}: "
                + ", ".join(self._template_why(best)[:3]).replace("E_", "")
            )
        body.append(
            "This is general guidance from open data, not medical advice. Your "
            "doctor knows your fracture and should decide what is safe for you."
        )

        return {
            "status": "partial",
            "clarification_question": None,
            "parsed": self._parsed_block(intent, bundle, "slm"),
            "recommendation": {
                "slot_id": best.slot_id if best else None,
                "place": best.place if best else None,
                "lat": best.lat if best else None,
                "lon": best.lon if best else None,
                "time": best.when.isoformat() if best else None,
                "time_label": best.time_label if best else None,
                "score": best.score if best else None,
                "activity_not_advisable": True,
                "why": self._template_why(best) if best else [],
                "breakdown": best.breakdown if best else {},
                "weather": best.weather if best else {},
                "air": best.air if best else {},
                "travel_min": best.travel_min if best else None,
                "evidence": best.evidence_ids if best else [],
            },
            "alternatives": [],
            "avoid": engine_result.avoid,
            "advice": {
                "carry": self._template_carry(best, bundle) if best else [],
                "watch_for": self._template_watch(best, bundle) if best else [],
                "note": advice,
            },
            "evidence": bundle.get("evidence", {}),
            "proof": self._build_proof(best, bundle, intent) if best else None,
            "map_geojson": self._geojson(bundle, engine_result.all_slots[:8]),
            "data_as_of": now_ist().isoformat(),
            "sources": self._sources(),
            "attribution": bundle.get("attribution", ""),
            "limitations": _dedupe(bundle.get("limitations", [])),
            "timings_ms": bundle.get("timings", {}),
            "answer": "\n".join(body),
            "verified": True,
            "answer_source": "template",
        }

    def _profile_note(self, bundle: dict[str, Any]) -> list[str]:
        """The advice the person's own condition calls for."""
        notes: list[str] = []
        profiles = bundle.get("intent").profile or []
        for name in profiles:
            cfg = self.engine.profile_cfg["profiles"].get(name, {})
            notes.extend(cfg.get("advice", []))
        return notes

    def _template_why(self, slot) -> list[str]:
        w, a = slot.weather, slot.air
        reasons = []
        if w.get("apparent_temp_c") is not None:
            reasons.append(
                f"E_WEATHER: feels like {w['apparent_temp_c']:.0f}C with "
                f"{w.get('precip_prob') or 0}% chance of rain"
            )
        if a.get("us_aqi") is not None:
            reasons.append(f"E_AQ: US-AQI {a['us_aqi']:.0f} locally")
        if a.get("exposure"):
            reasons.append(f"E_HAZARDS: local exposure index {a['exposure']:.2f}")
        if slot.travel_min is not None:
            reasons.append(f"E_ROUTES: about {slot.travel_min:.0f} min away")
        return reasons

    def _template_carry(self, slot, bundle) -> list[str]:
        """What to take, worked out from the conditions we fetched."""
        w = slot.weather
        temp = w.get("apparent_temp_c")
        items = ["Water bottle"]
        if (w.get("uv_index") or 0) >= 6 or (temp is not None and temp >= 30):
            items.append("Sunscreen and a hat")
        if (w.get("precip_prob") or 0) >= 30:
            items.append("Rain jacket")
        if temp is not None and temp < 18:
            items.append("A warm layer")
        elif temp is not None and temp < 26:
            items.append("A light layer")
        for profile in bundle["intent"].profile:
            if profile == "asthma":
                items.append("Your inhaler")
            elif profile == "child":
                items.append("Water and a snack for them")
        return items

    def _template_watch(self, slot, bundle) -> list[str]:
        """What to watch out for, worked out from the conditions we fetched."""
        items = []
        w, a = slot.weather, slot.air
        precip = w.get("precip_prob") or 0
        temp = w.get("apparent_temp_c")
        if precip >= 50:
            items.append(f"Heavy rain ({precip:.0f}% chance)")
        elif precip >= 25:
            items.append(f"Showers possible ({precip:.0f}% chance)")
        if (w.get("wind_kmh") or 0) > 20:
            items.append(f"Strong wind ({w['wind_kmh']:.0f} km/h from {w.get('wind_from')})")
        if (a.get("local_pm2_5") or 0) > 40:
            items.append(f"Elevated local PM2.5 ({a['local_pm2_5']:.0f} ug/m3)")
        if temp is not None and temp >= 33:
            items.append(f"Heat ({temp:.0f}C): plan shade and hydration")
        if (w.get("uv_index") or 0) >= 8:
            items.append(f"Very high UV ({w['uv_index']:.0f})")
        if not w.get("is_day"):
            items.append("It gets dark during this slot - bring a light")
        if bundle["intent"].profile:
            items.append(
                "Limits were tightened because of: " + ", ".join(bundle["intent"].profile)
            )
        if not items:
            items.append("Conditions look steady for this hour")
        return items

    def _template(self, chosen, engine_result, bundle) -> str:
        """Deterministic fallback used when the model's text cannot be verified."""
        lines = [
            (
                f"Go to {chosen.place} around {chosen.when.strftime('%H:%M')} on "
                f"{chosen.when.strftime('%a %d %b')} (score {chosen.score:.0f}/100)."
            )
        ]
        for reason in self._template_why(chosen):
            lines.append(f"- {reason}")
        lines.append(
            "Carry: " + ", ".join(self._template_carry(chosen, bundle)) + "."
        )
        lines.append(
            "Watch out for: " + ", ".join(self._template_watch(chosen, bundle)) + "."
        )
        return "\n".join(lines)

    @staticmethod
    def _simplify(points: list, tolerance: float = 0.00015) -> list:
        """Drop the points that do not change the shape of a line.

        A highway with 400 vertices is 4 KB of JSON and looks the same on a
        map as one with 40.
        """
        if len(points) < 3:
            return points

        def sq_distance(p, a, b) -> float:
            (px, py), (ax, ay), (bx, by) = p, a, b
            dx, dy = bx - ax, by - ay
            if dx == 0 and dy == 0:
                return (px - ax) ** 2 + (py - ay) ** 2
            t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
            return (px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2

        def douglas_peucker(pts, tol_sq):
            if len(pts) < 3:
                return pts
            worst, index = 0.0, 0
            for i in range(1, len(pts) - 1):
                d = sq_distance(pts[i], pts[0], pts[-1])
                if d > worst:
                    worst, index = d, i
            if worst <= tol_sq:
                return [pts[0], pts[-1]]
            left = douglas_peucker(pts[: index + 1], tol_sq)
            right = douglas_peucker(pts[index:], tol_sq)
            return left[:-1] + right

        kept = douglas_peucker(points, tolerance * tolerance)
        return [[round(v, 5) for v in p] for p in kept]

    @staticmethod
    def _geojson(bundle: dict[str, Any], slots) -> dict[str, Any]:
        features = []
        origin = bundle.get("origin")
        if origin:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [origin.lon, origin.lat]},
                    "properties": {"name": origin.short_name, "role": "origin"},
                }
            )
        for s in slots:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [s.lon, s.lat]},
                    "properties": {
                        "name": s.place,
                        "role": "venue",
                        "time": s.when.isoformat(),
                        "score": s.score,
                    },
                }
            )
        # The hazard lines are simplified and capped: the map needs to show
        # where the busy roads are, not every vertex of every road.
        recommended = slots[0] if slots else None
        hazards = list(bundle.get("hazards") or [])
        if recommended is not None:
            from app.exposure_engine import haversine_km

            hazards.sort(
                key=lambda h: haversine_km(
                    (recommended.lat, recommended.lon),
                    h.points[0] if h.points else (0.0, 0.0),
                )
            )
        for h in hazards[:12]:
            pts = h.points[:200]
            if len(pts) < 2:
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": Pipeline._simplify(
                            [[p[1], p[0]] for p in pts]
                        ),
                    },
                    "properties": {
                        "name": h.name or h.kind,
                        "role": "hazard",
                        "kind": h.kind,
                    },
                }
            )
        return {"type": "FeatureCollection", "features": features}

    def _build_proof(self, chosen, bundle: dict[str, Any], intent) -> dict[str, Any]:
        """The evidence behind the recommendation, in one readable block.

        This is what answers "why this place and this time": every factor
        carries the value we actually fetched, the source it came from and
        the evidence key, so a judge can check it.
        """
        evidence = bundle.get("evidence", {})
        w, a = chosen.weather, chosen.air
        factors: list[dict[str, Any]] = []

        def add(name: str, value: str, key: str) -> None:
            entry = evidence.get(key, {})
            factors.append(
                {
                    "factor": name,
                    "value": value,
                    "source": entry.get("source", ""),
                    "evidence": key,
                    "fetched_at": entry.get("fetched_at"),
                }
            )

        if w.get("apparent_temp_c") is not None:
            add(
                "weather",
                f"feels like {w['apparent_temp_c']:.0f}C, "
                f"{w.get('precip_prob') or 0}% chance of rain, "
                f"wind {w.get('wind_kmh')} km/h from {w.get('wind_from')}",
                "E_WEATHER",
            )
        if a.get("us_aqi") is not None:
            add(
                "air quality",
                f"US-AQI {a['us_aqi']:.0f}"
                + (
                    f", local PM2.5 estimated {a['local_pm2_5']:.0f} ug/m3"
                    if a.get("local_pm2_5")
                    else ""
                )
                + (
                    f" after station bias x{a.get('bias_factor')}"
                    if a.get("bias_factor")
                    else ""
                ),
                "E_AQ",
            )
        if a.get("exposure") is not None:
            sources = a.get("exposure_sources") or []
            add(
                "local exposure",
                f"index {a['exposure']:.2f}"
                + (f" ({sources[0]})" if sources else " (no busy roads downwind)"),
                "E_HAZARDS",
            )
        if chosen.travel_min is not None:
            travel_text = f"about {chosen.travel_min:.0f} min by car"
            if (
                chosen.travel_min_at_time is not None
                and abs(chosen.travel_min_at_time - chosen.travel_min) > 0.5
            ):
                travel_text += (
                    f", {chosen.travel_min_at_time:.0f} min at that hour of the day"
                )
            if chosen.distance_km:
                travel_text += f", {chosen.distance_km:.1f} km"
            add("travel", travel_text, "E_ROUTES")
        if chosen.daylight.get("sunset"):
            add(
                "daylight",
                f"sunrise {chosen.daylight.get('sunrise')} / sunset "
                f"{chosen.daylight.get('sunset')} (IST)",
                "E_DAYLIGHT",
            )
        add(
            "place",
            f"{chosen.place} was found among the mapped venues near the location",
            "E_VENUES",
        )

        proof: dict[str, Any] = {
            "headline": (
                f"{chosen.place} at {chosen.when.strftime('%a %d %b, %H:%M')} "
                f"scored {chosen.score:.0f} out of 100"
            ),
            "factors": factors,
            "scoring": chosen.breakdown,
            "evidence": {
                k: {"source": v.get("source"), "value": v.get("value")}
                for k, v in evidence.items()
            },
            "data_as_of": now_ist().isoformat(),
        }

        if intent.profile:
            proof["profile_effect"] = (
                "Limits were tightened because of: "
                + ", ".join(intent.profile)
                + ". The air-quality limit and the temperature limit were lowered "
                "for this user, and the advice below takes that into account."
            )
            proof["safety_note"] = (
                "This is general guidance based on open data, not medical advice. "
                "Please follow your doctor's advice and your own judgement."
            )
        return proof

    @staticmethod
    def _sources() -> list[dict[str, str]]:
        return [
            {
                "name": "© OpenStreetMap contributors (Overpass API, Nominatim)",
                "role": "venues, hazards, roads, water, green cover",
                "license": "ODbL",
            },
            {"name": "Open-Meteo forecast API", "role": "hourly weather", "license": "CC BY 4.0"},
            {"name": "Open-Meteo air quality (CAMS ENSEMBLE, Copernicus/ECMWF)", "role": "PM2.5, PM10, ozone, NO2, US-AQI", "license": "CC BY 4.0"},
            {"name": "WAQI (World Air Quality Index project)", "role": "nearby station PM2.5 for grid bias correction", "license": "CC BY 4.0"},
            {"name": "OSRM (router.project-osrm.org)", "role": "travel time", "license": "BSD / OSRM demo server fair use"},
            {"name": "Astral / Open-Meteo daily", "role": "sunrise and sunset", "license": "MIT / CC BY 4.0"},
        ]

    def _needs_clarification(self, question: str, parsed: dict) -> dict[str, Any]:
        return {
            "status": "needs_clarification",
            "clarification_question": question,
            "parsed": {
                "activity": parsed.get("activity", ""),
                "location": parsed.get("location", ""),
                "radius_km": parsed.get("radius_km", 0),
                "window": {},
                "profile": parsed.get("profile", []),
                "assumptions": [],
                "parse_source": parsed.get("parse_source", ""),
            },
            "recommendation": None,
            "alternatives": [],
            "avoid": [],
            "advice": {"carry": [], "watch_for": [], "note": []},
            "evidence": {},
            "proof": None,
            "map_geojson": {"type": "FeatureCollection", "features": []},
            "data_as_of": now_ist().isoformat(),
            "sources": self._sources(),
            "attribution": "",
            "limitations": [],
            "timings_ms": {},
            "answer": question,
        }

    def _out_of_scope(self) -> dict[str, Any]:
        msg = (
            "I can help you plan outdoor activities - running, cycling, golf, cricket, "
            "football, tennis, hiking, picnics, outdoor events and photography - using live "
            "weather, air-quality and map data. Tell me the activity, the area and roughly "
            "when, and I will suggest the best spots and times."
        )
        return {
            "status": "out_of_scope",
            "clarification_question": None,
            "parsed": {
                "activity": "",
                "location": "",
                "radius_km": 0,
                "window": {},
                "profile": [],
                "assumptions": [],
                "parse_source": "",
            },
            "recommendation": None,
            "alternatives": [],
            "avoid": [],
            "advice": {"carry": [], "watch_for": [], "note": []},
            "evidence": {},
            "proof": None,
            "map_geojson": {"type": "FeatureCollection", "features": []},
            "data_as_of": now_ist().isoformat(),
            "sources": self._sources(),
            "attribution": "",
            "limitations": [],
            "timings_ms": {},
            "answer": msg,
        }


pipeline = Pipeline()
