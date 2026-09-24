// xQc Summary Peek — two modes:
//
// 1. Channel page (twitch.tv/xqc/*): hover-revealed card at the upper-left
//    of the player, kept above its bottom control bar.
// 2. Sidebar hover-preview card (any twitch.tv page): the button is injected
//    card-relative, panel slides open under the card's title row.
//
// Twitch's hover-card markup uses obfuscated/hashed class names that change
// across deploys, so card detection anchors on the one stable thing: a link
// to /xqc paired with "viewers" text. THAT PART NEEDS LIVE VERIFICATION.

// Guard: reloading the extension does not remove already-injected scripts
// from open tabs, so only run when no previous copy is active in this tab.
if (!window.__xqcPeek) {
window.__xqcPeek = true;

const XQC_HREF_RE = /^\/xqc(\/|\?|$)/i;
const ALREADY_MARKED = "data-xqc-peek";
const PAGE_MARKED = "data-xqc-peek-page";

function stylePanel(panel) {
  Object.assign(panel.style, {
    maxHeight: "0px", opacity: "0", overflow: "hidden", overflowY: "auto",
    transition: "max-height 220ms ease, opacity 180ms ease",
    color: "#f5f5f6", fontSize: "13px", lineHeight: "1.5",
    whiteSpace: "pre-wrap", boxSizing: "border-box",
  });
}

function openPanel(panel) {
  panel.style.maxHeight = "320px";
  panel.style.opacity = "1";
}

function closePanel(panel) {
  panel.style.maxHeight = "0px";
  panel.style.opacity = "0";
}

function isOpen(panel) {
  return panel.style.maxHeight !== "0px";
}

// Shared click behavior: show the latest update immediately, then the
// cumulative full summary. Poll both rolling and material-change keys.
const BTN_LABEL = "Summarize";

function sendMsg(msg) {
  return new Promise((resolve) => {
    // Reloading the extension orphans already-injected scripts: chrome.runtime
    // goes away and every call throws. Detect it and say so.
    if (!chrome?.runtime?.sendMessage) {
      resolve({ ok: false, error: "Extension reloaded — refresh this tab and try again." });
      return;
    }
    try {
      chrome.runtime.sendMessage(msg, (resp) => {
        if (chrome.runtime.lastError) {
          resolve({ ok: false, error: "Extension reloaded — refresh this tab and try again." });
          return;
        }
        if (!resp) return resolve({ ok: false, error: "No response from extension background worker." });
        resolve(resp);
      });
    } catch (e) {
      resolve({ ok: false, error: "Extension reloaded — refresh this tab and try again." });
    }
  });
}

function wireToggle(btn, panel, meta = null) {
  let busy = false;
  let displayed = null;
  let observed = null;
  let lastDisplayedAt = 0;
  const updateDiv = document.createElement("section");
  const fullDiv = document.createElement("section");
  for (const section of [updateDiv, fullDiv]) {
    Object.assign(section.style, { padding: "14px 18px" });
  }
  fullDiv.style.borderTop = "1px solid rgba(255,255,255,0.16)";
  panel.replaceChildren(updateDiv, fullDiv);

  function setStatus(text) {
    if (meta) meta.textContent = text;
  }
  function displayAge() {
    const minutes = Math.floor((Date.now() - lastDisplayedAt) / 60000);
    return minutes < 1 ? "Updated just now" : `Updated ${minutes}m ago`;
  }
  function statusText(isLive) {
    return isLive ? displayAge() : "Stream offline · last summary";
  }
  function sectionText(section, heading, text) {
    section.replaceChildren();
    const label = document.createElement("div");
    label.textContent = heading;
    Object.assign(label.style, {
      color: "#aaa9ae", fontSize: "11px", fontWeight: "700",
      letterSpacing: "0.08em", marginBottom: "7px",
    });
    const body = document.createElement("div");
    body.textContent = text;
    section.appendChild(label);
    section.appendChild(body);
    return body;
  }
  function showFullSummary(preview, text) {
    const compact = preview && preview.trim() !== text.trim() ? preview : null;
    const body = sectionText(fullDiv, "STREAM SO FAR", compact || text);
    if (!compact) return;
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.textContent = "Show full summary";
    toggle.setAttribute("aria-expanded", "false");
    Object.assign(toggle.style, {
      display: "block", padding: "8px 0 0", background: "transparent",
      border: "none", color: "#bf9aff", fontSize: "12px", fontWeight: "650",
      cursor: "pointer", textAlign: "left",
    });
    toggle.addEventListener("click", (e) => {
      e.stopPropagation();
      const expanded = toggle.getAttribute("aria-expanded") === "true";
      body.textContent = expanded ? compact : text;
      toggle.textContent = expanded ? "Show full summary" : "Show less";
      toggle.setAttribute("aria-expanded", expanded ? "false" : "true");
    });
    fullDiv.appendChild(toggle);
  }

  async function refresh(status = null) {
    if (busy) return;
    busy = true;
    try {
      if (!displayed) sectionText(updateDiv, "LATEST UPDATE", "Loading latest…");
      const up = await sendMsg({ type: "FETCH_UPDATE", login: "xqc" });
      if (!up.ok) {
        if (!displayed) sectionText(updateDiv, "LATEST UPDATE", up.error);
        setStatus(up.error);
        return;
      }
      sectionText(updateDiv, "LATEST UPDATE", up.text);
      // Fetch the full summary only when the material key or session changes.
      // Rolling-only updates can be displayed immediately without an LLM call.
      if (!status || status.sessionId !== up.sessionId) {
        status = await sendMsg({ type: "FETCH_STATUS", login: "xqc" });
      }
      const needsFull = !displayed || displayed.sessionId !== up.sessionId ||
        displayed.fullKey !== status.fullKey;
      if (needsFull) {
        sectionText(fullDiv, "STREAM SO FAR", "Loading full summary…");
        const full = await sendMsg({ type: "FETCH_FULL_SUMMARY", sessionId: up.sessionId });
        if (!full.ok) {
          sectionText(fullDiv, "STREAM SO FAR", `Unavailable: ${full.error} (click again to retry)`);
          setStatus("Full summary unavailable");
          return;
        }
        showFullSummary(full.preview, full.text);
      }
      if (status.ok && status.sessionId === up.sessionId) {
        displayed = { sessionId: up.sessionId, recapKey: status.recapKey,
          fullKey: status.fullKey,
          rollingId: up.rollingId, isLive: up.isLive };
        lastDisplayedAt = Date.now();
        setStatus(statusText(up.isLive));
      } else {
        setStatus("Checking for updates…"); // session changed during the request
      }
    } finally {
      busy = false;
    }
  }

  async function pollStatus() {
    const st = await sendMsg({ type: "FETCH_STATUS", login: "xqc" });
    if (!st.ok) {
      if (isOpen(panel)) await refresh(); // retry automatically after API downtime
      else setStatus(st.error);
      return;
    }
    if (displayed) {
      if (displayed.sessionId !== st.sessionId || displayed.isLive !== st.isLive ||
           displayed.rollingId !== st.rollingId ||
           displayed.recapKey !== st.recapKey || displayed.fullKey !== st.fullKey) {
        if (isOpen(panel)) await refresh(st);
        else setStatus(st.isLive ? "Latest update available" : "Stream offline · last summary");
      } else setStatus(statusText(st.isLive));
    } else if (observed && (observed.sessionId !== st.sessionId ||
               observed.isLive !== st.isLive || observed.rollingId !== st.rollingId ||
                observed.recapKey !== st.recapKey || observed.fullKey !== st.fullKey)) {
      setStatus(st.isLive ? "Latest update available" : "Stream offline · last summary");
    } else if (!observed && !st.isLive) {
      setStatus("Stream offline · last summary");
    }
    observed = st;
  }

  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    e.preventDefault();
    if (busy) return;
    if (isOpen(panel)) {
      closePanel(panel);
    } else {
      openPanel(panel);
      await refresh();
    }
  });
  pollStatus(); // establish baseline before the first click
  const timer = setInterval(() => {
    if (btn.isConnected) pollStatus();
    else clearInterval(timer);
  }, 30000);
}

