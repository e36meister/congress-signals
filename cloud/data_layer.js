// ---------- data from Cloudflare ----------
function errorText(e) {
  switch (e?.code) {
    case "login": return "Please log in.";
    case "no_file": return "No results on Cloudflare yet. They arrive with the next update (within about 15 minutes).";
    case "offline": return "You're offline. Connect to the internet and tap Refresh.";
    default: return "Couldn't load results" + (e?.message ? ": " + esc(e.message) : ".");
  }
}
async function api(path, opts = {}) {
  let r;
  try { r = await fetch(path, { credentials: "same-origin", ...opts }); }
  catch (e) { throw { code: "offline" }; }
  if (r.status === 401 && path !== "/api/login") { showLogin(); throw { code: "login" }; }
  let js = null;
  try { js = await r.json(); } catch (e) { if (r.ok) throw { code: "bad_json", message: "the results file is incomplete; try Refresh in a minute" }; }
  if (r.status === 404 && js?.error === "no-file") throw { code: "no_file" };
  if (!r.ok) throw { code: js?.error || "http", why: js?.why, message: js?.message || ("error " + r.status), status: r.status };
  return { js, headers: r.headers };
}
const WHY_BUY = { "amount-over-limit": "the amount is over your per-order limit", "not-enough-cash": "not enough buying power",
  "not-tradable": "Alpaca can't trade this stock", "not-found": "stock not found on Alpaca", "no-price": "no current price",
  "amount-too-small": "the amount is less than one share", "no-keys": "no Alpaca keys are set up", "bad-request": "the request couldn't be read" };

let loading = false, lastEtag = null, lastData = 0, lastLive = null, liveTimer = null;
async function loadData() {
  if (loading) return; loading = true;
  let redraw = true;
  try {
    const { js, headers } = await api("/api/data");
    const etag = headers.get("ETag");
    if (etag && etag === lastEtag && state.data) {          // nothing new: keep the page as it is (no jump)
      lastData = Date.now();
      const synced = headers.get("X-Uploaded");
      if (synced) $("#statusText").textContent = $("#statusText").textContent.replace(/ · synced .*$/, "") + " · synced " + ago(synced);
      redraw = false;
      loadLive();
      return;
    }
    lastEtag = etag;
    state.data = js; state.error = null; lastData = Date.now();
    const synced = headers.get("X-Uploaded");
    if (lastLive) applyLive(lastLive);
    const upd = js.updated;
    const hours = (Date.now() - Date.parse(upd)) / 3.6e6;
    setStatus(hours > 36 ? "stale" : "ok", "Results updated " + ago(upd) + " (" + fmtET(upd) + ")" + (hours > 36 ? " · the daily update hasn't run since then" : "") +
      (synced ? " · synced " + ago(synced) : ""), true);
  } catch (e) {
    if (e.code === "login") return;
    if (state.data) setStatus("stale", e.code === "offline" ? "Offline · showing the last results" : "Couldn't refresh · showing the last results", true);
    else { state.error = errorText(e); setStatus("err", e.code === "no_file" ? "Waiting for the first upload" : "Couldn't load results", true); }
  } finally { loading = false; if (redraw) render(); }
  loadLive();
}

// live account numbers straight from Alpaca, laid over the last full update
function applyLive(L) {
  const p = state.data?.portfolio;
  state.taps = L.taps || [];
  if (!p) return;
  const old = Object.fromEntries((p.positions || []).map(x => [x.t, x]));
  p.positions = (L.positions || []).map(x => ({ bot: false, sleeve: null, hold: null, bought: null, held: null, ...(old[x.t] || {}), ...x }))
    .sort((a, b) => (b.mv || 0) - (a.mv || 0));
  if (L.equity != null) { p.equity = L.equity; p.cash = L.cash; if (L.last_equity) p.day_pl = L.equity - L.last_equity; }
  p.market_open = L.market_open; p.next_open = L.next_open; p.updated = L.at;
}
async function loadLive() {
  clearTimeout(liveTimer);
  if (document.visibilityState === "visible" && state.data && !window.__CTD_SAMPLE) {
    try { lastLive = (await api("/api/live")).js; applyLive(lastLive); render(); } catch (e) {}
  }
  liveTimer = setTimeout(loadLive, 60000);
}

