# GeoAI Outdoor Assistant

A high-precision backend system that recommends optimal outdoor activities and time windows based on live environmental data, geospatial context, and personalized health profiles.

## 🚀 Live API Endpoint
**URL**: `https://geoai-outdoor-assistant.azurewebsites.net/ask`

**Example Request**:
```bash
curl -X POST https://geoai-outdoor-assistant.azurewebsites.net/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "Best time to go running near Powai tomorrow morning? I have asthma"}'
```

## 🧠 Model Architecture
The system utilizes a Small Language Model (SLM) acting as a decision-maker and narrative generator, strictly grounded in live data to prevent hallucinations.

- **Base Model**: `Qwen/Qwen3-1.7B` (Unmodified)
- **Quantization**: `Q4_K_M` GGUF
- **Inference Engine**: `llama.cpp` via `llama-cpp-python`
- **Constraint**: The model is restricted by a GBNF grammar to ensure valid JSON output and grounded responses.

## 🌐 Data Sources
All data is fetched live at query time to ensure real-time accuracy.

| Source | Role | License |
| :--- | :--- | :--- |
| **OpenStreetMap** | Venues, road networks, and geographic features | © OSM contributors (ODbL) |
| **Open-Meteo** | Hourly weather, air quality (CAMS), and elevation | CC BY 4.0 |
| **WAQI** | Local station bias correction for air quality | CC BY 4.0 |
| **OSRM** | Routing and travel time estimates | BSD / OSRM Demo |

## 🛠️ Installation & Local Execution

### Prerequisites
- Python 3.12+
- `cmake` and `ninja-build` (for `llama-cpp-python` compilation)

### Setup
1. **Clone the repository**:
   ```bash
   git clone https://github.com/Deepesh2006/GeoAI.git
   cd GeoAI
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Download Model Weights**:
   Place the `Qwen3-1.7B-Q4_K_M.gguf` file in the `models/` directory.

4. **Configure Environment**:
   Create a `.env` file with the following keys:
   ```env
   WAQI_TOKEN=your_token_here
   CONTACT_EMAIL=your@email.com
   SLM_MODEL_PATH=models/Qwen3-1.7B-Q4_K_M.gguf
   ```

5. **Run the Server**:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 7860
   ```

The API will be available at `http://localhost:7860/ask`.
