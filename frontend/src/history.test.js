import { describe, expect, it } from "vitest";
import { MAX_ENTRIES, loadHistory, saveHistory } from "./history.js";

function memoryStorage(initial = {}) {
  const data = { ...initial };
  return {
    getItem: (k) => (k in data ? data[k] : null),
    setItem: (k, v) => {
      data[k] = String(v);
    },
  };
}

describe("history", () => {
  it("round-trips entries", () => {
    const storage = memoryStorage();
    saveHistory([{ id: "a", question: "q" }], storage);
    expect(loadHistory(storage)).toEqual([{ id: "a", question: "q" }]);
  });

  it("keeps only the newest MAX_ENTRIES", () => {
    const storage = memoryStorage();
    const entries = Array.from({ length: MAX_ENTRIES + 5 }, (_, i) => ({ id: String(i) }));
    const kept = saveHistory(entries, storage);
    expect(kept).toHaveLength(MAX_ENTRIES);
    expect(kept[0].id).toBe("5");
    expect(loadHistory(storage).at(-1).id).toBe(String(MAX_ENTRIES + 4));
  });

  it("returns an empty list for corrupt or non-array data", () => {
    expect(loadHistory(memoryStorage({ "geoai.history": "{not json" }))).toEqual([]);
    expect(loadHistory(memoryStorage({ "geoai.history": '{"a":1}' }))).toEqual([]);
  });

  it("does not throw when storage is full", () => {
    const full = { getItem: () => null, setItem: () => { throw new Error("QuotaExceeded"); } };
    expect(saveHistory([{ id: "x" }], full)).toEqual([{ id: "x" }]);
  });
});