async function sendOrder(t) {
  const inp = document.getElementById("amt-" + t);
  const dollars = Math.round(parseFloat(inp ? inp.value : state.amounts[t]) * 100) / 100;
  if (!(dollars >= 1)) { state.tapMsg[t] = { ok: false, text: "Enter a dollar amount." }; render(); return; }
  state.sending = true; render();
  try {
    const { js } = await api("/api/buy", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t, dollars }) });
    state.tapMsg[t] = { ok: true, text: `Order placed: ${money(dollars)} of ${t}` + (js.qty ? ` (${js.qty} shares)` : "") +
      (js.market_open ? "." : `. It fills when the market opens (${fmtET(js.next_open)}).`) };
    state.confirm = null;
    loadLive();
  } catch (e) {
    state.tapMsg[t] = { ok: false, text: e.code === "offline" ? "You're offline; the order wasn't sent." :
      e.code === "login" ? "Log in again, then tap Buy." :
      "Not placed: " + (WHY_BUY[e.why] || (e.why || "").replace(/-/g, " ") || e.message || "unknown reason") + "." };
  } finally { state.sending = false; render(); }
}
document.getElementById("main").addEventListener("click", ev => {
  const b = ev.target.closest("[data-buy],[data-send],[data-cancel]");
  if (!b) return;
  if (b.dataset.buy) { state.confirm = b.dataset.buy; delete state.tapMsg[b.dataset.buy]; render(); document.getElementById("amt-" + b.dataset.buy)?.focus(); }
  else if (b.dataset.send) { if (!state.sending) sendOrder(b.dataset.send); }
  else if (b.dataset.cancel) { state.confirm = null; render(); }
});
document.getElementById("main").addEventListener("input", ev => {
  if (ev.target.id && ev.target.id.startsWith("amt-")) state.amounts[ev.target.id.slice(4)] = ev.target.value;
});
$("#refresh").addEventListener("click", () => { setStatus("", "Checking for new results…", false); loadData(); });

// ---------- login ----------
function showLogin(msg) {
  $("#login").hidden = false;
  $("#pcMsg").textContent = msg || "";
  setTimeout(() => $("#pc").focus(), 50);
}
$("#loginForm").addEventListener("submit", async ev => {
  ev.preventDefault();
  const go = $("#pcGo"); go.disabled = true; $("#pcMsg").textContent = "";
  try {
    await api("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ passcode: $("#pc").value }) });
    $("#login").hidden = true; $("#pc").value = "";
    loadData(); alertsState();
  } catch (e) {
    $("#pcMsg").textContent = e.code === "offline" ? "You're offline." : (e.message || "Couldn't log in.");
  } finally { go.disabled = false; }
});

// refresh when the app comes back to the screen
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible" || !$("#login").hidden) return;
  loadData();
});
setInterval(() => { if (document.visibilityState === "visible" && $("#login").hidden) loadData(); }, 2 * 60000);

// ---------- alerts (push notifications; iPhone: only from the home-screen app) ----------
function keyBytes(s) { const b = atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)); return Uint8Array.from(b, c => c.charCodeAt(0)); }
async function alertsState() {
  const btn = $("#alerts");
  if (!btn || !("serviceWorker" in navigator)) return;
  const reg = await navigator.serviceWorker.register("/sw.js").catch(() => null);
  if (!reg || !("PushManager" in window) || !("Notification" in window)) {
    // an iPhone Safari tab can't get alerts; the home-screen app can
    if (/iPhone|iPad/.test(navigator.userAgent)) { btn.textContent = "Alerts"; btn.dataset.mode = "info"; btn.hidden = false; }
    return;
  }
  const sub = await reg.pushManager.getSubscription();
  btn.dataset.mode = "on"; btn.textContent = "Turn on alerts";
  btn.hidden = !!(sub && Notification.permission === "granted");
}
$("#alerts").addEventListener("click", async () => {
  const btn = $("#alerts");
  if (btn.dataset.mode === "info") { alert("Alerts work in the home-screen app. Open Capitol Capital from its icon, then tap “Turn on alerts”."); return; }
  btn.disabled = true;
  try {
    const perm = await Notification.requestPermission();
    if (perm !== "granted") { alert("Notifications are off for Capitol Capital. Turn them on in iPhone Settings → Notifications → Capitol Capital, then tap the button again."); return; }
    const reg = await navigator.serviceWorker.ready;
    const { js } = await api("/api/push/key");
    let sub = await reg.pushManager.getSubscription();
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(js.key) });
    await api("/api/push/subscribe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sub: sub.toJSON() }) });
    btn.hidden = true;
  } catch (e) {
    if (e.code !== "login") alert("Couldn't turn on alerts: " + (e.message || e.code || e));
  } finally { btn.disabled = false; }
});
// tapping an alert opens a tab (e.g. /#port)
addEventListener("hashchange", () => {
  const h = location.hash.slice(1);
  if (/^(today|record|defense|small|works|real|port)$/.test(h)) { state.tab = h; render(); scrollTo(0, 0); }
});

