const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const vm = require("node:vm");

const script = readFileSync(require.resolve("./content.js"), "utf8");

function harness() {
  const timers = [];
  const state = { sessionId: 1, isLive: true, rollingId: 1,
    recapKey: "1:old", fullKey: "session-v2:1", failFull: false,
    failUpdate: false, failStatus: false };
  const requests = [];
  const roots = [];

  class Element {
    constructor(tag) {
      this.tag = tag;
      this.style = {};
      this.attrs = {};
      this.children = [];
      this.textContent = "";
      this.isConnected = true;
      this.listeners = {};
    }
    setAttribute(key, value) { this.attrs[key] = value; }
    getAttribute(key) { return this.attrs[key]; }
    appendChild(child) {
      child.parentElement = this;
      this.children.push(child);
      if (this === player) roots.push(child);
      return child;
    }
    replaceChildren(...children) { this.children = []; children.forEach((c) => this.appendChild(c)); }
    addEventListener(name, cb) { this.listeners[name] = cb; }
    insertAdjacentElement(_position, child) { roots.push(child); }
    contains(node) { return this === node || this.children.some((c) => c.contains(node)); }
    matches() { return false; }
    remove() {
      this.isConnected = false;
      const parent = this.parentElement?.children;
      if (parent) parent.splice(parent.indexOf(this), 1);
      if (roots.includes(this)) roots.splice(roots.indexOf(this), 1);
    }
  }
  const player = new Element("player");
  const document = {
    body: new Element("body"),
    createElement: (tag) => new Element(tag),
    querySelector: (selector) => selector === '[data-a-target="video-player"]' ? player
      : selector === '[data-xqc-peek-page]' ? roots.find((r) => r.attrs["data-xqc-peek-page"]) : null,
    querySelectorAll: () => roots.filter((r) => r.attrs["data-xqc-peek-page"]),
  };
  const chrome = { runtime: {
    sendMessage(msg, cb) {
      requests.push(msg.type);
      const response = msg.type === "FETCH_STATUS"
        ? state.failStatus ? { ok: false, error: "Cannot reach summarizer" }
          : { ok: true, sessionId: state.sessionId, isLive: state.isLive,
            rollingId: state.rollingId,
            recapKey: state.recapKey, fullKey: state.fullKey }
        : msg.type === "FETCH_UPDATE"
          ? state.failUpdate ? { ok: false, error: "Cannot reach summarizer" }
            : { ok: true, sessionId: state.sessionId, isLive: state.isLive,
              rollingId: state.rollingId,
              text: `update ${state.rollingId}` }
          : state.failFull ? { ok: false, error: "API 503" }
            : { ok: true, text: `complete session ${state.sessionId}`,
                preview: state.noPreview ? null : `short session ${state.sessionId}` };
      cb(response);
    },
  } };
  const location = { pathname: "/xqc" };
  vm.runInNewContext(script, {
    window: {}, document, chrome, location, HTMLElement: Element,
    MutationObserver: class { observe() {} },
    setInterval: (fn) => { timers.push(fn); return timers.length; },
    clearInterval: () => {}, setTimeout: () => {}, console: { info() {} },
  });
  const button = () => roots.at(-1)?.children[0]?.children[1];
  const panel = () => roots.at(-1)?.children[1];
  const meta = () => roots.at(-1)?.children[0]?.children[0]?.children[1];
  const click = () => button().listeners.click({ stopPropagation() {}, preventDefault() {} });
  const tick = async () => { for (const fn of [...timers]) await fn(); await settle(); };
  return { state, requests, location, roots, player, button, panel, meta, click, tick };
}

async function settle() {
  await new Promise((resolve) => setImmediate(resolve));
}

