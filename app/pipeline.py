import logging
import re
from typing import Any
from app.slm import slm
from app.context import gather_context, Intent
from app.exposure_engine import ExposureEngine
from app.verifier import Verifier

logger = logging.getLogger("geoai.pipeline")

class Pipeline:
    def __init__(self):
        self.exposure_engine = ExposureEngine()
        self.verifier = Verifier()

    def _clean_response(self, text: str) -> str:
        # 1. Strip out evidence citations like [E1], [E2], etc.
        text = re.sub(r"\[E\d+\]", "", text)

        # 2. Strip out monologue and Chain-of-Thought
        patterns = [
            r"(?i)The assistant should.*",
            r"(?i)Okay, let's tackle.*",
            r"(?i)Wait, but.*",
            r"(?i)I need to make sure.*",
            r"(?i)Thus, the assistant should.*",
            r"(?i)In conclusion, the assistant.*",
            r"```json.*```",
            r"Answer:.*"
        ]

        cleaned = text
        for p in patterns:
            cleaned = re.split(p, cleaned)[0]

        return cleaned.strip().replace("  ", " ")

    async def run(self, question: str) -> dict[str, Any]:
        logger.info(f"Parsing question: {question}")
        intent = slm.parse_intent(question)

        if intent.activity == "out_of_scope":
            return {
                "answer": "I'm sorry, but I can only help with outdoor activities, location safety, and environmental conditions. Could you please ask a relevant question?",
                "status": "out_of_scope",
                "verified": True
            }

        logger.info(f"Gathering context for intent: {intent}")
        bundle = await gather_context(intent)
        if "error" in bundle:
            return {"error": bundle["error"], "status": "failed"}

        logger.info("Scoring venues and hours...")
        top_slots = self.exposure_engine.score(bundle)

        logger.info("Generating explanation...")
        top_3 = top_slots[:3]
        response_text = slm.explain(question, {**bundle, "top_slots": top_3})

        response_text = self._clean_response(response_text)

        logger.info("Verifying response...")
        verification = self.verifier.verify(response_text, bundle)

        return {
            "answer": response_text,
            "verified": verification.verified,
            "unverified": verification.unverified_entities,
            "evidence": bundle.get("evidence", {}),
            "top_slots": [s.model_dump() for s in top_3],
            "status": "success"
        }

pipeline = Pipeline()