// ---- Mode 1: hover-revealed card inside the player on xQc's page ----
function injectPageAnchored() {
  if (document.querySelector(`[${PAGE_MARKED}]`)) return true;

  const player = document.querySelector('[data-a-target="video-player"]') ||
    document.querySelector(".video-player");
  if (!player) {
    console.info("[xqc-peek] waiting for player…", location.pathname);
    return false; // player not mounted yet — retry later
  }
  console.info("[xqc-peek] overlay in upper-left of player");

  const wrap = document.createElement("div");
  wrap.setAttribute(PAGE_MARKED, "1");
  Object.assign(wrap.style, {
    position: "absolute", top: "12px", left: "12px", zIndex: "30",
    width: "min(460px, calc(100% - 24px))", maxHeight: "calc(100% - 88px)",
    display: "flex", flexDirection: "column", overflow: "hidden",
    background: "linear-gradient(120deg, #262426, #1b1c1e)", color: "#fff",
    border: "1px solid rgba(255,255,255,0.18)", borderRadius: "14px",
    boxShadow: "0 12px 32px rgba(0,0,0,0.5)", boxSizing: "border-box",
    pointerEvents: "none", opacity: "0", visibility: "hidden",
    transition: "opacity 160ms ease, visibility 160ms ease",
  });

  const header = document.createElement("div");
  Object.assign(header.style, {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    gap: "12px", padding: "12px 16px", flexShrink: "0", pointerEvents: "auto",
  });
  const titleBlock = document.createElement("div");
  const title = document.createElement("div");
  title.textContent = "Stream summary";
  Object.assign(title.style, { fontSize: "17px", fontWeight: "650" });
  const meta = document.createElement("div");
  meta.textContent = "Ready to summarize";
  Object.assign(meta.style, { color: "#aaa9ae", fontSize: "12px", marginTop: "3px" });
  titleBlock.appendChild(title);
  titleBlock.appendChild(meta);

  const btn = document.createElement("button");
  btn.textContent = "Summarize";
  btn.title = "Show current AI summary";
  Object.assign(btn.style, {
    fontSize: "13px", fontWeight: "650", lineHeight: "1",
    padding: "10px 14px", borderRadius: "9px", flexShrink: "0",
    background: "#772ce8", color: "#fff", border: "none", cursor: "pointer",
  });

  const panel = document.createElement("div");
  stylePanel(panel);
  Object.assign(panel.style, {
    borderTop: "1px solid rgba(255,255,255,0.16)", pointerEvents: "auto",
    minHeight: "0", flexShrink: "1",
  });

  wireToggle(btn, panel, meta);
  header.appendChild(titleBlock);
  header.appendChild(btn);
  wrap.appendChild(header);
  wrap.appendChild(panel);
  player.appendChild(wrap);
  function show() {
    wrap.style.visibility = "visible";
    wrap.style.opacity = "1";
  }
  function hide() {
    if (wrap.contains(document.activeElement)) return;
    wrap.style.opacity = "0";
    wrap.style.visibility = "hidden";
  }
  player.addEventListener("mouseenter", show);
  player.addEventListener("mouseleave", hide);
  wrap.addEventListener("focusin", show);
  wrap.addEventListener("focusout", () => setTimeout(hide, 0));
  if (player.matches(":hover")) show();
  return true;
}