test("rolling updates are labeled clearly without implying the full story changed", async () => {
  const h = harness();
  await settle();
  h.state.rollingId = 2;
  await h.tick();
  assert.equal(h.button().textContent, "Summarize");
  assert.equal(h.meta().textContent, "Latest update available");
  await h.click();
  assert.equal(h.panel().children[0].children[1].textContent, "update 2");
  assert.equal(h.panel().children[1].children[1].textContent, "short session 1");
  assert.equal(h.meta().textContent, "Updated just now");
  h.state.rollingId = 3;
  await h.tick();
  assert.equal(h.panel().children[0].children[1].textContent, "update 3");
  assert.equal(h.requests.filter((r) => r === "FETCH_FULL_SUMMARY").length, 1);
  await h.click(); // close
  h.state.rollingId = 4;
  await h.tick();
  assert.equal(h.meta().textContent, "Latest update available");
  assert.equal(h.button().textContent, "Summarize");
  await h.click();
  assert.equal(h.panel().children[0].children[1].textContent, "update 4");
  assert.equal(h.requests.filter((r) => r === "FETCH_FULL_SUMMARY").length, 1);
});

test("a new live session refreshes the full summary and a failed request stays retryable", async () => {
  const h = harness();
  await h.click();
  h.state.sessionId = 2;
  h.state.failFull = true;
  await h.tick();
  assert.match(h.panel().children[1].children[1].textContent, /Unavailable: API 503/);
  assert.equal(h.meta().textContent, "Full summary unavailable");
  h.state.failFull = false;
  await h.tick();
  assert.match(h.panel().children[1].children[1].textContent, /short session 2/);
  assert.equal(h.button().textContent, "Summarize");
});

test("cached preview expands to full detail and can collapse without refetching", async () => {
  const h = harness();
  await h.click();
  const fullSection = h.panel().children[1];
  const toggle = fullSection.children[2];
  const count = h.requests.length;
  assert.equal(toggle.textContent, "Show full summary");
  toggle.listeners.click({ stopPropagation() {} });
  assert.equal(fullSection.children[1].textContent, "complete session 1");
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  toggle.listeners.click({ stopPropagation() {} });
  assert.equal(fullSection.children[1].textContent, "short session 1");
  assert.equal(h.requests.length, count);
});

test("a missing preview shows the detailed summary directly", async () => {
  const h = harness();
  h.state.noPreview = true;
  await h.click();
  assert.equal(h.panel().children[1].children[1].textContent, "complete session 1");
  assert.equal(h.panel().children[1].children.length, 2);
});

test("ended streams keep the last summary accessible and label it offline", async () => {
  const h = harness();
  h.state.isLive = false;
  await h.tick();
  assert.equal(h.meta().textContent, "Stream offline · last summary");
  await h.click();
  assert.equal(h.panel().children[1].children[1].textContent, "short session 1");
  assert.equal(h.meta().textContent, "Stream offline · last summary");
});

test("API downtime shows a useful error and recovers without closing the card", async () => {
  const h = harness();
  await h.click();
  h.state.failStatus = true;
  h.state.failUpdate = true;
  await h.tick();
  assert.equal(h.meta().textContent, "Cannot reach summarizer");
  assert.equal(h.panel().children[0].children[1].textContent, "update 1");
  h.state.failStatus = false;
  h.state.failUpdate = false;
  h.state.rollingId = 2;
  await h.tick();
  assert.equal(h.panel().children[0].children[1].textContent, "update 2");
  assert.equal(h.meta().textContent, "Updated just now");
});

test("full-summary cache version change refreshes a stale displayed full summary", async () => {
  const h = harness();
  await h.click();
  h.state.fullKey = "session-v3:1";
  await h.tick();
  assert.equal(h.requests.filter((r) => r === "FETCH_FULL_SUMMARY").length, 2);
});

test("overlay reveals on player hover, hides on leave, and leaves the control bar clear", () => {
  const h = harness();
  const card = h.roots[0];
  assert.equal(card.parentElement, h.player);
  assert.equal(card.style.visibility, "hidden");
  assert.equal(card.style.pointerEvents, "none");
  assert.equal(card.style.top, "12px");
  assert.equal(card.style.maxHeight, "calc(100% - 88px)");
  h.player.listeners.mouseenter();
  assert.equal(card.style.visibility, "visible");
  h.player.listeners.mouseleave();
  assert.equal(card.style.visibility, "hidden");
});

test("SPA navigation clears the stale page marker and reinjects the button", async () => {
  const h = harness();
  const old = h.roots[0];
  h.location.pathname = "/somebody";
  await h.tick();
  assert.equal(old.isConnected, false);
  h.location.pathname = "/xqc";
  await h.tick();
  assert.notEqual(h.roots[0], old);
  assert.equal(h.button().textContent, "Summarize");
});
