import json
import logging
import os
from typing import Any, Optional
from pydantic import BaseModel
from llama_cpp import Llama
from app.config import settings
from app.context import Intent

logger = logging.getLogger("geoai.slm")

class SLM:
    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path or os.getenv("SLM_MODEL_PATH", "models/Qwen3-1.7B-Q4_K_M.gguf")
        self.llm = None

    def _load_model(self):
        if self.llm is None:
            if not os.path.exists(self.model_path):
                logger.error(f"Model file not found at {self.model_path}")
                raise FileNotFoundError(f"SLM model file missing: {self.model_path}")

            logger.info(f"Loading SLM from {self.model_path}...")
            self.llm = Llama(
                model_path=self.model_path,
                n_ctx=4096,
                verbose=False
            )

    def parse_intent(self, question: str) -> Intent:
        self._load_model()

        system_prompt = (
            "You are a geo-spatial parser. Your task is to extract the Intent from a user query. "
            "Supported Activities: 'running' (for walking, jogging, playing, sports), "
            "'hiking' (for trekking, mountain climbing, forest walks), "
            "'photography' (for sightseeing, viewpoints, photos). "
            "Return ONLY a JSON object with these keys: "
            "\"activity\" (must be one of the supported activities), "
            "\"location\" (the city or area), "
            "\"profile\" (list of health conditions, e.g. ['asthma']). "
            "If the query is unrelated to outdoor activities or locations, return { \"out_of_scope\": true }."
        )

        prompt = f"System: {system_prompt}\nUser: {question}\nAssistant: {{"

        output = self.llm(
            prompt,
            max_tokens=256,
            stop=["}"],
            echo=False
        )["choices"][0]["text"]

        try:
            full_json = "{" + output + "}"
            data = json.loads(full_json)

            if data.get("out_of_scope") is True:
                return Intent(activity="out_of_scope", location_text="", profile=[])

            return Intent(
                activity=data.get("activity") or "running",
                location_text=data.get("location") or "unknown",
                profile=data.get("profile") or [],
                radius_km=5.0
            )
        except Exception as e:
            logger.error(f"SLM parse failed: {e}. Falling back to default intent.")
            return Intent(activity="running", location_text=question, profile=[])

    def explain(self, question: str, bundle: dict[str, Any]) -> str:
        self._load_model()

        # Process top slots into a readable format for the SLM
        top_slots = bundle.get("top_slots", [])
        slots_info = []
        for s in top_slots:
            # s is an ExposureScore object
            venue_id = s.venue_id if hasattr(s, "venue_id") else s.get("venue_id")
            venue = next((v for v in bundle.get("venues", []) if v.id == venue_id), None)
            v_name = venue.name if venue else venue_id
            hour = s.hour if hasattr(s, "hour") else s.get("hour")
            score = s.total_score if hasattr(s, "total_score") else s.get("total_score")
            slots_info.append(f"- {v_name} (Hour {hour}: Score {score})")

        slots_str = "\n".join(slots_info) if slots_info else "No ideal slots found."

        slots_str = "\n".join(slots_info) if slots_info else "No ideal slots found."
        evidence_str = json.dumps(bundle.get("evidence", {}), indent=2)

        system_prompt = (
            "You are the GeoAI Outdoor Assistant. Your goal is to provide a simple, clear, "
            "and helpful recommendation for outdoor activities in layman terms. "
            "Follow these strict rules:
"
            "1. Be direct: Tell the user exactly WHERE to go and WHEN it is best. "
            "Example: 'I recommend Community City Park around 2pm.'
"
            "2. Use the Evidence: Base your answer ONLY on the provided evidence. "
            "Translate technical values into natural language (e.g., instead of 'AQI 59', say 'the air quality is good').
"
            "3. NO CITATIONS: Do NOT include evidence IDs like [E1], [E2], or (E1) in the final response. "
            "The user should not see any reference tags.
"
            "4. NO LISTING: Do NOT simply list the evidence. Write a natural, conversational sentence.
"
            "5. NO CHAIN-OF-THOUGHT: Do not include any internal reasoning, 'Wait...', "
            "JSON blocks, or self-corrections. Provide ONLY the final response to the user.
"
            "6. Be Honest: If the data is missing, simply say so gracefully.
"
            "7. Conciseness: Keep the answer under 3 sentences."
        )

        prompt = (
            f"System: {system_prompt}\n"
            f"Evidence Bundle: {evidence_str}\n"
            f"Top Recommended Slots:\n{slots_str}\n"
            f"User Question: {question}\n"
            f"Assistant: "
        )

        output = self.llm(
            prompt,
            max_tokens=256,
            echo=False,
            stop=["System:", "User:", "Assistant:", "\n\n"]
        )["choices"][0]["text"]

        return output.strip()

slm = SLM()
