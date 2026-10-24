import { useEffect, useRef, useState } from "react";
import { ask } from "./api.js";
import { loadHistory, saveHistory } from "./history.js";
import HistorySidebar from "./components/HistorySidebar.jsx";
import MapView from "./components/MapView.jsx";
import AssistantPanel from "./components/AssistantPanel.jsx";

const isNarrow = () => window.matchMedia("(max-width: 900px)").matches;

export default function App() {
  // Oldest first, so the chat thread reads top to bottom.
  const [history, setHistory] = useState(loadHistory);
  const [activeId, setActiveId] = useState(() => history.at(-1)?.id ?? null);
  const [sidebarOpen, setSidebarOpen] = useState(() => !isNarrow());
  const [draft, setDraft] = useState("");
  const [focus, setFocus] = useState(null);
  const inputRef = useRef(null);

  const pending = history.some((e) => e.pending);
  const active = history.find((e) => e.id === activeId) ?? null;

  useEffect(() => {
    saveHistory(history.filter((e) => !e.pending));
  }, [history]);

  const update = (id, patch) =>
    setHistory((h) => h.map((e) => (e.id === id ? { ...e, ...patch } : e)));

  async function submit(question, coords) {
    // randomUUID only exists on https/localhost; plain-http LAN testing falls back.
    const id = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setHistory((h) => [
      ...h,
      { id, question, coords, askedAt: new Date().toISOString(), pending: true, response: null, error: null },
    ]);
    setActiveId(id);
    setFocus(null);
    try {
      const response = await ask(question, coords);
      update(id, { pending: false, response });
      // The backend is stateless, so the user re-asks with the place added.
      if (response.status === "needs_clarification") setDraft(`${question} near `);
    } catch (err) {
      update(id, { pending: false, error: err.message });
    }
  }

  function select(id) {
    setActiveId(id);
    setFocus(null);
    if (isNarrow()) setSidebarOpen(false);
  }

  function startNew() {
    setActiveId(null);
    setFocus(null);
    setDraft("");
    if (isNarrow()) setSidebarOpen(false);
    inputRef.current?.focus();
  }

  function clearHistory() {
    setHistory((h) => h.filter((e) => e.pending));
    setActiveId(null);
    setFocus(null);
  }

  return (
    <div className="app" data-sidebar={sidebarOpen ? "open" : "closed"}>
      <HistorySidebar
        entries={history.filter((e) => !e.pending).toReversed()}
        activeId={activeId}
        open={sidebarOpen}
        onToggle={() => setSidebarOpen((o) => !o)}
        onSelect={select}
        onNew={startNew}
        onClear={clearHistory}
      />
      <MapView response={active?.response} focus={focus} />
      <AssistantPanel
        entries={history}
        activeId={activeId}
        pending={pending}
        draft={draft}
        setDraft={setDraft}
        inputRef={inputRef}
        onSubmit={submit}
        onSelect={setActiveId}
        onFocusPlace={setFocus}
      />
    </div>
  );
}
