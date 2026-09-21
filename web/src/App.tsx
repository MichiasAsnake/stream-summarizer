import { useCallback, useEffect, useRef, useState } from "react";

// Relative by default so the UI works behind any host/proxy; Vite proxies
// /api to the backend in dev. Override with VITE_API_BASE when needed.
const API = (import.meta.env.VITE_API_BASE as string | undefined) || "/api/v1";
const TOKEN_KEY = "stream-summary-token";
const MAX_LINES = 500;
const RUNNING = ["starting", "live", "degraded"];

let token = sessionStorage.getItem(TOKEN_KEY) || "";

function askToken(): string {
  token = window.prompt("API bearer token") || "";
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
  return token;
}

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function api<T>(path: string, init: RequestInit = {}, retried = false): Promise<T> {
  if (!token) askToken();
  const r = await fetch(`${API}${path}`, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...init.headers },
  });
  if (r.status === 401 && !retried) {
    askToken();
    return api<T>(path, init, true);
  }
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const j = await r.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch { /* non-JSON error body */ }
    throw new ApiError(r.status, `${r.status}: ${detail}`);
  }
  return r.json() as Promise<T>;
}

type SessionInfo = {
  id: number;
  twitch_login: string;
  source: string;
  status: string;
  title: string | null;
  category: string | null;
  started_at: string | null;
  last_error: string | null;
};
type Segment = { t_start: number; text: string };
type StreamMsg = { type: string; t_start?: number; text?: string; status?: string; stage?: string; error?: string };

const clean = (t: string) => t.replaceAll("**", "");

function fmtTime(sec: number) {
  const s = Math.max(0, Math.floor(sec));
  const h = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Keep a session the user picked; otherwise follow the newest running one. */
function chooseSession(list: SessionInfo[], sid: number | null, userPicked: boolean): number | null {
  const existing = list.find((s) => s.id === sid);
  if (existing && (userPicked || RUNNING.includes(existing.status))) return existing.id;
  return (list.find((s) => RUNNING.includes(s.status)) ?? existing ?? list[0])?.id ?? null;
}

/** SSE over fetch, because EventSource cannot send an Authorization header.
 *  Reconnects with backoff until aborted. */
async function subscribe(sid: number, onMsg: (m: StreamMsg) => void,
                         onState: (connected: boolean) => void, signal: AbortSignal) {
  let backoff = 1000;
  while (!signal.aborted) {
    try {
      if (!token) askToken();
      const r = await fetch(`${API}/sessions/${sid}/stream`, {
        headers: { Authorization: `Bearer ${token}` },
        signal,
      });
      if (r.status === 401) askToken();
      if (!r.ok || !r.body) throw new ApiError(r.status, `stream ${r.status}`);
      onState(true);
      backoff = 1000;
      const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += value;
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const data = frame.split("\n").filter((l) => l.startsWith("data:"))
            .map((l) => l.slice(5).trimStart()).join("\n");
          if (!data) continue; // keepalive comment
          try { onMsg(JSON.parse(data)); } catch { /* malformed frame */ }
        }
      }
    } catch {
      if (signal.aborted) return;
    }
    onState(false);
    await new Promise((res) => setTimeout(res, backoff));
    backoff = Math.min(backoff * 2, 15000);
  }
}

