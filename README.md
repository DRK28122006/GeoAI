# GeoAI Outdoor Assistant

A high-precision backend system that recommends optimal outdoor activities and time windows based on live environmental data, geospatial context, and personalized health profiles.

## 🌐 Live Deployment
- **Web Portal**: `https://geoai-outdoor-assistant.azurewebsites.net/` (Interactive UI)
- **API Endpoint**: `https://geoai-outdoor-assistant.azurewebsites.net/ask`
- **Interactive API Docs**: `https://geoai-outdoor-assistant.azurewebsites.net/docs` (Swagger UI)

### Quick Test
```bash
curl -X POST https://geoai-outdoor-assistant.azurewebsites.net/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "Best time to go running near Powai tomorrow morning? I have asthma"}'
```

## 🧠 Model Registry
The system utilizes a Small Language Model (SLM) that is hosted publicly for transparency and verification.

- **Model Repository**: [som-doe2007/geoai-outdoor-assistant](https://huggingface.co/som-doe2007/geoai-outdoor-assistant)
- **Base Model**: `Qwen/Qwen3-1.7B` (Unmodified)
- **Parameters**: ~2.03B (Total) / 1.72B (Non-embedding)
- **Quantization**: `Q4_K_M` GGUF
- **License**: Apache-2.0

## 🌐 Data Sources
All data is fetched live at query time to ensure real-time accuracy.

| Source | Role | License |
| :--- | :--- | :--- |
| **OpenStreetMap** | Venues, road networks, and geographic features | © OSM contributors (ODbL) |
| **Open-Meteo** | Hourly weather, air quality (CAMS), and elevation | CC BY 4.0 |
| **WAQI** | Local station bias correction for air quality | CC BY 4.0 |
| **OSRM** | Routing and travel time estimates | BSD / OSRM Demo |

## 🛠️ Local Execution
For developers wishing to run the system locally:

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
   Download `Qwen3-1.7B-Q4_K_M.gguf` from the [Model Repository](https://huggingface.co/Deepesh2006/geoai-outdoor-assistant) and place it in the `models/` directory.

4. **Configure Environment**:
   Create a `.env` file:
   ```env
   WAQI_TOKEN=your_token_here
   CONTACT_EMAIL=your@email.com
   SLM_MODEL_PATH=models/Qwen3-1.7B-Q4_K_M.gguf
   ```

5. **Run the Server**:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 7860
   ```
