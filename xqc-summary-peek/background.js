// Background service worker: the only place that talks to the summarizer API.
// Content scripts message this worker instead of fetching directly, so the
// bearer token never lives in page-accessible context and CORS is a non-issue.

const DEFAULTS = {
  apiBase: "http://localhost:8000",
  apiToken: "", // set via chrome.storage.sync — see README setup step.
};

async function getConfig() {
  return chrome.storage.sync.get(DEFAULTS);
}

async function apiGet(path) {
  const { apiBase, apiToken } = await getConfig();
  if (!apiToken) throw new Error("No API token configured — see README setup step.");
  let res;
  try {
    res = await fetch(`${apiBase}${path}`, {
      headers: { Authorization: `Bearer ${apiToken}` },
    });
  } catch (err) {
    throw new Error(`Cannot reach summarizer at ${apiBase} — check that the API is running.`,
      { cause: err });
  }
  if (res.status === 401) throw new Error("API token rejected — check the extension's token.");
  if (res.status === 404) throw new Error("No stream summary available yet.");
  if (!res.ok) throw new Error(`Summarizer API error (${res.status}) — try again shortly.`);
  return res.json();
}

// Latest rolling update (fast) — also resolves the current live session.
async function fetchUpdateForLogin(login) {
  const session = await apiGet(`/api/v1/channels/${encodeURIComponent(login)}/summary-session`);
  const summary = await apiGet(`/api/v1/sessions/${session.session_id}/summary`);
  return { sessionId: session.session_id, isLive: session.live, rollingId: summary.id,
    text: summary.text || (session.live ? "(no summary yet)" : "No latest update saved for this stream.") };
}

async function fetchFullSummary(sessionId) {
  const summary = await apiGet(`/api/v1/sessions/${sessionId}/full-summary`);
  return { text: summary.text || "(no full summary yet — give it a minute)",
    preview: summary.preview || null };
}

// Re-resolve the live session on every poll, including after a stream restarts.
// A rolling update may arrive without a new event/thread, so track both keys.
async function fetchStatus(login) {
  const session = await apiGet(`/api/v1/channels/${encodeURIComponent(login)}/summary-session`);
  const [st, rolling] = await Promise.all([
    apiGet(`/api/v1/sessions/${session.session_id}/recap-status`),
    apiGet(`/api/v1/sessions/${session.session_id}/summary`),
  ]);
  return { sessionId: session.session_id, isLive: session.live,
    recapKey: st.key || "", fullKey: st.full_key || st.key || "", rollingId: rolling.id };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "FETCH_UPDATE") {
    fetchUpdateForLogin(msg.login)
      .then((d) => sendResponse({ ok: true, ...d }))
      .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
    return true;
  }
  if (msg?.type === "FETCH_FULL_SUMMARY") {
    fetchFullSummary(msg.sessionId)
      .then((summary) => sendResponse({ ok: true, ...summary }))
      .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
    return true;
  }
  if (msg?.type === "FETCH_STATUS") {
    fetchStatus(msg.login)
      .then((status) => sendResponse({ ok: true, ...status }))
      .catch((err) => sendResponse({ ok: false, error: String(err.message || err) }));
    return true;
  }
  return false;
});
