import { useEffect, useState } from "react";
import "./App.css";

type EventRow = {
  id: string;
  title: string;
  start_utc: string;
  needs_ack: boolean;
  failed: boolean;
};

type State = {
  now_utc: string;
  stale: boolean;
  events: EventRow[];
};

const PHT = "Asia/Manila";

function clockTime(iso: string) {
  return new Date(iso).toLocaleTimeString("en-PH", {
    timeZone: PHT,
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
}

function countdown(startIso: string, nowIso: string) {
  const mins = Math.round(
    (new Date(startIso).getTime() - new Date(nowIso).getTime()) / 60000,
  );
  if (mins < 0) return "now";
  if (mins < 60) return `in ${mins} min`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `in ${hours}h ${mins % 60}m`;
  return `in ${Math.floor(hours / 24)}d`;
}

export default function App() {
  const [state, setState] = useState<State | null>(null);

  useEffect(() => {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${proto}//${location.host}/ws`);
    socket.onmessage = (e) => setState(JSON.parse(e.data));
    return () => socket.close();
  }, []);

  if (!state) return <div className="screen loading">Connecting…</div>;

  const ack = (id: string) =>
    fetch(`/api/ack/${encodeURIComponent(id)}`, { method: "POST" });

  const today = new Date(state.now_utc).toLocaleDateString("en-PH", {
    timeZone: PHT,
    weekday: "long",
    day: "numeric",
    month: "long",
  });

  return (
    <div className="screen">
      <header>
        <div className="clock">{clockTime(state.now_utc)}</div>
        <div className="date">{today}</div>
        {state.stale && <div className="stale">NOT UPDATING</div>}
      </header>

      {state.events.length === 0 && <div className="empty">Nothing scheduled</div>}

      <ul className="events">
        {state.events.map((e) => (
          <li key={e.id} className={e.needs_ack ? "event urgent" : "event"}>
            <div className="when">
              <span className="at">{clockTime(e.start_utc)}</span>
              <span className="rel">{countdown(e.start_utc, state.now_utc)}</span>
            </div>
            <div className="title">{e.title}</div>
            {e.failed && <div className="failed">missed</div>}
            {e.needs_ack && (
              <button className="ack" onClick={() => ack(e.id)}>
                OK, I heard you
              </button>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