// ---------- home-screen widget (Scriptable) ----------
let widgetJs = null;
async function fetchWidget(renew) {
  const r = await fetch("/api/widget/script", renew ? { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" } : {});
  if (r.status === 401) { showLogin(); throw { code: "login" }; }
  if (!r.ok) throw { message: "error " + r.status };
  return r.text();
}
function widgetModal() {
  let bg = document.getElementById("wbg");
  if (!bg) {
    bg = document.createElement("div"); bg.id = "wbg"; bg.className = "mbg";
    bg.addEventListener("click", ev => { if (ev.target === bg || ev.target.closest(".mx")) bg.hidden = true; });
    document.body.appendChild(bg);
  }
  bg.hidden = false;
  bg.innerHTML = `<div class="modal"><div class="mh"><div><div class="t" style="font-size:18px">Home-screen widget</div>
    <div class="co">A medium widget: account value, today's change, you vs the S&amp;P, newest buys. Read-only.</div></div>
    <button class="mx" aria-label="Close">×</button></div>
    <ol style="margin:0;padding-left:20px;display:grid;gap:6px;font-size:14px">
      <li>Install <b>Scriptable</b> (free) from the App Store.</li>
      <li>Tap <b>Copy widget script</b> below.</li>
      <li>In Scriptable tap <b>+</b>, paste, and name it <b>Capitol Capital</b>.</li>
      <li>On your home screen: hold an empty spot → <b>Edit</b> → <b>Add Widget</b> → <b>Scriptable</b> → the <b>medium</b> size (the wide one) → <b>Add</b>.</li>
      <li>Hold the new widget → <b>Edit Widget</b> → Script: <b>Capitol Capital</b>. Tapping it opens this app.</li>
    </ol>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
      <button type="button" class="buybtn" id="wCopy" disabled>Loading…</button>
      <button type="button" class="link" id="wNew">Make a new key</button>
      <span class="foot" id="wMsg"></span></div>
    <p class="foot" style="margin:0">The script holds its own key, which only reads this summary; "Make a new key" turns off any copy you've shared.</p></div>`;
  const btn = document.getElementById("wCopy"), msg = document.getElementById("wMsg");
  const ready = () => { btn.disabled = false; btn.textContent = "Copy widget script"; };
  fetchWidget(false).then(t => { widgetJs = t; ready(); }, e => { msg.textContent = e.code === "login" ? "Log in first." : "Couldn't load the script."; });
  btn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(widgetJs); msg.textContent = "Copied. Now paste it in Scriptable."; }
    catch (e) { msg.textContent = "Copy didn't work here; long-press and copy from the text below."; 
      const ta = document.createElement("textarea"); ta.value = widgetJs; ta.style.cssText = "width:100%;height:120px;font:11px var(--mono)"; btn.closest(".modal").appendChild(ta); ta.select(); }
  });
  document.getElementById("wNew").addEventListener("click", async () => {
    if (!confirm("Make a new widget key? The widget stops working until you paste the new script into Scriptable.")) return;
    btn.disabled = true; btn.textContent = "Loading…";
    try { widgetJs = await fetchWidget(true); ready(); msg.textContent = "New key made. Copy the script again."; } catch (e) { msg.textContent = "Couldn't make a new key."; }
  });
}
// no Widget button any more (already installed); opening the app with #widget at the end of its address shows the setup again
if (location.hash === "#widget") setTimeout(widgetModal, 1500);