// Retry until the player mounts (Twitch renders it async after page load).
function waitForPlayer(tries = 30) {
  if (!isChannelPage()) return;
  if (injectPageAnchored()) return;
  if (tries <= 0) {
    console.info("[xqc-peek] player anchor not found — using floating fallback");
    injectFloatingFallback();
    return;
  }
  setTimeout(() => waitForPlayer(tries - 1), 1000);
}

// Last resort: keep the UI beside channel information, never over player controls.
function injectFloatingFallback() {
  if (document.querySelector(`[${PAGE_MARKED}]`)) return;
  const info = document.querySelector('[data-a-target="channel-info-content"]');
  if (!info) return;

  const panel = document.createElement("div");
  stylePanel(panel);
  Object.assign(panel.style, {
    width: "min(460px, 100%)", background: "#242326", borderRadius: "10px",
  });

  const btn = document.createElement("button");
  btn.textContent = "Summarize";
  btn.title = "Show current AI summary";
  Object.assign(btn.style, {
    fontSize: "12px", fontWeight: "600", lineHeight: "1",
    padding: "7px 12px", borderRadius: "6px",
    background: "#772ce8", color: "#fff", border: "none", cursor: "pointer",
  });

  wireToggle(btn, panel);
  const wrap = document.createElement("div");
  wrap.setAttribute(PAGE_MARKED, "1");
  Object.assign(wrap.style, { display: "flex", flexDirection: "column", alignItems: "flex-end",
    gap: "8px", margin: "8px 12px" });
  wrap.appendChild(btn);
  wrap.appendChild(panel);
  info.insertAdjacentElement("afterend", wrap);
}

