# Implementation Plan: GeoAI Outdoor Assistant Phase 2

## 1. Architecture & File Structure
The goal is to evolve from a simple context gatherer to a reasoning pipeline powered by a local SLM (Qwen3-1.7B).

### New Files
- `app/slm.py`: Interface for `llama-cpp-python`. Handles model loading, prompting, and text generation.
- `app/pipeline.py`: The orchestrator. Coordinates the flow from `question` $\rightarrow$ `SLM parse` $\rightarrow$ `Exposure Engine` $\rightarrow$ `SLM explain` $\rightarrow$ `Verifier`.
- `app/exposure_engine.py`: Logic for scoring venues based on environmental data.
- `app/verifier.py`: Fact-checking logic to ensure SLM responses are grounded in the evidence bundle.
- `configs/exposure_weights.yaml`: Scoring weights for different hazards/mitigators.

## 2. SLM Integration (`app/slm.py`)
Use `llama-cpp-python` for GGUF execution.

### Model Configuration
- Model: `qwen3-1.7b-q4_k_m.gguf`
- Context Window: 4096 tokens.

### Prompt Templates
- **Parsing (Intent Extraction)**:
  `System: You are a geo-spatial parser. Extract activity, location, and user profile from the query. Return JSON.`
  `User: {question}`
  `Output: { "activity": "...", "location": "...", "profile": ["..."] }`
- **Explaining (Response Generation)**:
  `System: You are an outdoor safety expert. Answer the question using ONLY the provided evidence bundle. Cite evidence IDs (e.g., E1, E2).`
  `Context: {evidence_bundle}`
  `User: {question}`
  `Response: ...`

## 3. Geo-Exposure Engine (`app/exposure_engine.py`)
Calculates a score for each venue at specific time slots.

### Logic
$\text{Score} = \sum (\text{Hazard Score} \times \text{Weight}) + \sum (\text{Mitigator Score} \times \text{Weight})$

### Proposed `configs/exposure_weights.yaml`
```yaml
weights:
  air_quality:
    very_poor: -5
    poor: -2
    fair: 0
    good: 1
  temperature:
    extreme_cold: -3
    extreme_heat: -3
    moderate: 1
  weather:
    rain: -2
    storm: -5
    clear: 1
  hazards:
    industrial: -2
    traffic: -1
  mitigators:
    nature: 2
    park: 2
```

## 4. Verification Logic (`app/verifier.py`)
A rule-based scanner to prevent SLM hallucinations.

### Implementation
1. **Entity Extraction**: Use regex to find all numbers, times, and venue names in the generated response.
2. **Cross-Reference**: 
   - Check if the number/time exists in the `Weather`, `Air Quality`, or `Daylight` data.
   - Check if the venue name exists in the `venues` list.
3. **Verdict**: If any entity is missing from the evidence bundle, the verifier flags it as "unverified" and the pipeline can either regenerate or append a warning.

## 5. API Integration (`app/main.py`)
Add a `POST /ask` endpoint.

### Request/Response
- **Request**: `{ "question": "Is it safe to go running in Central Park tomorrow morning?" }`
- **Pipeline Execution**:
  1. `slm.parse(question)` $\rightarrow$ `Intent`
  2. `context.gather_context(intent)` $\rightarrow$ `Bundle`
  3. `exposure_engine.score(bundle)` $\rightarrow$ `Top-K Slots`
  4. `slm.explain(question, bundle, top_slots)` $\rightarrow$ `Draft Response`
  5. `verifier.verify(draft, bundle)` $\rightarrow$ `Final Response`
- **Response**: `{ "answer": "...", "evidence": [...], "verified": true }`

## 6. Verification & Smoke Test
**Test Scenario**: "I have asthma, where should I walk in London tomorrow at 10am?"
- **Success Criteria**:
  - `POST /ask` returns 200 OK.
  - Response cites specific evidence IDs (e.g., E3 for AQI).
  - Response recommends a venue with a high exposure score (nature/park) and low hazard score (low AQI).
  - The verifier confirms all mentioned AQI values match the CAMS/Open-Meteo data.

### Critical Files for Implementation
- `app/slm.py`
- `app/pipeline.py`
- `app/exposure_engine.py`
- `app/verifier.py`
- `app/main.py`
EOF`
