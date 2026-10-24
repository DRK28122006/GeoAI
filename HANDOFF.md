# Handoff — GeoAI Outdoor Assistant API

## Live endpoint

| | |
|---|---|
| Base URL | **https://85-211-194-173.sslip.io** |
| Swagger UI | https://85-211-194-173.sslip.io/docs |
| OpenAPI JSON | https://85-211-194-173.sslip.io/openapi.json |
| Liveness | https://85-211-194-173.sslip.io/live |
| Readiness | https://85-211-194-173.sslip.io/ready |
| Sources | https://85-211-194-173.sslip.io/sources |
| Public IP | `85.211.194.173` |
| TLS | Let's Encrypt, managed by Caddy, auto-renewing |
| CORS | `*` (all origins) |
| API key | none; the endpoint is public |

`sslip.io` is a free DNS name tied to the static public IP. There is no
DuckDNS account. If the IP changes, the hostname changes too.

---

## The VM

| | |
|---|---|
| Subscription | Azure for Students |
| Resource group | `rg-geoai-malaysia` |
| VM | `geoai-api` |
| Region | Malaysia West |
| Size | `Standard_B2als_v2` — 2 vCPU, 4 GB, x64 |
| OS | Ubuntu Server 24.04 LTS |
| OS disk | 64 GiB Premium_LRS (P6) |
| Swap | 2 GB `/swapfile`, in `/etc/fstab` |
| Public IP | `85.211.194.173` (static) |
| NSG inbound | 22, 80, 443 only; **7860 is closed** |
| Auto-shutdown | Off |

SSH:

```bash
ssh -i geoai-api_key.pem azureuser@85.211.194.173
```

The key is `/home/mesh/Downloads/geoai-api_key.pem` on the team machine.
Share it over a secure channel; never commit it.

---

## What is running

| | |
|---|---|
| App directory | `/home/azureuser/geoai` |
| Docker image | `geoai-outdoor-assistant:latest` (about 3.1 GB) |
| Container | `geoai-api` |
| Internal bind | `127.0.0.1:7860` only |
| Public entry | Caddy on 80/443 → `127.0.0.1:7860` |
| Caddy config | `/etc/caddy/Caddyfile` |
| Model | Qwen3-1.7B Q4_K_M, baked into the image |
| Model threads | 2 (`SLM_N_THREADS=2`) |
| In-flight limit | 2 (`MAX_IN_FLIGHT=2`) |
| Request deadline | 90 s (`REQUEST_DEADLINE_S=90`) |
| Memory limit | 3 GB (`mem_limit: 3g`) |
| Restart policy | `unless-stopped` |
| Boot | Docker and Caddy both enabled at boot |

The app is not exposed on 7860 from the internet. Only Caddy is public.

---

## Frontend contract

```http
POST /ask
Content-Type: application/json

{ "question": "Where can I go running tomorrow evening near Koregaon Park in Pune?" }
```

Optional fields: `lat`, `lon`, `debug`.

- Use a **120-second** client timeout.
- Retry when `status` is **`busy`**.
- Status values: `ok`, `partial`, `needs_clarification`, `out_of_scope`, `busy`.
- A normal answer takes **60–90 seconds** on this 2 vCPU VM.
- Render `answer`, `proof.factors`, `advice`, `alternatives`, `avoid`,
  `map_geojson`, `sources`, `attribution` and `limitations`.
- The `attribution` includes **© OpenStreetMap contributors**.

Minimal JavaScript:

```js
const API = "https://85-211-194-173.sslip.io";

async function ask(question) {
  const response = await fetch(`${API}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!response.ok) throw new Error(`${response.status}`);
  return response.json();
}
```

Full contract: [`docs/API.md`](docs/API.md).

---

## Operations

```bash
cd /home/azureuser/geoai

docker compose ps
docker compose logs -f --tail=100
docker compose restart
docker stats --no-stream

