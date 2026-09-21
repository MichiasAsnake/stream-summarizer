import { useEffect, useState } from "react";

const API = "http://localhost:8000/api/v1";
const TOKEN = "dev-token-change-me";
const H = { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" };

const clean = (t: string) => t.replaceAll("**", "");

export default function App() {
  const [summary, setSummary] = useState("(loading latest summary...)");
  const [lines, setLines] = useState<string[]>([]);
  const [sessionId, setSessionId] = useState(1);
  const [rolling, setRolling] = useState("");
  const [hasNew, setHasNew] = useState(false);
  const [baseKey, setBaseKey] = useState("");
  const [summaryId, setSummaryId] = useState<number | null>(null);
  const [summaryKind, setSummaryKind] = useState("rolling");
  const [voted, setVoted] = useState<string | null>(null);

  async function statusKey() {
    try {
      const r = await fetch(`${API}/sessions/${sessionId}/recap-status`, { headers: H });
      const j = await r.json();
      return (j.key || "") as string;
    } catch {
      return "";
    }
  }

  async function loadRolling() {
    try {
      const r = await fetch(`${API}/sessions/${sessionId}/summary`, { headers: H });
      const j = await r.json();
      const text = clean(j.text || "");
      if (text) {
        setRolling(text);
        setSummary(text);
        setSummaryId(j.id ?? null);
        setSummaryKind("rolling");
        setVoted(null);
      }
    } catch { /* ignore */ }
  }

  async function recap() {
    const r = await fetch(`${API}/sessions/${sessionId}/recap`, { headers: H });
    const j = await r.json();
    const text = clean(j.text || JSON.stringify(j));
    setSummary(text);
    setSummaryId(j.id ?? null);
    setSummaryKind("recap");
    setVoted(null);
    try {
      const s = await fetch(`${API}/sessions/${sessionId}/summary`, { headers: H });
      const sj = await s.json();
      const latest = clean(sj.text || "");
      if (latest) {
        setRolling(latest);
      }
    } catch {
      /* ignore */
    }
    setBaseKey(await statusKey());
    setHasNew(false);
  }

  useEffect(() => {
    setSummary("(loading latest summary...)");
    setRolling("");
    setHasNew(false);
    setBaseKey("");
    setSummaryId(null);
    setSummaryKind("rolling");
    setVoted(null);
    setLines([]);
    loadRolling();
    statusKey().then(setBaseKey);
  }, [sessionId]);

  useEffect(() => {
    let timer: number | undefined;
    async function poll() {
      try {
        const k = await statusKey();
        if (k && baseKey && k !== baseKey) {
          setHasNew(true);
        }
      } catch { /* ignore */ }
      timer = window.setTimeout(poll, 5000);
    }
    poll();
    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [sessionId, baseKey]);

  async function vote(v: "yes" | "no" | "not_sure") {
    try {
      await fetch(`${API}/sessions/${sessionId}/feedback`, {
        method: "POST",
        headers: H,
        body: JSON.stringify({ summary_id: summaryId, kind: summaryKind, vote: v }),
      });
      setVoted(v);
    } catch { /* ignore */ }
  }

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

  const blinkStyle: React.CSSProperties = {    display: "inline-block",
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

  return (
    <div style={{ fontFamily: "Geist, system-ui, sans-serif", padding: 24, maxWidth: 900 }}>
      <style>{`@keyframes blink { 50% { opacity: 0; } }`}</style>
      <h1>Stream Summarizer</h1>
      <section style={{ background: "#22252c", color: "#f2f3f5", borderRadius: 14, padding: "16px 20px", marginBottom: 16, boxShadow: "0 10px 32px rgba(0,0,0,.5)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span>☰</span>
          <span style={{ fontWeight: 700, fontSize: 17, flex: 1 }}>Stream Summary</span>
        </div>
        <p style={{ fontSize: 15, lineHeight: 1.55, whiteSpace: "pre-wrap" }}>{summary}</p>
        <button onClick={recap}>
          Catch me up
          {hasNew && <span style={blinkStyle}>NEW!</span>}
        </button>
        <p style={{ fontWeight: 700, fontSize: 15 }}>Was this summary easy to understand at a glance?</p>
        <div style={{ display: "flex", gap: 10 }}>
          {(["yes", "no", "not_sure"] as const).map((v) => (
            <button
              key={v}
              disabled={voted !== null}
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
        {lines.map((l, i) => <div key={i} style={{ opacity: 0.9 }}>{l}</div>)}
      </section>
    </div>
  );
}
