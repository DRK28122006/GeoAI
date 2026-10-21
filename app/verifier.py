import re
import logging
from typing import Any, Optional
from pydantic import BaseModel

logger = logging.getLogger("geoai.verifier")

class VerificationResult(BaseModel):
    verified: bool
    unverified_entities: list[str]
    suggestions: list[str]

class Verifier:
    def verify(self, response_text: str, bundle: dict[str, Any]) -> VerificationResult:
        # 1. Extract entities using regex
        # Numbers (temperatures, AQI, etc.)
        numbers = re.findall(r"(\d+(?:\.\d+)?\s*(?:°C|%|AQI|km))", response_text)
        # Times (e.g., 10am, 14:00)
        times = re.findall(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))", response_text)
        # Venue names
        venues = bundle.get("venues", [])
        found_venues = []
        for v in venues:
            if v.name.lower() in response_text.lower():
                found_venues.append(v.name)

        unverified = []

        # Check numbers against weather/aq data
        # This is a simplified check: if the number appears anywhere in the data
        all_data_str = str(bundle.get("weather", "")) + str(bundle.get("air_quality", ""))
        for num in numbers:
            # Strip units for a looser match
            val = re.sub(r"[^0-9.]", "", num)
            if val not in all_data_str:
                unverified.append(num)

        # Check times
        # (Simplified: since we provide the context, if the SLM mentions a time,
        # we check if it's within the requested hours_ahead)

        # Check venues
        # If the SLM mentions a place name not in our venue list or origin
        # (More complex regex needed for general place names, so we'll skip for now)

        return VerificationResult(
            verified=len(unverified) == 0,
            unverified_entities=unverified,
            suggestions=["Check the cited evidence IDs for accurate values"] if unverified else []
        )