curl -sS https://85-211-194-173.sslip.io/ready
curl -sS https://85-211-194-173.sslip.io/live

sudo systemctl status caddy
sudo journalctl -u caddy -n 50 --no-pager
```

Rebuild after a code change:

```bash
cd /home/azureuser/geoai
docker compose up -d --build
```

`docker compose up -d` alone reuses the existing image.

---

## Updating the code

The VM currently runs the **local tree**, including commit `5a90499`.
The GitHub repo (`Someshwar-prox/GeoAI-v2`) is two commits behind
(`5a90499` and the latest local docs/handoff commit) because the stored GitHub
credential is the exposed token and has not been replaced yet.

Until that commit is pushed:

- Treat `/home/azureuser/geoai` on the VM as the source of truth.
- Copy changes with `rsync`/`scp`, then `docker compose up -d --build`.
- Do not clone `GeoAI-v2` over it expecting the same code.

After the final commit is pushed, a clean deploy is:

```bash
git clone https://github.com/Someshwar-prox/GeoAI-v2.git
cd GeoAI-v2
# put the VM's docker-compose.yml in place
docker compose up -d --build
```

---

## HTTPS and domains

The current certificate is for `85-211-194-173.sslip.io` and is valid until
**21 January 2027**. Caddy renews it automatically.

To use a nicer domain later:

1. Point an `A` record at `85.211.194.173`.
2. Replace the hostname in `/etc/caddy/Caddyfile`.
3. `sudo systemctl reload caddy`.

Caddy will issue a new certificate automatically. Ports 80 and 443 are already
open in the NSG.

---

## Cost and limits

- Compute: **$0.0428/hr ≈ $31.24/month**
- Public IP: about **$3.65/month**
- Disk: 64 GiB P6; check whether the student free disk applies
- Total: roughly **$35/month**
- Credit: **$100**, so about **3 months** of 24/7 runtime
- No card attached; when the credit reaches $0 the subscription disables.
- Set Azure budget alerts at **$20 / $50 / $80** in Cost Management.

The VM has no application-level request rate limit beyond
`MAX_IN_FLIGHT=2`. The external limits are Overpass, Nominatim and WAQI.

---

## Smoke tests already run on this VM

| Test | Result |
|---|---|
| `GET /live` | 200 |
| `GET /ready` | 200, model loaded |
| `GET /sources` | 200 |
| `GET /docs` | 200, Swagger UI |
| `POST /ask` running, Koregaon Park | 200, `ok`, 80 s, answer from model |
| `POST /ask` asthma + cycling, Marine Drive | 200, `partial`, 62 s, profile `asthma` |
| Clarification path | 200, `needs_clarification`, 5.5 s |
| Out-of-scope path | 200, `out_of_scope`, 6.3 s |
| CORS preflight | 200, `access-control-allow-origin: *` |
| `http://85.211.194.173:7860` from outside | timed out (closed) |
| TLS certificate | Let's Encrypt, valid to 21 Jan 2027 |

---

## Known issues

- **Latency:** 60–90 seconds per answer on 2 vCPU. That is why the frontend
  must use a 120-second timeout.
- **Overpass can fail:** one smoke test returned `partial` with
  `overpass_hazards was unavailable`, and the response said so honestly.
- **Memory growth:** the app was previously measured at 2.41 GB long-running.
  The container has a 3 GB limit and 2 GB swap. Current idle is about
  1.22 GB. Monitor `docker stats`; if it OOM-restarts, restart the container
  and investigate.
- **No WAQI token:** responses say the air quality comes from the forecast
  grid. Add `WAQI_TOKEN` to `.env` and restart if a token is available.
- **SSH is open to the internet** on port 22, key-only. Restrict it to known
  IPs after handoff.
- **Exposed GitHub/HF tokens** still need to be revoked.
- **GitHub is two commits behind** the VM tree (`5a90499` plus the latest
  local docs/handoff commit).

---

