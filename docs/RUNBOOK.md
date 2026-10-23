# GeoAI Outdoor Assistant — Dev Runbook

Everything below was verified on the team machine (Arch Linux, Hyprland, i7-1165G7 / 4C / 8T / 16 GB, Iris Xe iGPU, no CUDA).

---

## 1. Local model runtime

| What | Where | Notes |
|---|---|---|
| Python venv | `.venv/` | CPython **3.12.15** via `uv` (no system Python touched) |
| Inference lib | `llama-cpp-python==0.3.19` | **built from source** — see §1.2 |
| Model weights | `models/Qwen3-1.7B-Q4_K_M.gguf` | 1.1 GB, from `unsloth/Qwen3-1.7B-GGUF` |
| System CLI | `/usr/bin/llama-cli`, `/usr/bin/llama-server` | `pacman -S llama-cpp` (v0.6.0) |
| Vulkan backend | `ggml-vulkan` + `vulkan-intel` | iGPU detected: *Intel Iris Xe (TGL GT2)* |

### 1.1 Why the source build

The prebuilt wheels on the abetlen index are **musl (Alpine)** builds and fail on glibc with
`libc.musl-x86_64.so.1: cannot open shared object file`.
The glibc (manylinux) wheels stop at **0.2.62**, which predates JSON-schema support.
→ build 0.3.19 locally:

```bash
uv venv --python 3.12 .venv
uv pip install cmake ninja
CMAKE_ARGS="-DGGML_NATIVE=ON -DLLAMA_CURL=OFF" \
  uv pip install --no-binary llama-cpp-python "llama-cpp-python==0.3.19"
```

`-DGGML_NATIVE=ON` matters: it builds for **AVX-512** on this CPU.

### 1.2 Measured performance

| Config | prompt (pp256) | generation (tg64) |
|---|---|---|
| **CPU, 4 threads, AVX-512** | **124 t/s** | **14.0 t/s** best for generation |
| Vulkan, full offload (`-ngl 99`) | 319.4 t/s | 11.2 t/s |

→ **Run on CPU.** Vulkan only wins on prefill, and we keep prompts small.
`llama-cpp-python` (in-process) additionally gives us `LlamaGrammar.from_json_schema` for
grammar-constrained JSON, which the HTTP server would only give us via the OpenAI-style
`response_format`.

### 1.3 Model server (optional, local dev only)

```bash
systemctl --user start|stop|status llama-server   # runs on 127.0.0.1:8080
curl -s http://127.0.0.1:8080/health
```

It is started with `--jinja --reasoning-budget 0` so **thinking is off** and the answer is clean JSON.
Useful for the frontend/backend folks to prototype against; the shipped app should use the
in-process binding (no second process to babysit inside Docker).

---

## 2. Qwen3: thinking must be disabled

Qwen3 emits a `<think>…</think>` block by default. That wastes 100+ tokens and breaks JSON parsing.

- `chat_template_kwargs={"enable_thinking": False}` is **not supported** in `llama-cpp-python` 0.3.19.
- The template supports `enable_thinking`, and the official non-thinking trick is to pre-seed an
  **empty think block** in the assistant turn.

**Working recipe (verified — valid JSON, no thinking):**

```python
prompt = (
    f"<|im_start|>system\n{system}<|im_end|>\n"
    f"<|im_start|>user\n{user}<|im_end|>\n"
    f"<|im_start|>assistant\n<think>\n\n</think>\n\n"
)
grammar = LlamaGrammar.from_json_schema(json.dumps(schema))
out = llm.create_completion(prompt=prompt, grammar=grammar, max_tokens=200, stop=["<|im_end|>"])
```

Fallback: `/no_think` in the system message also works (soft switch), but the empty-think-block
version is deterministic.

**Trap:** `llm.create_completion()` has **no** `response_format` parameter — use `grammar=`.
`create_chat_completion()` does accept `response_format={"type":"json_object","schema":…}`.
A custom `chat_handler=Jinja2ChatFormatter(...)` returns a `ChatFormatterResponse` **object**, not a dict.

---

## 3. Live data APIs — the traps (all reproduced and fixed)

| Source | Trap | What works |
|---|---|---|
| **Nominatim** | 403 with a short contact UA | UA must look like a browser: `Mozilla/5.0 (compatible; GeoAI-Hackathon/1.0; +mailto:team@geoai.dev)` |
| **Overpass** | **406 for any UA starting with `Mozilla/`**; 502 "at capacity" on `overpass-api.de` | short honest UA (`GeoAI-Hackathon/1.0 (team@geoai.dev)`), POST the query in the **body**, mirror `https://overpass.openstreetmap.fr/api/interpreter` (**primary**, 4.4 s) → `overpass-api.de` (fallback) |
| Overpass query | `node()` only hides ways (most parks/pitches are ways); `["sport"="*"]` matches the literal `*` | use `nwr(...)` and real tag regexes; `out center tags;` |
| **Photon** | 403 from here | drop it; use Nominatim → Open-Meteo geocoding |
| overpass.osm.ch | 200 but **Switzerland-only** extract (0 results in India) | don't use |
| overpass.kumi.systems / private.coffee | read timeouts | don't use |
| overpass.osm.jp | SSL hostname mismatch | don't use |
| Open-Meteo, OSRM, Hugging Face | fine | yes |

→ **Per-source User-Agent**, not one global UA. Nominatim and Overpass need *opposite* UAs.

---

## 4. What the audits changed

Three independent audits were run against the rulebook. The findings that
changed the code:

* A failed Overpass lookup was cached as an empty success, so the assistant
  could claim a venue was free of traffic when it simply could not check.
* The server side element limit threw away most candidate venues and roads
  before the ranking saw them.
* The model invented a health profile and the answer then tightened the
  limits for a person nobody mentioned. The activity and the place are now
  taken from the question too.
* The verifier read any two digit number as a clock time, so every dated
  headline was rejected and the model's wording never survived.
* A non editable install had no config files and crashed on import.
* The docker image was compiled for the build machine's CPU and would die
  with an illegal instruction on any other host.

## 5. Commands

```bash
cd /home/mesh/Projects/GEOai/deepesh-repo
source .venv/bin/activate

uvicorn app.main:app --host 0.0.0.0 --port 7860 --reload   # API
pytest -q                                               # tests
ruff check .                                            # lint
```
