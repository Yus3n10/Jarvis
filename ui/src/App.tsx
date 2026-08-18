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

type DriveRow = {
  name: string;
  present: boolean;
  free_bytes: number;
  total_bytes: number;
  free_pct: number;
  pending: number | null;
  reallocated: number | null;
  failing: boolean;
};

type State = {
  type: "state";
  now_utc: string;
  stale: boolean;
  speaking: boolean;
  level: number;
  events: EventRow[];
  drives: DriveRow[];
};

// The socket carries two message shapes: the full state once a second, and a
// bare loudness level 20 times a second so the orb can move with the voice.
type LevelMsg = { type: "level"; level: number; speaking: boolean };

const GIB = 1024 ** 3;

function gib(n: number) {
  return Math.round(n / GIB);
}

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
  const [speaking, setSpeaking] = useState(false);
  const [armedExit, setArmedExit] = useState(false);
  const lastMessageAtRef = useRef<number | null>(null);
  // Written 20x/sec from the socket and read by the orb's own animation loop.
  // Deliberately not React state: see the note in Orb.tsx.
  const levelRef = useRef(0);
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
      const drives: DriveRow[] = [
        {
          name: "movies",
          present: true,
          free_bytes: 197 * GIB,
          total_bytes: 298 * GIB,
          free_pct: 66,
          pending: 784,
          reallocated: 8216,
          failing: true,
        },
      ];
      setConnected(true);
      let talking = false;
      const push = () => {
        lastMessageAtRef.current = Date.now();
        setState({
          type: "state",
          now_utc: new Date().toISOString(),
          stale: false,
          speaking: talking,
          level: 0,
          events,
          drives,
        });
      };
      push();
      // Fake a syllable envelope so the orb can be previewed without audio.
      const anim = setInterval(() => {
        const t = Date.now() / 1000;
        levelRef.current = talking
          ? Math.max(0, Math.sin(t * 9) * 0.5 + Math.sin(t * 5.5) * 0.4 + 0.15)
          : 0;
      }, 50);
      const iv = setInterval(() => {
        talking = !talking;
        setSpeaking(talking);
        push();
      }, 2500);
      return () => {
        clearInterval(iv);
        clearInterval(anim);
      };
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
        const msg: State | LevelMsg = JSON.parse(e.data);
        levelRef.current = msg.level ?? 0;
        // Passing the same value makes React bail out, so the 19-in-20 level
        // messages that change nothing cost no re-render at all.
        setSpeaking(msg.speaking);
        if (msg.type !== "level") setState(msg);
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

  // Two taps to leave, and the arm expires: a stray touch on a wall-mounted
  // panel must not drop the appliance to a desktop nobody is standing at.
  const exitKiosk = () => {
    if (!armedExit) {
      setArmedExit(true);
      setTimeout(() => setArmedExit(false), 5000);
      return;
    }
    setArmedExit(false);
    fetch("/api/kiosk/exit", { method: "POST" }).catch((err) =>
      console.error("kiosk exit failed", err),
    );
  };

  return (
    <div className={`screen${speaking ? " speaking" : ""}`}>
      <Orb level={levelRef} />

      <div className="hud">
        <header>
          <div className="clockwrap">
            <div className="clock">{clockTime(state.now_utc)}</div>
            <div className="date">{today}</div>
          </div>

          <div className="rail">
            {isStale && <div className="stale">NOT UPDATING</div>}

            {state.drives?.map((d) => (
              <div
                key={d.name}
                className={
                  "drive" + (!d.present ? " gone" : d.failing ? " failing" : "")
                }
              >
                <div className="drow">
                  <span className="dname">{d.name}</span>
                  <span className="dfree">
                    {d.present ? `${gib(d.free_bytes)} GB free` : "disconnected"}
                  </span>
                </div>
                {d.present && (
                  <div className="dbar">
                    <div style={{ width: `${Math.max(0, 100 - d.free_pct)}%` }} />
                  </div>
                )}
                {d.present && d.failing && (
                  <span className="dwarn">
                    {d.pending
                      ? `${d.pending} unreadable sectors`
                      : `${d.reallocated} remapped sectors`}
                  </span>
                )}
              </div>
            ))}

            <button
              className={"exit" + (armedExit ? " armed" : "")}
              onClick={exitKiosk}
            >
              {armedExit ? "Tap again to confirm" : "Exit kiosk"}
            </button>
          </div>
        </header>

        <div className="center">
          {speaking && <div className="listening">Jarvis is speaking…</div>}
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