function isChannelPage() {
  return /^\/xqc(\/|$)/i.test(location.pathname);
}

// ---- Mode 2: sidebar hover-preview card ----
function isXqcCard(node) {
  if (!(node instanceof HTMLElement)) return false;
  const link = node.querySelector('a[href^="/xqc"], a[href*="twitch.tv/xqc"]');
  if (!link) return false;
  const href = link.getAttribute("href") || "";
  const path = href.replace(/^https?:\/\/(www\.)?twitch\.tv/i, "");
  if (!XQC_HREF_RE.test(path)) return false;
  return /viewers/i.test(node.textContent || "");
}

function findCardRoot(node) {
  let el = node;
  for (let i = 0; i < 3 && el.parentElement; i++) {
    if (el.offsetWidth > 200) break;
    el = el.parentElement;
  }
  return el;
}

function injectCardButton(cardRoot) {
  // On xQc's own channel page the anchored button owns the UI.
  if (document.querySelector(`[${PAGE_MARKED}]`)) return;
  if (cardRoot.getAttribute(ALREADY_MARKED)) return;
  cardRoot.setAttribute(ALREADY_MARKED, "1");

  if (getComputedStyle(cardRoot).position === "static") {
    cardRoot.style.position = "relative";
  }

  const btn = document.createElement("button");
  btn.textContent = "Summarize";
  btn.title = "Show current AI summary";
  Object.assign(btn.style, {
    position: "absolute", top: "8px", right: "8px",
    fontSize: "11px", fontWeight: "600", lineHeight: "1",
    padding: "5px 9px", borderRadius: "4px",
    background: "#772ce8", color: "#fff", border: "none", cursor: "pointer",
    zIndex: "9999",
  });

  const panel = document.createElement("div");
  stylePanel(panel);
  panel.style.margin = "6px 0 0 0";

  const titleBlock = cardRoot.firstElementChild || cardRoot;
  titleBlock.insertAdjacentElement("afterend", panel);

  wireToggle(btn, panel);
  cardRoot.appendChild(btn);
}

const observer = new MutationObserver((mutations) => {
  for (const mutation of mutations) {
    for (const node of mutation.addedNodes) {
      if (!(node instanceof HTMLElement)) continue;
      if (isXqcCard(node)) { injectCardButton(findCardRoot(node)); continue; }
      const nested = [...(node.querySelectorAll?.("div,section,article") || [])].find(isXqcCard);
      if (nested) injectCardButton(findCardRoot(nested));
    }
  }
});

if (isChannelPage()) waitForPlayer();
observer.observe(document.body, { childList: true, subtree: true });

// Twitch is a single-page app: navigating (e.g. home -> xQc) does not reload
// the page, so the check above only runs on full loads. Re-check on nav.
let lastPath = location.pathname;
setInterval(() => {
  if (location.pathname === lastPath) return;
  lastPath = location.pathname;
  console.info("[xqc-peek] nav to", lastPath);
  document.querySelectorAll(`[${PAGE_MARKED}]`).forEach((el) => el.remove());
  if (isChannelPage()) waitForPlayer();
}, 2000);

} // end single-run guard
