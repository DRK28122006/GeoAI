// The one backend call the UI makes. See docs/API.md.

const API_KEY = "geoai.apiBase";
const DEFAULT_API = import.meta.env.VITE_API_URL || "http://127.0.0.1:7860";

// The backend fetches live data from six services, so a question takes
// 20-40 s. docs/API.md asks for a client timeout of at least 60 s.
const TIMEOUT_MS = 90_000;

export function getApiBase() {
  try {
    return localStorage.getItem(API_KEY) || DEFAULT_API;
  } catch {
    return DEFAULT_API;
  }
}

export function setApiBase(url) {
  try {
    if (url.trim()) localStorage.setItem(API_KEY, url.trim().replace(/\/+$/, ""));
    else localStorage.removeItem(API_KEY);
  } catch {
    // Storage blocked (private window): the default URL keeps working.
  }
}

export async function ask(question, coords) {
  const body = { question };
  if (coords) {
    body.lat = coords.lat;
    body.lon = coords.lon;
  }
  let response;
  try {
    response = await fetch(`${getApiBase()}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (err) {
    if (err.name === "TimeoutError") {
      throw new Error("The assistant took longer than 90 seconds. Try again.");
    }
    throw new Error(`Can't reach the assistant at ${getApiBase()}. Check that the backend is running.`);
  }
  if (!response.ok) {
    throw new Error(`The assistant returned an error (HTTP ${response.status}).`);
  }
  return response.json();
}