// ---------- phone: wide tables as cards ----------
// Each cell gets its column name (shown as a label on phones); tables with 4+ columns and up to 25 rows become a
// stack of cards on narrow screens (CSS in the app build). The ticker/stock column, if any, is the card's title.
const TITLE_COL = /^(ticker|stock|company|member|portfolio|setting|signal|year|when)$/i;
function cardify(root) {
  for (const tbl of root.querySelectorAll(".tbl")) {
    if (tbl.classList.contains("tlist") || tbl.dataset.cd) continue;
    const t = tbl.querySelector("table"); if (!t) continue;
    const heads = [...t.querySelectorAll("thead th")].map(th => th.textContent.trim());
    const rows = t.querySelectorAll("tbody tr");
    tbl.dataset.cd = "1";
    if (heads.length < 4 || rows.length > 25) continue;
    let ti = heads.findIndex(h => TITLE_COL.test(h)); if (ti < 0) ti = 0;
    for (const tr of rows) {
      let i = 0;
      for (const td of tr.children) {
        td.dataset.l = td.colSpan > 1 ? "" : (heads[i] || "");
        if (i === ti && td.colSpan === 1) td.classList.add("ttl");
        else if (td.textContent.trim().length > 26 || td.querySelector(".who-cell,.bar,svg")) td.classList.add("full");
        i += td.colSpan || 1;
      }
    }
    tbl.classList.add("cards");
  }
}
let cardQueued = false;
new MutationObserver(() => { if (cardQueued) return; cardQueued = true; requestAnimationFrame(() => { cardQueued = false; cardify(document.body); }); })
  .observe(document.body, { childList: true, subtree: true });

// ---------- pull to refresh (home-screen app has no browser reload) ----------
(() => {
  const ind = document.createElement("div"); ind.id = "ptr"; ind.setAttribute("aria-hidden", "true"); document.body.appendChild(ind);
  const PULL = 80;
  let y0 = null, dy = 0, busy = false;
  const show = (d, text, go) => { ind.textContent = text; ind.classList.toggle("go", !!go);
    ind.style.transform = `translate(-50%, ${Math.min(d, PULL + 20) - 60}px)`; ind.style.opacity = String(Math.min(1, d / 40)); };
  const hide = () => { ind.style.transition = "transform .2s, opacity .2s"; ind.style.transform = "translate(-50%,-60px)"; ind.style.opacity = "0";
    setTimeout(() => { ind.style.transition = ""; }, 220); };
  addEventListener("touchstart", e => {
    const modal = document.querySelector(".mbg:not([hidden])");
    y0 = (!busy && scrollY <= 0 && e.touches.length === 1 && $("#login").hidden && !modal) ? e.touches[0].clientY : null; dy = 0;
  }, { passive: true });
  addEventListener("touchmove", e => {
    if (y0 == null) return;
    dy = (e.touches[0].clientY - y0) * 0.6;
    if (dy <= 0 || scrollY > 0) { if (dy < 0) y0 = null; return; }
    show(dy, dy >= PULL ? "Release to refresh" : "Pull to refresh", dy >= PULL);
  }, { passive: true });
  addEventListener("touchend", async () => {
    if (y0 == null) return; y0 = null;
    if (dy < PULL) { hide(); return; }
    busy = true; show(PULL, "Refreshing…", true);
    try { lastEtag = null; setStatus("", "Checking for new results…", false); await loadData(); }
    finally { busy = false; show(PULL, "Up to date", true); setTimeout(hide, 600); }
  }, { passive: true });
})();

// ---------- app-icon badge: alerts add to it (service worker); opening the app clears it ----------
function clearBadge() {
  try { navigator.clearAppBadge && navigator.clearAppBadge().catch(() => {}); } catch (e) {}
  try { caches.open("cc-badge").then(c => c.put("/n", new Response("0"))).catch(() => {}); } catch (e) {}
}
clearBadge();
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") clearBadge(); });

state.folderId = "cloud";       // turns on the Buy buttons
// preview hook for local screenshots only
if (window.__CTD_SAMPLE) { state.data = window.__CTD_SAMPLE; state.confirm = window.__CTD_CONFIRM || null; state.taps = window.__CTD_TAPS || [];
  if (window.__CTD_LOGIN) showLogin(); setStatus("ok", "Results updated " + ago(state.data.updated), true); render(); return; }
render();
api("/api/session").then(() => { loadData(); alertsState(); }, e => { if (e.code !== "login") { state.error = errorText(e); setStatus("err", "Can't reach the server", true); render(); } });

