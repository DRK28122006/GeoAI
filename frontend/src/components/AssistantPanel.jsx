import { useEffect, useState } from "react";
import AnswerCard from "./AnswerCard.jsx";
import { LocateIcon, SendIcon } from "./icons.jsx";

// From the example questions in the hackathon problem statement (PS-01).
const EXAMPLES = [
  "Where can I go running tomorrow evening near Koregaon Park in Pune?",
  "Is it safe for someone with asthma to walk near Powai tomorrow morning?",
  "Best time for sunset photos at Bandra today?",
  "Where can I play cricket near Whitefield this Sunday?",
];

// What the backend does for every question. It reports no live progress,
// so these are shown as a list, not as a fake progress bar.
const STEPS = [
  "Finding the place",
  "Fetching weather, air quality and map data",
  "Scoring every place against every hour",
  "Writing the answer",
];

function Working({ since }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const seconds = Math.max(0, Math.round((now - new Date(since)) / 1000));
  return (
    <div className="working" role="status">
      <div className="working__bar" />
      <p className="working__head">
        Checking live conditions <span className="working__time">{seconds}s</span>
      </p>
      <ul className="working__steps">
        {STEPS.map((s) => <li key={s}>{s}</li>)}
      </ul>
      <p className="working__hint">This usually takes 20 to 40 seconds.</p>
    </div>
  );
}

function Reply({ entry, onRetry, onFocusPlace }) {
  if (entry.pending) return <Working since={entry.askedAt} />;
  if (entry.error) {
    return (
      <div className="note note--warning">
        <p>{entry.error}</p>
        <button className="btn btn--sm btn--ghost" onClick={onRetry}>Ask again</button>
      </div>
    );
  }
  const r = entry.response;
  switch (r.status) {
    case "ok":
    case "partial":
      return <AnswerCard response={r} onFocusPlace={onFocusPlace} />;
    case "needs_clarification":
      return (
        <div className="note note--info">
          <p>{r.clarification_question || r.answer}</p>
          <p className="note__sub">Add the area or a landmark to your question and send it again.</p>
        </div>
      );
    case "busy":
      return (
        <div className="note note--pending">
          <p>{r.answer}</p>
          <button className="btn btn--sm btn--ghost" onClick={onRetry}>Ask again</button>
        </div>
      );
    default:
      return <div className="note"><p>{r.answer}</p></div>;
  }
}

export default function AssistantPanel({
  entries, activeId, pending, draft, setDraft, inputRef, onSubmit, onSelect, onFocusPlace,
}) {
  const [coords, setCoords] = useState(null);
  const [locError, setLocError] = useState("");

  // Bring the selected exchange into view (sidebar click or a new question).
  useEffect(() => {
    if (activeId) document.getElementById(`x-${activeId}`)?.scrollIntoView({ block: "start" });
  }, [activeId]);

  function send(text) {
    const q = text.trim();
    if (q.length < 2 || pending) return;
    onSubmit(q, coords);
    setDraft("");
  }

  function locate() {
    if (coords) return setCoords(null);
    setLocError("");
    if (!navigator.geolocation) return setLocError("This browser can't share your location.");
    navigator.geolocation.getCurrentPosition(
      (p) => setCoords({ lat: p.coords.latitude, lon: p.coords.longitude }),
      () => setLocError("Location permission was denied. Name a place in your question instead."),
      { timeout: 10_000 },
    );
  }

  return (
    <section className="chat" aria-label="Assistant">
      <header className="chat__head">
        <h1>Outdoor assistant</h1>
        <p>Live weather, air quality and OpenStreetMap data, scored hour by hour.</p>
      </header>

      <div className="chat__thread">
        {entries.length === 0 && (
          <div className="intro">
            <p className="intro__lead">
              Tell me the activity, the area and roughly when. I'll suggest the best place and time, and show why.
            </p>
            <ul className="examples">
              {EXAMPLES.map((q) => (
                <li key={q}>
                  <button className="example" onClick={() => send(q)} disabled={pending}>{q}</button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {entries.map((e) => (
          <article
            key={e.id}
            id={`x-${e.id}`}
            className="exchange"
            data-active={e.id === activeId || undefined}
            onClick={() => e.id !== activeId && onSelect(e.id)}
          >
            <p className="bubble">{e.question}</p>
            <Reply
              entry={e}
              onRetry={() => onSubmit(e.question, e.coords)}
              onFocusPlace={onFocusPlace}
            />
          </article>
        ))}
      </div>

      <form
        className="composer"
        onSubmit={(ev) => {
          ev.preventDefault();
          send(draft);
        }}
      >
        {(coords || locError) && (
          <p className={`composer__loc${locError ? " composer__loc--err" : ""}`}>
            {locError || "Starting from your current location"}
          </p>
        )}
        <div className="composer__row">
          <button
            type="button"
            className="icon-btn"
            onClick={locate}
            aria-pressed={!!coords}
            aria-label={coords ? "Stop using my location" : "Use my location"}
            title={coords ? "Stop using my location" : "Use my location"}
          >
            <LocateIcon />
          </button>
          <label htmlFor="question" className="sr-only">Your question</label>
          <textarea
            id="question"
            ref={inputRef}
            rows={2}
            value={draft}
            maxLength={2000}
            placeholder="e.g. A quiet morning walk near Powai on Saturday"
            onChange={(ev) => setDraft(ev.target.value)}
            onKeyDown={(ev) => {
              if (ev.key === "Enter" && !ev.shiftKey) {
                ev.preventDefault();
                send(draft);
              }
            }}
          />
          <button className="icon-btn icon-btn--send" disabled={pending || draft.trim().length < 2} aria-label="Ask">
            <SendIcon />
          </button>
        </div>
      </form>
    </section>
  );
}
