// Labels for the five parts of the score (exposure_engine breakdown keys).
const BREAKDOWN = [
  ["comfort", "Comfort"],
  ["air", "Air quality"],
  ["wind_rain", "Wind and rain"],
  ["daylight", "Daylight"],
  ["travel", "Travel"],
];

function List({ title, items }) {
  if (!items?.length) return null;
  return (
    <div className="ac__list">
      <h4>{title}</h4>
      <ul>{items.map((t) => <li key={t}>{t}</li>)}</ul>
    </div>
  );
}

export default function AnswerCard({ response: r, onFocusPlace }) {
  const rec = r.recommendation;
  const factors = r.proof?.factors ?? [];
  const advice = r.advice ?? {};

  return (
    <div className="ac">
      {/* "partial" means the answer carries a caveat (nothing ideal, a health
          note, or no venues found), so the answer text is the caveat itself. */}
      <p className={r.status === "partial" ? "ac__answer note note--pending" : "ac__answer"}>{r.answer}</p>

      {rec && (
        <div className="verdict">
          <p className="verdict__label">{r.status === "partial" ? "Best available, with caveats" : "Best pick"}</p>
          <button
            className="verdict__place"
            onClick={() => onFocusPlace({ lat: rec.lat, lon: rec.lon })}
            title="Show on map"
          >
            {rec.place}
          </button>
          <p className="verdict__when">
            {rec.time_label}
            {rec.travel_min != null && <span>about {Math.round(rec.travel_min)} min away</span>}
          </p>
          <div className="score" aria-label={`Score ${rec.score} out of 100`}>
            <span className="score__num">{Math.round(rec.score)}</span>
            <span className="score__of">/ 100</span>
            <div className="score__track"><div className="score__fill" style={{ "--p": rec.score / 100 }} /></div>
          </div>
          {rec.breakdown && (
            <dl className="parts">
              {BREAKDOWN.filter(([k]) => rec.breakdown[k] != null).map(([k, label]) => (
                <div key={k} className="parts__row">
                  <dt>{label}</dt>
                  <dd>
                    <span className="parts__bar" style={{ "--p": rec.breakdown[k] / 100 }} />
                    {Math.round(rec.breakdown[k])}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}

      {factors.length > 0 && (
        <details className="ac__proof" open>
          <summary>Why this place and time</summary>
          <ul className="proof">
            {factors.map((f) => (
              <li key={f.factor}>
                <span className="proof__factor">{f.factor}</span>
                <span className="proof__value">{f.value}</span>
                <span className="src-tag">{f.source}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {(advice.carry?.length > 0 || advice.watch_for?.length > 0 || advice.note?.length > 0) && (
        <div className="ac__advice">
          <List title="Carry" items={advice.carry} />
          <List title="Watch out for" items={advice.watch_for} />
          <List title="For you" items={advice.note} />
        </div>
      )}

      {r.alternatives?.length > 0 && (
        <div className="ac__section">
          <h4>Other good options</h4>
          <ul className="alts">
            {r.alternatives.map((a) => (
              <li key={a.slot_id || `${a.place}-${a.time_label}`}>
                <button className="alt" onClick={() => onFocusPlace({ lat: a.lat, lon: a.lon })}>
                  <span className="alt__place">{a.place}</span>
                  <span className="alt__when">{a.time_label}</span>
                  <span className="alt__score">{Math.round(a.score)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {r.avoid?.length > 0 && (
        <div className="ac__section">
          <h4>Avoid</h4>
          <ul className="avoid">
            {r.avoid.slice(0, 5).map((a, i) => (
              <li key={i}>
                <strong>{a.window}</strong>
                {a.venue && ` at ${a.venue}`}: {a.reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {r.limitations?.length > 0 && (
        <div className="note note--pending">
          <h4>Caveats</h4>
          <ul>{r.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
        </div>
      )}

      <footer className="ac__foot">
        {r.attribution && <p>{r.attribution}</p>}
        {r.data_as_of && (
          <p>Data as of {new Date(r.data_as_of).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}</p>
        )}
        <details>
          <summary>Technical details</summary>
          <dl className="tech">
            <dt>Answer written by</dt><dd>{r.answer_source === "model" ? "the model" : r.answer_source || "unknown"}</dd>
            {r.request_id && (<><dt>Request ID</dt><dd>{r.request_id}</dd></>)}
            {r.timings_ms?.total_ms != null && (<><dt>Took</dt><dd>{(r.timings_ms.total_ms / 1000).toFixed(1)} s</dd></>)}
          </dl>
        </details>
      </footer>
    </div>
  );
}