export default function App() {
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [sessionId, setSessionId] = useState<number | null>(null);
  const [summary, setSummary] = useState("");
  const [lines, setLines] = useState<Segment[]>([]);
  const [hasNew, setHasNew] = useState(false);
  const [summaryId, setSummaryId] = useState<number | null>(null);
  const [summaryKind, setSummaryKind] = useState("rolling");
  const [voted, setVoted] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [recapBusy, setRecapBusy] = useState(false);
  const [finalSummary, setFinalSummary] = useState<string | null>(null);
  const userPicked = useRef(false);
  const baseKey = useRef("");
  const transcriptEnd = useRef<HTMLDivElement>(null);

  const current = sessions.find((s) => s.id === sessionId) ?? null;

  const loadSessions = useCallback(async () => {
    try {
      const list = await api<SessionInfo[]>("/sessions?limit=50");
      setSessions(list);
      setSessionId((sid) => chooseSession(list, sid, userPicked.current));
    } catch (e) {
      setError(`Could not load sessions — ${(e as Error).message}`);
    }
  }, []);

  const statusKey = useCallback(async (sid: number) => {
    const j = await api<{ key: string }>(`/sessions/${sid}/recap-status`);
    return j.key || "";
  }, []);

  const checkNew = useCallback(async (sid: number) => {
    try {
      const k = await statusKey(sid);
      if (!baseKey.current) baseKey.current = k;
      else if (k && k !== baseKey.current) setHasNew(true);
    } catch { /* surfaced by other calls */ }
  }, [statusKey]);

  const loadRolling = useCallback(async (sid: number) => {
    try {
      const j = await api<{ id: number | null; text: string }>(`/sessions/${sid}/summary`);
      const text = clean(j.text || "");
      setSummary((prev) => text || (prev.startsWith("(loading") ? "(no summary yet)" : prev));
      if (text) {
        setSummaryId(j.id ?? null);
        setSummaryKind("rolling");
        setVoted(null);
      }
    } catch (e) {
      setError(`Could not load summary — ${(e as Error).message}`);
    }
  }, []);

  async function recap() {
    if (sessionId === null) return;
    setRecapBusy(true);
    try {
      const j = await api<{ id: number | null; text: string }>(`/sessions/${sessionId}/recap`);
      setSummary(clean(j.text || ""));
      setSummaryId(j.id ?? null);
      setSummaryKind("recap");
      setVoted(null);
      baseKey.current = await statusKey(sessionId);
      setHasNew(false);
      setError(null);
    } catch (e) {
      setError(`Catch-up failed — ${(e as Error).message}`);
    } finally {
      setRecapBusy(false);
    }
  }

  async function vote(v: "yes" | "no" | "not_sure") {
    if (sessionId === null) return;
    try {
      await api(`/sessions/${sessionId}/feedback`, {
        method: "POST",
        body: JSON.stringify({ summary_id: summaryId, kind: summaryKind, vote: v }),
      });
      setVoted(v);
    } catch (e) {
      setError(`Vote not saved — ${(e as Error).message}`);
    }
  }

  // Session discovery: initial load plus periodic refresh for new sessions.
  useEffect(() => {
    loadSessions();
    const t = window.setInterval(loadSessions, 15000);
    return () => window.clearInterval(t);
  }, [loadSessions]);

  // Per-session state, transcript backfill and live stream.
  useEffect(() => {
    if (sessionId === null) return;
    const sid = sessionId;
    setSummary("(loading latest summary...)");
    setHasNew(false);
    baseKey.current = "";
    setSummaryId(null);
    setSummaryKind("rolling");
    setVoted(null);
    setLines([]);
    setError(null);
    setFinalSummary(null);
    loadRolling(sid);
    checkNew(sid);
    api<Segment[]>(`/sessions/${sid}/transcript?limit=200`)
      .then((rows) => setLines((live) => [...rows, ...live].slice(-MAX_LINES)))
      .catch((e) => setError(`Could not load transcript — ${(e as Error).message}`));

    const ctrl = new AbortController();
    subscribe(sid, (msg) => {
      if (msg.type === "transcript.segment" && msg.text) {
        setLines((prev) => [...prev, { t_start: msg.t_start ?? 0, text: msg.text! }].slice(-MAX_LINES));
      } else if (msg.type === "summary.updated") {
        loadRolling(sid);
        checkNew(sid);
      } else if (msg.type === "pipeline.error") {
        setError(`Pipeline ${msg.stage ?? ""} error: ${msg.error ?? "unknown"} — retrying automatically`);
      } else if (msg.type === "session.status") {
        loadSessions();
      }
    }, setConnected, ctrl.signal);
    // Slow fallback for the NEW badge in case stream events are missed.
    const poll = window.setInterval(() => checkNew(sid), 30000);
    return () => { ctrl.abort(); window.clearInterval(poll); };
  }, [sessionId, loadRolling, checkNew, loadSessions]);

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ block: "nearest" });
  }, [lines]);

  const pillStyle: React.CSSProperties = {
    fontFamily: "inherit",
    background: "#3a3f47",
    color: "#fff",
    border: 0,
    borderRadius: 999,
    padding: "9px 24px",
    fontSize: 15,
    cursor: "pointer",
  };

  const blinkStyle: React.CSSProperties = {
    display: "inline-block",
    background: "#2563eb",
    color: "#fff",
    fontSize: 11,
    fontWeight: 700,
    borderRadius: 4,
    padding: "2px 6px",
    marginLeft: 8,
    verticalAlign: "middle",
    animation: "blink 1s step-end infinite",
  };

  const live = current !== null && RUNNING.includes(current.status);
  const hasCurrent = current !== null;

  // The wrap-up is written shortly after a session ends; refresh until it exists.
  useEffect(() => {
    if (sessionId === null || live || !hasCurrent || finalSummary) return;
    const sid = sessionId;
    const load = () => api<{ final_summary: string | null }>(`/sessions/${sid}`)
      .then((j) => j.final_summary && setFinalSummary(clean(j.final_summary)))
      .catch(() => {});
    load();
    const t = window.setInterval(load, 30000);
    return () => window.clearInterval(t);
  }, [sessionId, live, hasCurrent, finalSummary]);

  return (
    <div style={{ fontFamily: "Geist, system-ui, sans-serif", padding: 24, maxWidth: 900 }}>
      <style>{`@keyframes blink { 50% { opacity: 0; } }`}</style>
      <h1>Stream Summarizer</h1>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}>
        <label>
          Session{" "}
          <select
            value={sessionId ?? ""}
            onChange={(e) => { userPicked.current = true; setSessionId(Number(e.target.value)); }}
            disabled={sessions.length === 0}
          >
            {sessions.length === 0 && <option value="">No sessions yet</option>}
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>
                #{s.id} · {s.twitch_login} · {s.source} · {s.status}
                {s.started_at ? ` · ${new Date(s.started_at).toLocaleString()}` : ""}
              </option>
            ))}
          </select>
        </label>
        {current && (
          <span style={{ fontSize: 13, color: live ? "#16a34a" : "#6b7280" }}>
            {live ? (connected ? "● Live" : "○ Reconnecting…") : `Session ${current.status}`}
          </span>
        )}
        {current && (current.title || current.category) && (
          <span style={{ fontSize: 13, color: "#374151" }}>
            {current.title}{current.title && current.category ? " · " : ""}
            {current.category && <em>{current.category}</em>}
          </span>
        )}
      </div>
      {(error || current?.last_error) && (
        <div role="alert" style={{ background: "#fef2f2", color: "#991b1b", border: "1px solid #fecaca",
                                   borderRadius: 8, padding: "8px 12px", marginBottom: 12, fontSize: 14 }}>
          {error ?? current?.last_error}
          {error && <button onClick={() => setError(null)} style={{ marginLeft: 12 }}>Dismiss</button>}
        </div>
      )}
      {sessionId === null ? (
        <p>No sessions yet. Start a monitor or replay from the API to see live summaries here.</p>
      ) : (
        <>
          {finalSummary && (
            <section style={{ border: "1px solid #e5e7eb", borderRadius: 14, padding: "12px 20px", marginBottom: 16 }}>
              <h2 style={{ fontSize: 17, margin: "4px 0 8px" }}>Stream wrap-up</h2>
              <p style={{ fontSize: 15, lineHeight: 1.55, whiteSpace: "pre-wrap", margin: 0 }}>{finalSummary}</p>
            </section>
          )}
          <section style={{ background: "#22252c", color: "#f2f3f5", borderRadius: 14, padding: "16px 20px", marginBottom: 16, boxShadow: "0 10px 32px rgba(0,0,0,.5)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span>☰</span>
              <span style={{ fontWeight: 700, fontSize: 17, flex: 1 }}>Stream Summary</span>
            </div>
            <p style={{ fontSize: 15, lineHeight: 1.55, whiteSpace: "pre-wrap" }}>{summary}</p>
            <button onClick={recap} disabled={recapBusy}>
              {recapBusy ? "Catching up…" : "Catch me up"}
              {hasNew && !recapBusy && <span style={blinkStyle}>NEW!</span>}
            </button>
            <p style={{ fontWeight: 700, fontSize: 15 }}>Was this summary easy to understand at a glance?</p>
            <div style={{ display: "flex", gap: 10 }}>
              {(["yes", "no", "not_sure"] as const).map((v) => (
                <button
                  key={v}
                  disabled={voted !== null || summaryId === null}
                  onClick={() => vote(v)}
                  style={{
                    ...pillStyle,
                    ...(voted === v ? { background: "#2563eb" } : {}),
                    ...(voted !== null && voted !== v ? { opacity: 0.45 } : {}),
                  }}
                >
                  {v === "yes" ? "Yes" : v === "no" ? "No" : "Not Sure"}
                </button>
              ))}
            </div>
            <p style={{ color: "#9aa0aa", fontSize: 12.5 }}>
              Generated by AI. May make mistakes.
              {voted !== null && " Thanks — this tunes future recaps."}
            </p>
          </section>
          <section>
            <h2>Live transcript</h2>
            {lines.length === 0 && <p style={{ color: "#6b7280" }}>No transcript yet.</p>}
            <div style={{ maxHeight: 420, overflowY: "auto" }}>
              {lines.map((l, i) => (
                <div key={i} style={{ opacity: 0.9, marginBottom: 4 }}>
                  <span style={{ color: "#6b7280", fontVariantNumeric: "tabular-nums", marginRight: 8 }}>
                    {fmtTime(l.t_start)}
                  </span>
                  {l.text}
                </div>
              ))}
              <div ref={transcriptEnd} />
            </div>
          </section>
        </>
      )}
    </div>
  );
}
