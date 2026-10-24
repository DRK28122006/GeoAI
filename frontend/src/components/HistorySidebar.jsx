import { useState } from "react";
import { getApiBase, setApiBase } from "../api.js";
import { CloseIcon, MenuIcon, PlusIcon } from "./icons.jsx";
import { statusOf } from "./status.js";

const timeFmt = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export default function HistorySidebar({ entries, activeId, open, onToggle, onSelect, onNew, onClear }) {
  const [apiUrl, setApiUrl] = useState(getApiBase);
  const [saved, setSaved] = useState(false);

  function saveApi(e) {
    e.preventDefault();
    setApiBase(apiUrl);
    setApiUrl(getApiBase());
    setSaved(true);
  }

  return (
    <>
      <aside className="side" aria-label="Question history">
        <div className="side__bar">
          <button
            className="icon-btn on-dark"
            onClick={onToggle}
            aria-expanded={open}
            aria-controls="side-panel"
            aria-label={open ? "Close history" : "Open history"}
          >
            {open ? <CloseIcon /> : <MenuIcon />}
          </button>
          <span className="brand">
            Geo<span className="brand__ai">AI</span>
          </span>
          {!open && (
            <button className="icon-btn on-dark side__rail-new" onClick={onNew} aria-label="New question">
              <PlusIcon />
            </button>
          )}
        </div>

        <div className="side__panel" id="side-panel" hidden={!open}>
          <p className="side__tagline">Where and when to head outdoors</p>
          <button className="btn btn--teal side__new" onClick={onNew}>
            <PlusIcon /> New question
          </button>

          <h2 className="side__heading">History</h2>
          {entries.length === 0 ? (
            <p className="side__empty">Questions you ask appear here, with their place and time.</p>
          ) : (
            <ul className="hist">
              {entries.map((e) => {
                const s = statusOf(e);
                const rec = e.response?.recommendation;
                return (
                  <li key={e.id}>
                    <button
                      className="hist__item"
                      aria-current={e.id === activeId ? "true" : undefined}
                      onClick={() => onSelect(e.id)}
                    >
                      <span className={`dot dot--${s.tone}`} title={s.label} />
                      <span className="hist__q">{e.question}</span>
                      <span className="hist__meta">
                        {rec ? `${rec.place}, ${rec.time_label}` : s.label}
                      </span>
                      <span className="hist__time">{timeFmt.format(new Date(e.askedAt))}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          <div className="side__foot">
            {entries.length > 0 && (
              <button className="link-btn" onClick={onClear}>
                Clear history
              </button>
            )}
            <form className="api-form" onSubmit={saveApi}>
              <label htmlFor="api-url">Assistant server</label>
              <div className="api-form__row">
                <input
                  id="api-url"
                  value={apiUrl}
                  onChange={(e) => {
                    setApiUrl(e.target.value);
                    setSaved(false);
                  }}
                  spellCheck={false}
                />
                <button className="btn btn--sm btn--ghost on-dark">{saved ? "Saved" : "Save"}</button>
              </div>
            </form>
          </div>
        </div>
      </aside>
      {/* Tap-outside to close on narrow screens, where the panel overlays the map. */}
      {open && <div className="side__scrim" onClick={onToggle} aria-hidden="true" />}
    </>
  );
}
