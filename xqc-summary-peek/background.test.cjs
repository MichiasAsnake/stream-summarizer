const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const vm = require("node:vm");

const script = readFileSync(require.resolve("./background.js"), "utf8");

function worker(fetch) {
  let listener;
  vm.runInNewContext(script, {
    chrome: {
      storage: { sync: { get: async () => ({ apiBase: "http://localhost:8000", apiToken: "test" }) } },
      runtime: { onMessage: { addListener: (fn) => { listener = fn; } } },
    },
    fetch,
  });
  return (msg) => new Promise((resolve) => listener(msg, null, resolve));
}

test("connection failures explain the missing API without exposing the token", async () => {
  const send = worker(async () => { throw new TypeError("Failed to fetch"); });
  const response = await send({ type: "FETCH_UPDATE", login: "xqc" });
  assert.equal(response.ok, false);
  assert.match(response.error, /Cannot reach summarizer at http:\/\/localhost:8000/);
  assert.doesNotMatch(response.error, /test/);
});

test("unauthorized responses point to extension configuration", async () => {
  const send = worker(async () => ({ status: 401, ok: false }));
  const response = await send({ type: "FETCH_STATUS", login: "xqc" });
  assert.match(response.error, /API token rejected/);
});

test("status passes the full-summary cache version through to the card", async () => {
  const send = worker(async (url) => ({ ok: true, json: async () =>
    url.includes("summary-session") ? { session_id: 2, live: true }
      : url.includes("recap-status") ? { key: "2:stamp", full_key: "session-v2:2" }
        : { id: 3 } }));
  const response = await send({ type: "FETCH_STATUS", login: "xqc" });
  assert.equal(response.fullKey, "session-v2:2");
});
