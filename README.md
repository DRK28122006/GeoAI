# GeoAI Outdoor Assistant

A high-precision backend system that recommends optimal outdoor activities and time windows based on live environmental data, geospatial context, and personalized health profiles.

## 🌐 Live Deployment
- **Web Portal**: `https://85-211-194-173.sslip.io/` (Interactive UI)
- **API Endpoint**: `https://85-211-194-173.sslip.io/ask`
- **Interactive API Docs**: `https://85-211-194-173.sslip.io/docs` (Swagger UI)
- **OpenAPI Schema**: `https://85-211-194-173.sslip.io/openapi.json`

### Quick Test
```bash
curl -X POST https://85-211-194-173.sslip.io/ask \
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
   Download `Qwen3-1.7B-Q4_K_M.gguf` from the [Model Repository](https://huggingface.co/som-doe2007/geoai-outdoor-assistant) and place it in the `models/` directory.

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

## 🗺️ Frontend
`frontend/` is a React + Vite chat UI for the `/ask` API: question history on the left, an OpenStreetMap (Leaflet) map in the middle and the assistant on the right. History is kept in the browser's localStorage.

```bash
cd frontend
npm install
npm run dev        # opens on http://localhost:5173
npm test           # history storage tests (vitest)
```

## 📜 Credits & Declarations

### AI Assistance
This project was developed with the assistance of **Claude (Anthropic)** for architectural planning, code optimization, and documentation. All core logic, the Exposure Engine, and the Verification Loop were designed and implemented by the developer.

### External APIs & Data
The system relies on the following external services:
- **OpenStreetMap (Overpass API)**: Venue and geospatial data.
- **Open-Meteo API**: Weather and CAMS Air Quality data.
- **World Air Quality Index (WAQI)**: Ground-station AQI data.
- **OSRM API**: Distance and travel-time routing.

### Model Credit
- **Base Model**: `Qwen3-1.7B` developed by the **Qwen Team (Alibaba Cloud)**.
- **Quantization**: GGUF format via `llama.cpp`.
