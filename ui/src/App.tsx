import { useEffect, useRef, useState } from "react";
import "./App.css";
import Orb from "./Orb";

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
  speaking: boolean;
  events: EventRow[];
};

const PHT = "Asia/Manila";

// The backend pushes a payload roughly once a second. If the client hasn't
// heard anything in this long, the socket is effectively dead even though
// nothing told us so directly -- a dead socket can't report its own death,
// so we detect it by silence instead of waiting to be told.
const STALE_AFTER_MS = 5000;
const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 10000;

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
  const [connected, setConnected] = useState(false);
  const lastMessageAtRef = useRef<number | null>(null);
  // Ticking state has no meaning of its own -- it exists only to force a
  // re-render every second so a silent (dead) socket still gets re-checked
  // for staleness even though no message arrives to trigger one.
  const [, setTick] = useState(0);

  useEffect(() => {
    // ?demo drives the UI with fake data (toggling "speaking") so the dashboard
    // and orb can be shown or tested without a live backend or real events.
    if (new URLSearchParams(location.search).has("demo")) {
      const events = [
        {
          id: "a",
          title: "Interview with Lumachain",
          start_utc: new Date(Date.now() + 3600e3).toISOString(),
          needs_ack: true,
          failed: false,
        },
        {
          id: "b",
          title: "Dentist appointment",
          start_utc: new Date(Date.now() + 3 * 3600e3).toISOString(),
          needs_ack: false,
          failed: false,
        },
      ];
      setConnected(true);
      let speaking = false;
      const push = () => {
        lastMessageAtRef.current = Date.now();
        setState({ now_utc: new Date().toISOString(), stale: false, speaking, events });
      };
      push();
      const iv = setInterval(() => {
        speaking = !speaking;
        push();
      }, 2500);
      return () => clearInterval(iv);
    }

    let cancelled = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectDelay = RECONNECT_MIN_MS;

    const connect = () => {
      if (cancelled) return;
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${proto}//${location.host}/ws`);

      socket.onopen = () => {
        if (cancelled) return;
        reconnectDelay = RECONNECT_MIN_MS;
        setConnected(true);
      };

      socket.onmessage = (e) => {
        if (cancelled) return;
        lastMessageAtRef.current = Date.now();
        setState(JSON.parse(e.data));
      };

      socket.onclose = () => {
        if (cancelled) return;
        setConnected(false);
        reconnectTimer = setTimeout(() => {
          reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
          connect();
        }, reconnectDelay);
      };

      socket.onerror = () => {
        // onerror is always followed by onclose, which schedules the
        // reconnect -- just make sure the socket actually closes.
        socket?.close();
      };
    };

    connect();

    const tick = setInterval(() => setTick((t) => t + 1), 1000);

    return () => {
      cancelled = true;
      clearInterval(tick);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (socket) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onclose = null;
        socket.onerror = null;
        socket.close();
      }
    };
  }, []);

  if (!state) return <div className="screen loading">Connecting…</div>;

  const ack = (id: string) =>
    fetch(`/api/ack/${encodeURIComponent(id)}`, { method: "POST" }).catch((err) => {
      // A failed ack POST would otherwise be invisible until the next state
      // push overwrites it -- surface it immediately so it isn't silent.
      console.error("ack request failed", err);
    });

  const today = new Date(state.now_utc).toLocaleDateString("en-PH", {
    timeZone: PHT,
    weekday: "long",
    day: "numeric",
    month: "long",
  });

  const silentTooLong =
    lastMessageAtRef.current === null ||
    Date.now() - lastMessageAtRef.current > STALE_AFTER_MS;
  const isStale = state.stale || !connected || silentTooLong;

  const next = state.events[0];

  return (
    <div className={`screen${state.speaking ? " speaking" : ""}`}>
      <Orb intensity={state.speaking ? 1 : 0} />

      <div className="hud">
        <header>
          <div className="clockwrap">
            <div className="clock">{clockTime(state.now_utc)}</div>
            <div className="date">{today}</div>
          </div>
          {isStale && <div className="stale">NOT UPDATING</div>}
        </header>

        <div className="center">
          {state.speaking && <div className="listening">Jarvis is speaking…</div>}
        </div>

        <footer className="dock">
          {state.events.length === 0 && <div className="empty">Nothing scheduled</div>}
          <ul className="events">
            {state.events.map((e) => (
              <li
                key={e.id}
                className={
                  "event" +
                  (e.needs_ack ? " urgent" : "") +
                  (e === next ? " next" : "")
                }
              >
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
        </footer>
      </div>
    </div>
  );
}
