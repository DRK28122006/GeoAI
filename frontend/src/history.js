// Past questions live in the browser only: the backend has no history store.

const KEY = "geoai.history";

// Each response carries GeoJSON, and localStorage holds about 5 MB per site.
export const MAX_ENTRIES = 30;

export function loadHistory(storage = globalThis.localStorage) {
  try {
    const parsed = JSON.parse(storage.getItem(KEY) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

// Entries are kept oldest first, so the newest ones survive the cap.
export function saveHistory(entries, storage = globalThis.localStorage) {
  const kept = entries.slice(-MAX_ENTRIES);
  try {
    storage.setItem(KEY, JSON.stringify(kept));
  } catch {
    // Quota exceeded or storage blocked: the session still works, it just won't persist.
  }
  return kept;
}
