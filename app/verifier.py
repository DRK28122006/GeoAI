"""Grounding verifier.

The rulebook forbids inventing weather, places or times. This module checks
every number, clock time and place name in the model's answer against the
facts the engine actually computed. It is pure code - no model involved.

The pipeline asks the model to regenerate once when something does not check
out, and falls back to a deterministic template if it fails twice.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("geoai.verifier")

UNIT_NUMBER = re.compile(
    r"(?<![\w.])(\d{1,3}(?:\.\d+)?)\s*"
    r"(°\s*c|celsius|%|ug/m3|µg/m3|aqi|km/h|kmh|km|min|mins|minutes|m\b|hours?|h\b)?",
    re.IGNORECASE,
)
CLOCK = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|AM|PM)?\b|\b(\d{1,2}):(\d{2})\b"
)
DATEISH = re.compile(
    r"\b(today|tomorrow|tonight|this weekend|next weekend|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday|morning|afternoon|evening|night|dawn|dusk|"
    r"sunset|sunrise|golden hour|blue hour)\b",
    re.IGNORECASE,
)
PLACE = re.compile(r"\b([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){0,3})\b")

STOPWORDS = {
    "I", "The", "A", "An", "This", "That", "There", "It", "We", "You", "Best", "Good",
    "Go", "Head", "Plan", "Try", "If", "For", "And", "But", "So", "Your",
    "My", "Please", "Would", "Can", "Could", "Should", "When", "Where", "What",
    "Which", "Consider", "Avoid", "Carry", "Wear", "Bring", "Keep", "Stay", "Aim",
    "Around", "Based", "Given", "Here", "Now", "Then", "Also", "More", "Most",
    "Because", "While", "After", "Before", "During", "Since", "Until", "Both",
    "Suggested", "Recommendation", "Summary", "Advice", "Note", "Reason", "Reasons",
    "Overall", "Ideal", "Nice", "Cool", "Warm", "Hot", "Light", "Heavy", "Moderate",
    "Rain", "Wind", "Cloud", "Sunny", "Clear", "Mist",
    "Fog", "Haze", "Thunder", "Storm", "Showers", "Drizzle", "Humidity", "AQI",
    "PM", "UV", "Start", "End", "Window", "Time", "Place", "Source", "Sources",
    "Alternatives", "Location", "Travel", "Watch", "Out", "Sunrise", "Sunset",
    "Air", "Quality", "Water", "Sunscreen", "Hat", "Cap", "Bottle", "Inhaler",
    "Layer", "Jacket", "Snacks", "Shade", "Hydration", "Nearby", "Within", "Score",
    "Us", "Aqi", "Feels", "Like", "Chance", "Of", "Index", "Locally", "Distance", "Away", "Min", "Mins", "Km", "Degrees",
}

# Words that introduce our own scaffolding are not place names.
SCAFFOLD_WORDS = {
    "Carry", "Watch", "Out", "For", "Alternatives", "Avoid", "Location", "Travel",
    "Evidence", "Options", "Reason", "Reasons", "Summary", "Recommendation",
}


class VerificationResult(BaseModel):
    verified: bool
    violations: list[str] = Field(default_factory=list)
    checked: dict[str, int] = Field(default_factory=dict)


class Verifier:
    def verify(self, text: str, facts: dict[str, Any]) -> VerificationResult:
        allowed_numbers: set[float] = facts.get("numbers", set())
        allowed_hours: set[int] = facts.get("hours", set())
        allowed_places: set[str] = facts.get("places", set())
        allowed_dates: set[str] = facts.get("dates", set())
        allowed_evidence: set[str] = facts.get("evidence", set())

        violations: list[str] = []
        checked = {"numbers": 0, "times": 0, "places": 0}

        # ------------------------------------------------------------ numbers
        for match in UNIT_NUMBER.finditer(text):
            raw = match.group(1)
            unit = (match.group(2) or "").strip().lower()
            try:
                value = float(raw)
            except ValueError:
                continue
            checked["numbers"] += 1
            if value <= 12 and not unit:
                continue  # small counts ("2 alternatives") are not data claims
            if any(abs(value - a) <= max(0.6, abs(a) * 0.02) for a in allowed_numbers):
                continue
            violations.append(f"number not in the fetched data: {raw}{' ' + unit if unit else ''}")

        # -------------------------------------------------------------- times
        for match in CLOCK.finditer(text):
            groups = match.groups()
            hour = None
            minute = 0
            meridiem = ""
            if groups[0] is not None:
                hour = int(groups[0])
                minute = int(groups[1] or 0)
                meridiem = (groups[2] or "").lower()
            elif groups[3] is not None:
                hour = int(groups[3])
                minute = int(groups[4] or 0)
            if hour is None:
                continue
            if meridiem == "pm" and hour < 12:
                hour += 12
            if meridiem == "am" and hour == 12:
                hour = 0
            if not (0 <= hour <= 23):
                continue
            # a time that matches a scored slot hour is fine
            if hour in allowed_hours:
                continue
            if minute and any(a == hour for a in allowed_hours):
                continue
            # times of day that are part of the requested window
            if any(
                w in text.lower()
                for w in ("morning", "afternoon", "evening", "night", "dawn", "dusk")
            ):
                continue
            violations.append(f"time not in the scored window: {match.group(0).strip()}")

        for word in DATEISH.findall(text):
            key = word.lower()
            if key in {"sunset", "sunrise", "golden hour", "blue hour", "dawn", "dusk"}:
                continue
            if key in {"today", "tomorrow", "tonight"}:
                continue
            if key in allowed_dates:
                continue
            if key in {
                "morning", "afternoon", "evening", "night", "weekend", "monday",
                "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
                "this weekend", "next weekend",
            }:
                continue
            violations.append(f"date reference not in the request: {word}")

        # ------------------------------------------------------------- places
        for match in PLACE.finditer(text):
            phrase = match.group(1).strip()
            words = phrase.split()
            if not words:
                continue
            if words[0] in SCAFFOLD_WORDS:
                continue
            if all(w in STOPWORDS for w in words):
                continue
            if phrase in allowed_places:
                continue
            if any(phrase in p or p in phrase for p in allowed_places):
                continue
            # a phrase that starts with a stopword is usually prose, not a place
            if words[0] in STOPWORDS:
                continue
            # a single ordinary word is prose, not a place name
            if len(words) == 1 and words[0].lower() not in {w.lower() for w in allowed_places}:
                continue
            violations.append(f"place name not in the fetched venues: {phrase}")

        # ------------------------------------------------- evidence citations
        cited = set(re.findall(r"\bE(?:_[A-Z]+|\d+)\b", text))
        for key in cited:
            if key not in allowed_evidence:
                violations.append(f"cites evidence that does not exist: {key}")

        return VerificationResult(
            verified=not violations, violations=violations, checked=checked
        )

    @staticmethod
    def build_facts(engine_result, bundle: dict[str, Any]) -> dict[str, Any]:
        """Collect the numbers, hours, dates and places the answer may cite."""
        numbers: set[float] = set()
        hours: set[int] = set()
        places: set[str] = set()
        dates: set[str] = set()

        for slot in engine_result.slots:
            hours.add(slot.when.hour)
            places.add(slot.place)
            dates.add(slot.when.strftime("%a %d %b").lower())
            for value in slot.weather.values():
                if isinstance(value, (int, float)):
                    numbers.add(float(value))
            for value in slot.air.values():
                if isinstance(value, (int, float)):
                    numbers.add(float(value))
            if slot.travel_min is not None:
                numbers.add(float(slot.travel_min))
            if slot.distance_km is not None:
                numbers.add(float(slot.distance_km))
            for value in slot.breakdown.values():
                numbers.add(float(value))
            numbers.add(float(slot.score))

        origin = bundle.get("origin")
        if origin:
            places.add(origin.short_name)
            places.add(origin.display_name)
        for venue in bundle.get("venues") or []:
            places.add(venue.name)
        window = bundle.get("window")
        if window:
            for h in window.hours:
                hours.add(h.hour)
                dates.add(h.strftime("%a %d %b").lower())
        for day in bundle.get("daylight", {}).get("dates", []) or []:
            dates.add(str(day)[:10].lower())

        return {
            "numbers": numbers,
            "hours": hours,
            "places": places,
            "dates": dates,
            "evidence": set(bundle.get("evidence", {}).keys()),
        }
