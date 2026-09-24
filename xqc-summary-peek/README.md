# xQc Stream Summary Peek (v0.1)

Chrome extension (Manifest V3): reveals a **Stream summary** card when you
hover the upper-left of xQc's player; the player's bottom controls remain
uncovered. A smaller button is also available on the sidebar hover-preview
card. Clicking **Summarize** shows the latest update immediately, then a
cached 60–90 word preview of the cumulative summary. **Show full summary**
expands the cached detail without another request. When you haven't opened the card since a
change, its subtitle says **Latest update available** rather than implying
the longer summary is necessarily different. Single channel (xQc) only.
When the stream ends, the card keeps the most recent saved summary and labels
it **Stream offline · last summary**.

## Setup

1. Backend running: `uvicorn app.api.main:app --host 127.0.0.1 --port 8000`
   (from the repo root).
2. `chrome://extensions` → Developer mode → **Load unpacked** →
   select this folder.
3. Set the API base + token in the background worker's console
   (Service Worker → Inspect):
   ```js
   chrome.storage.sync.set({ apiBase: "http://localhost:8000", apiToken: "PASTE_TOKEN_HERE" })
   ```
 4. Go to twitch.tv/xqc, hover the player and click **Summarize** in the
    upper-left card, or hover xQc's sidebar avatar and use its button.
     Reload the extension and refresh existing Twitch tabs after updating.

If the card says **Cannot reach summarizer**, verify the backend responds at
`http://127.0.0.1:8000/api/v1/sessions` (a 401 without a token means it is
running). On the main Mac, the `com.purplesprite.stream-summarizer` LaunchAgent
starts it at login and restarts it after an unexpected exit; its log is
`data/launchd.log`. If the card says **API token rejected**, reset `apiToken`
in the extension service worker console. The card retries automatically while open.

## Live DOM verification (needed)

`content.js` anchors on `<a href="/xqc">` + "viewers" text — a hypothesis
about Twitch's markup, not a verified fact. With the hover card open in
devtools, confirm the anchor holds and the button/panel land in the right
spot (panel directly under the title/tags row). Fix `isXqcCard()` /
`titleBlock` if not.
