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

let loading = false, lastData = 0, lastLive = null, liveTimer = null;
async function loadData() {
  if (loading) return; loading = true;
  try {
    const { js } = await api("/api/data");
    state.data = js; state.error = null; lastData = Date.now();
    if (lastLive) applyLive(lastLive);
    const upd = js.updated;
    const hours = (Date.now() - Date.parse(upd)) / 3.6e6;
    setStatus(hours > 36 ? "stale" : "ok", "Results updated " + ago(upd) + " (" + fmtET(upd) + ")" + (hours > 36 ? " · the daily update hasn't run since then" : ""), true);
  } catch (e) {
    if (e.code === "login") return;
    if (state.data) setStatus("stale", e.code === "offline" ? "Offline · showing the last results" : "Couldn't refresh · showing the last results", true);
    else { state.error = errorText(e); setStatus("err", e.code === "no_file" ? "Waiting for the first upload" : "Couldn't load results", true); }
  } finally { loading = false; render(); }
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
  if (Date.now() - lastData > 5 * 60000) loadData(); else loadLive();
});
setInterval(() => { if (document.visibilityState === "visible" && $("#login").hidden) loadData(); }, 5 * 60000);

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

state.folderId = "cloud";       // turns on the Buy buttons
// preview hook for local screenshots only
if (window.__CTD_SAMPLE) { state.data = window.__CTD_SAMPLE; state.confirm = window.__CTD_CONFIRM || null; state.taps = window.__CTD_TAPS || [];
  if (window.__CTD_LOGIN) showLogin(); setStatus("ok", "Results updated " + ago(state.data.updated), true); render(); return; }
render();
api("/api/session").then(() => { loadData(); alertsState(); }, e => { if (e.code !== "login") { state.error = errorText(e); setStatus("err", "Can't reach the server", true); render(); } });

