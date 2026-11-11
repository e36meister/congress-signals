// ---------- family accounts (owner, Nov 2 2026) ----------
// A family account sees Today (Buy candidates only), Members, Alerts, its own Account and a short More menu (company
// search, notifications, settings, questions, bug report). The server only answers those (cloud/accounts.js); this
// hides every button and link that would point anywhere else, and render() sends any other page back to Today.
// The owner gets the Accounts page (under More) to add people and see each portfolio.
let ROLE = "owner", SESSION_ME = null;
const isViewer = () => ROLE === "viewer";
const VIEWER_TABS = new Set(["today", "members", "port", "alerts", "more", "search", "vnotif", "vset", "faq", "blog", "bug", "terms", "privacy", "tax"]);
const VIEWER_MORE = [
  { k: "search", l: "Company search", d: "Any ticker or company: its chart, what's going on, and every member's trades" },
  { k: "vnotif", l: "Notifications", d: "Push and email: trades, the daily close, big moves, your own account alerts" },
  { k: "tax", l: "Taxes", d: "Every sale's gain or loss, your Form 8949 statement and what to file" },
  { k: "vset", l: "Settings", d: "Profile picture, extra explanations, home-screen widget, sign out" },
  { k: "faq", l: "Questions and answers", d: "How the tool picks, buys, sells and sizes, and what you can do" },
  { k: "blog", l: "Blog", d: "How things work: the Buy and Sell buttons, suggested sizing" },
  { k: "bug", l: "Report a bug or suggest a feature", d: "Something broken, or an idea for the app? It goes straight to the Operator" },
];
// Today sections a family account sees; the jump bar shows once there are two or more (owner, Nov 3 2026)
const VIEWER_SECTIONS = new Set(["today-buy"]);
const VIEWER_SUB = new Set(["search", "vnotif", "vset", "faq", "blog", "bug", "terms", "privacy", "tax"]);
function applyRole(s) {
  ROLE = s && s.role === "viewer" ? "viewer" : "owner";
  SESSION_ME = s || null;
  setTimeout(applyLeakGuards, 0);
  document.documentElement.classList.toggle("viewer", isViewer());
  // Extra explanations (owner, Nov 10 2026): saved on their account, so the owner can turn it on or off for them
  // from Accounts and they can still change it in Settings
  document.documentElement.classList.toggle("tips", isViewer() && !!(s && s.tips));
  if (isViewer() && !VIEWER_TABS.has(state.tab)) state.tab = "today";
}
function guardTab() { if (isViewer() && !VIEWER_TABS.has(state.tab)) state.tab = "today"; }
// the picture on chart markers where you bought or sold: the owner's photo, or the family member's own
function myPhoto() {
  if (!isViewer()) return "/me.jpg";
  return SESSION_ME && SESSION_ME.photo ? "/api/me/photo?v=" + encodeURIComponent(SESSION_ME.photo) : "/no-photo";
}
function buyListHas(t) { return ((state.data?.watchlist?.buys) || []).some(r => r.t === t && r.action === "BUY"); }
const tip = html => `<div class="vtip">${html}</div>`;

// messages from an earlier tap (a sent order, a saved size) are cleared when they go to another page, so old ones
// don't pile up under new ones (Nov 4 click-through)
function clearViewerMsgs() { VA.msg = {}; VA.sizeMsg = null; VA.pauseMsg = null; VA.prefsMsg = null; VA.photoMsg = null; VA.bugMsg = null; if (VA.keyMsg && VA.keyMsg.ok) VA.keyMsg = null; }
const VA = { open: null, sellOpen: null, sellMode: {}, amt: {}, msg: {}, busy: false, keyMsg: null, keyBusy: false,
  prefs: null, prefsErr: null, prefsMsg: null, photoMsg: null, photoBusy: false, bugMsg: null, bugBusy: false, bugFrom: null, bugKind: "bug" };

// ---------- first time in: a profile picture (or skip) ----------
function needsPhotoStep() { return isViewer() && SESSION_ME && !SESSION_ME.photo_prompted; }
function renderPhotoStep() {
  const m = VA.photoMsg;
  return `<section class="panel"><div class="ph"><h2>Add a profile picture</h2></div>
    <p>It shows on a stock's chart at the spots where you bought or sold it, so you can see your own trades next to the members'. Only you see it.</p>
    <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:10px 0">
      <label class="buybtn go" style="cursor:pointer">${VA.photoBusy ? "Saving…" : "Choose a picture"}<input type="file" accept="image/*" id="vphoto" hidden></label>
      <button type="button" class="link" data-vphotoskip="1">Skip for now</button></div>
    ${m ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}
    <p class="foot">You can add or change it later in More → Settings.</p></section>`;
}
async function shrinkPhoto(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((ok, no) => { const i = new Image(); i.onload = () => ok(i); i.onerror = no; i.src = url; });
    const side = Math.min(img.naturalWidth, img.naturalHeight), c = document.createElement("canvas");
    c.width = c.height = 256;
    c.getContext("2d").drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side, 0, 0, 256, 256);
    return c.toDataURL("image/jpeg", 0.85);
  } finally { URL.revokeObjectURL(url); }
}
document.addEventListener("change", async ev => {
  if (ev.target.id !== "vphoto" || !isViewer() || !ev.target.files || !ev.target.files[0]) return;
  VA.photoBusy = true; VA.photoMsg = null; render();
  try {
    const data = await shrinkPhoto(ev.target.files[0]);
    const { js } = await api("/api/me/photo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ data }) });
    SESSION_ME.photo = js.photo; SESSION_ME.photo_prompted = true;
    VA.photoMsg = { ok: true, text: "Saved. It'll show on a stock's chart once you've traded it." };
  } catch (e) { VA.photoMsg = { ok: false, text: e.message && e.message !== "error 400" ? e.message : "That picture didn't work. Try another one." }; }
  VA.photoBusy = false; render();
});
document.addEventListener("click", async ev => {
  if (!isViewer()) return;
  if (ev.target.closest("[data-vphotoskip]")) {
    SESSION_ME.photo_prompted = true; render();
    try { await api("/api/me/photo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ skip: true }) }); } catch (e) {}
  }
  if (ev.target.closest("[data-vphotodel]") && confirm("Remove your profile picture?")) {
    try { await api("/api/me/photo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ remove: true }) }); SESSION_ME.photo = null; VA.photoMsg = { ok: true, text: "Removed." }; }
    catch (e) { VA.photoMsg = { ok: false, text: "Couldn't remove it. Try again." }; }
    render();
  }
});

// ---------- first time in, after the picture: turn on alerts (once per phone; alerts are per device) ----------
// Shown only where alerts can work (on iPhone: the home-screen app), and not again once on or skipped here.
let ALERT_STEP = null;     // null = not checked yet, true = show, false = don't
function alertStepSeen() { try { return localStorage.getItem("cc-alert-step") === "1"; } catch (e) { return false; } }
function alertStepDone() { try { localStorage.setItem("cc-alert-step", "1"); } catch (e) {} ALERT_STEP = false; }
function needsAlertStep() {
  if (!isViewer() || alertStepSeen()) return false;
  if (ALERT_STEP === null) {
    ALERT_STEP = false;
    alertsStatus().then(st => { if (st === "off") { ALERT_STEP = true; render(); } else if (st === "on") alertStepDone(); });
  }
  return ALERT_STEP;
}
function renderAlertStep() {
  return `<section class="panel"><div class="ph"><h2>Turn on alerts</h2></div>
    <p>Get a notification on this phone when the tool buys or sells in your account, plus a short summary after the market closes. Tapping one opens the app right to it.</p>
    <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:10px 0">
      <button type="button" class="buybtn go" data-valerton="1"${VA.alertBusy ? " disabled" : ""}>${VA.alertBusy ? "Turning on…" : "Turn on alerts"}</button>
      <button type="button" class="link" data-valertskip="1">Not now</button></div>
    <p class="foot">You can pick which alerts you get in More → Notifications.</p></section>`;
}
document.addEventListener("click", async ev => {
  if (!isViewer()) return;
  if (ev.target.closest("[data-valertskip]")) { alertStepDone(); render(); return; }
  if (ev.target.closest("[data-valerton]") && !VA.alertBusy) {
    VA.alertBusy = true; render();
    await enableAlerts();          // right from the tap, so the phone shows its permission question
    VA.alertBusy = false; alertStepDone(); render();
  }
});

// ---------- a few days in: ask once whether they want emails too ----------
function emailAskCard() {
  if (!isViewer() || !SESSION_ME || !SESSION_ME.ask_email) return "";
  return `<section class="panel vask"><div class="ph"><h2>want emails too?</h2></div>
    <p>Besides phone alerts, we can email you each trade in your account and a short summary after the market closes.</p>
    <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:8px">
      <button type="button" class="buybtn go" data-vemail="yes">yea</button>
      <button type="button" class="link" data-vemail="no">nay</button></div>
    <p class="foot">Change it anytime in More → Notifications.</p></section>`;
}
document.addEventListener("click", async ev => {
  const b = ev.target.closest && ev.target.closest("[data-vemail]");
  if (!b || !isViewer() || !SESSION_ME) return;
  SESSION_ME.ask_email = false; VA.prefs = null; render();
  try { await api("/api/me/email-ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ choice: b.dataset.vemail }) }); } catch (e) {}
});

// ---------- Today: a Buy button on each Buy candidate ----------
function vBuyArea(r) {
  const V0 = state.data?.viewer || {};
  if (!V0.connected || !V0.terms) return "";
  const held = (state.data?.portfolio?.positions || []).some(x => x.t === r.t);
  // trading off (owner, Nov 11 2026): the button stays, greyed out, with the info button. Their own pause also gets a
  // "Trading paused" link above it to the Pause switch; the family hold (trading not started) gets no label
  if (V0.hold || V0.paused) return `<div class="vlock">${!V0.hold ? `<button type="button" class="link vpausedl" data-vgopause="1">Trading paused</button>` : ""}
    <div class="vbtns"><button type="button" class="buybtn vb" disabled aria-disabled="true">${held ? "Buy more" : "Buy"}</button>${infoBtn()}</div></div>`;
  return `<button type="button" class="buybtn vb" data-vbuy="${esc(r.t)}">${VA.open === r.t ? "Close" : held ? "Buy more" : "Buy"}</button>`;
}
function vBuyBox(t, inTable) {
  const m = VA.msg[t];
  if (VA.open !== t) return m && !inTable ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : "";
  const L = state.data?.portfolio?.limits || {}, v = VA.amt[t] ?? (L.per_pick ? Math.round(L.per_pick * 0.25) : "");
  const held = (state.data?.portfolio?.positions || []).some(x => x.t === t);
  const box = `<div class="sellbox">
    <div class="note warn" style="margin:0 0 10px"><b>Before you ${held ? "add more" : "buy"}:</b> <button type="button" class="link" data-blog="buttons/buy-yourself">see the disclaimer on making your own buys</button></div>
    <label class="foot" for="vbuy-${esc(t)}">Amount in dollars</label>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:4px">
      <input id="vbuy-${esc(t)}" class="amt" type="number" inputmode="decimal" min="1" step="1" value="${esc(v)}" style="max-width:140px">
      <button type="button" class="buybtn go" data-vbuysend="${esc(t)}"${VA.busy ? " disabled" : ""}>${VA.busy ? "Sending…" : "Buy " + esc(t)}</button>
      <button type="button" class="link" data-vbuy="${esc(t)}">Cancel</button></div>
    <div class="foot">Real money. Fills now if the market is open, otherwise at the next open. If cash is short, some of the Treasury-bill fund is sold first.</div>
    ${m ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}</div>`;
  return inTable ? `<tr class="sellrow"><td colspan="6">${box}</td></tr>` : box;
}

// ---------- buy size (owner, Nov 3 2026): low / standard / high, from their balance ----------
const SIZE_K = { low: 0.5, standard: 1, high: 1.5 };
function sizePanel(d, ro) {
  const V = d.viewer || {}, L = (d.portfolio || {}).limits || {}, cur = V.connected ? (V.size || "standard") : (VA.size || V.size || "standard");
  const base = L.per_pick != null ? L.per_pick / (L.size_k || SIZE_K[L.size] || 1) : null;      // the standard amount
  const est = k => base != null ? ` · about ${money(base * SIZE_K[k])} a pick` : "";
  const opt = (k, l) => `<button type="button" class="seg1" data-vsize="${k}" aria-pressed="${cur === k}"${ro ? " disabled" : ""}>${l}</button>`;
  return `<section class="panel"><div class="ph"><h2>Buy size</h2></div>
    <div class="seg" role="group" aria-label="Buy size">${opt("low", "Low")}${opt("standard", "Standard")}${opt("high", "High")}</div>
    <p class="foot" style="margin-top:8px">How much each pick gets, from your account balance. <b>Low</b> is half the standard amount${est("low")}; <b>Standard</b> is an equal share of the account across the picks a holding period needs${est("standard")}; <b>High</b> is one and a half times${est("high")}. The tool never invests more than ${Math.round(L.max_invested_pct || 90)}% of the account whatever the size, so with High the account fills up with fewer picks and later picks wait for money to free up; with Low more of it stays as spare cash. ${V.connected ? "A change applies to new buys; positions already bought keep their size." : "You can change it any time later."}</p>
    ${VA.sizeMsg ? `<div class="tapmsg ${VA.sizeMsg.ok ? "pos" : "neg"}">${esc(VA.sizeMsg.text)}</div>` : ""}</section>`;
}
document.addEventListener("click", async ev => {
  const b = ev.target.closest("[data-vsize]");
  if (!b || !isViewer() || VA.sizeBusy) return;
  const size = b.dataset.vsize, d = state.data || {};
  if (!(d.viewer && d.viewer.connected)) { VA.size = size; render(); return; }      // not connected yet: sent with the keys
  VA.sizeBusy = true; VA.sizeMsg = null;
  try {
    const { js } = await api("/api/me/size", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ size }) });
    d.viewer.size = js.size; VA.sizeMsg = { ok: true, text: js.message || "Saved." };
  } catch (e) { VA.sizeMsg = { ok: false, text: e.message || "Couldn't save. Try again." }; }
  VA.sizeBusy = false; render();
});

// ---------- the Account tab for a family account (also the owner's read-only view of it) ----------
// opts.readOnly: the owner looking at someone's account (no buttons, no setup form)
function renderViewerAccount(d, opts = {}) {
  const V = d.viewer || {}, ro = !!opts.readOnly;
  if (!V.terms && !ro) return renderTermsSign(true);
  if (!V.connected && !ro) return renderConnect();
  if (!V.connected && ro) return `<div class="empty">They haven't connected their Alpaca account yet.</div>`;
  // before the first update there's no snapshot yet: the page shows zeros, with a note on what happens next
  const first = !d.portfolio;
  const p = d.portfolio || { equity: 0, cash: 0, day_pl: 0, positions: [], orders: [], limits: {}, updated: null, market_open: null };
  const firstNote = first ? `<div class="note">${VA.keyMsg ? `<b>${esc(VA.keyMsg.text)}</b> ` : ""}${ro ? "They're connected; this fills in at the next update."
      : V.hold ? "Your Alpaca account is connected. This page fills in with your account at the next update (within a few hours)."
      : V.paused ? "Your Alpaca account is connected. Trading is paused, so nothing is bought until you resume it."
      : V.auto === false ? "Your Alpaca account is connected. Auto-buying turned off by Operator: nothing is bought until the Operator turns it back on."
      : "Your Alpaca account is connected and the tool is buying the current picks for it now. This page fills in within a few minutes; pull down to refresh."}</div>` : "";
  const pos = p.positions || [], ords = p.orders || [], L = p.limits || {};
  const picks = pos.filter(x => x.bot && x.sleeve !== "park"), park = pos.filter(x => x.sleeve === "park"), own = pos.filter(x => !x.bot && x.sleeve !== "park");
  const mainP = picks.filter(x => x.sleeve !== "small"), smallP = picks.filter(x => x.sleeve === "small");
  const grp = a => { const v = a.reduce((s, x) => s + (x.mv || 0), 0), g = a.reduce((s, x) => s + (x.pl || 0), 0); return { v, g, pc: v - g > 0 ? g / (v - g) : 0, n: a.length }; };
  const GM = grp(mainP), GS = grp(smallP);
  const invested = picks.reduce((a, x) => a + (x.mv || 0), 0), gain = picks.reduce((a, x) => a + (x.pl || 0), 0), parked = park.reduce((a, x) => a + (x.mv || 0), 0);
  const C = d.close_report;
  const auto = V.hold ? `<span class="chip watch">View only for now</span>` : V.paused ? `<span class="chip avoid">Trading paused</span>` : V.auto !== false ? `<span class="chip BUY">Auto-buying on</span>` : `<span class="chip avoid">${ro ? "Auto-buying turned off by you" : "Auto-buying turned off by Operator"}</span>`;
  const row = x => {
    const held = x.sleeve === "park" ? `Cash waiting for picks<div class="foot">${V.park === "cash" ? "Being moved back to cash" : "In SGOV until the tool needs it"}</div>`
      : x.bot ? `${x.sleeve === "small" ? "Small-company pick · " : ""}${x.held ?? 0} of ${x.hold ?? L.hold_days ?? 60} days${x.bought ? `<div class="foot">Tool sells ~${esc(fmtDate(addTradingDays(x.bought, x.hold ?? L.hold_days ?? 60)))}; a winner may be kept longer</div>` : ""}`
      : `Yours<div class="foot">You bought this yourself; the tool leaves it alone</div>`;
    // trading off (owner, Nov 11 2026): greyed out instead of hidden, with the info button. While their own pause is
    // on only Buy greys out: selling stays open (the Terms promise they can sell any time)
    const off = !!(V.hold || V.paused);
    const bBtn = !buyListHas(x.t) ? "" : off ? `<button type="button" class="buybtn vb" disabled aria-disabled="true">Buy more</button>`
      : `<button type="button" class="buybtn vb" data-vbuy="${esc(x.t)}">${VA.open === x.t ? "Close" : "Buy more"}</button>`;
    const sBtn = V.hold ? `<button type="button" class="vsell" disabled aria-disabled="true">Sell</button>`
      : `<button type="button" class="vsell" data-vsell="${esc(x.t)}">${VA.sellOpen === x.t ? "Close" : "Sell"}</button>`;
    const btns = ro || x.sleeve === "park" ? "" : `<div class="vbtns">${bBtn}${sBtn}${off ? infoBtn() : ""}</div>`;
    const m = VA.msg[x.t];
    return `<tr data-hl="pos:${esc(x.t)}"><td class="mono">${tkBtn(x.t)}${m && VA.open !== x.t && VA.sellOpen !== x.t ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}</td><td class="n">${Number(x.qty).toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
      <td class="n">${money(x.avg, 2)}</td><td class="n">${money(x.mv)}</td><td class="n ${cls(x.pl)}">${pct(x.plpc)} · ${(x.pl > 0 ? "+" : "") + money(x.pl)}</td><td class="nw">${held}${btns}</td></tr>${ro ? "" : vBuyBox(x.t, true) + vSellRow(x)}`;
  };
  // Nov 4: the first family account was connected with nothing in it, and its page just said "Nothing bought yet"
  const empty = !first && !(p.equity >= 1) && !pos.length;
  const why = empty ? `<div class="note warn"><b>${ro ? "This Alpaca account has no money in it yet." : "Your Alpaca account doesn't have any money in it yet."}</b> ${ro ? "The tool buys the current picks once they deposit." : "Deposit money in the Alpaca app (Banking → Deposit). The tool buys the current picks as soon as the money shows up during market hours, or at the next update."}</div>`
    : V.auto === false && !V.paused && !V.hold ? `<div class="note">${ro ? "Auto-buying is off for this account." : "<b>Auto-buying turned off by Operator.</b> The tool won't buy anything new in your account until the Operator turns it back on. The tool still sells what it holds on its usual schedule."}</div>` : "";
  return `${ro ? "" : tip("<b>Your Account.</b> Everything here is your own real-money Alpaca account. The tool buys new picks for you as they're found, each with a share of your balance set by your buy size; spare cash waits the way you chose under Spare cash. Tap a ticker for its chart.")}
  <div class="filters"><span class="chip SHORT">Real money</span>${auto}<span class="foot">${p.updated ? `Updated ${esc(ago(p.updated))} (${esc(fmtET(p.updated))}) · market ${p.market_open ? "open" : "closed"}` : "Waiting for the first update"}</span></div>
  ${firstNote}${why}
  <div class="tiles">
    <div class="tile"><span class="v">${money(p.equity)}</span><span class="l">Account value</span><span class="c">Cash ${money(p.cash)}</span></div>
    <div class="tile"><span class="v ${cls(p.day_pl)}">${p.day_pl == null ? "–" : (p.day_pl > 0 ? "+" : "") + money(p.day_pl)}</span><span class="l">${esc(dayLabel(p))}</span></div>
    <div class="tile"><span class="v ${cls(GM.pc)}">${pct(GM.pc, 2)}</span><span class="l">Main picks</span><span class="c">${(GM.g > 0 ? "+" : "") + money(GM.g)} · ${money(GM.v)} in ${GM.n} pick${GM.n === 1 ? "" : "s"}</span></div>
    <div class="tile"><span class="v ${cls(GS.pc)}">${pct(GS.pc, 2)}</span><span class="l">Small-company buys</span><span class="c">${(GS.g > 0 ? "+" : "") + money(GS.g)} · ${money(GS.v)} in ${GS.n} stock${GS.n === 1 ? "" : "s"}</span></div>
    <div class="tile"><span class="v">${money(V.park === "cash" ? Math.max(0, +p.cash || 0) + parked : parked)}</span><span class="l">${V.park === "cash" ? "Cash waiting for picks" : "Waiting for picks"}</span><span class="c">${V.park === "cash" ? "Kept as cash" : "In SGOV"}</span></div>
    ${!(C && C.since_start != null) ? `<div class="tile"><span class="v">${money(0)}</span><span class="l">Gain since start</span><span class="c">${pct(0, 2)} · starts counting at the first market close</span></div>` : ""}
    ${C && C.since_start != null ? `<div class="tile"><span class="v ${cls(C.gain ?? C.since_start)}">${C.gain != null ? (C.gain > 0 ? "+" : "") + money(C.gain) : pct(C.since_start, 2)}</span><span class="l">Gain since ${esc(fmtDate(C.start))}</span><span class="c">${pct(C.since_start, 2)}${C.net_in ? ` · deposits not counted` : ""} · last close ${esc(fmtDate(C.date))}</span></div>` : ""}
    ${C && C.since_start != null && C.spy_since_start != null ? `<div class="tile"><span class="v ${cls(C.since_start - C.spy_since_start)}">${pnum((C.since_start - C.spy_since_start) * 100, 2)}</span><span class="l">${C.since_start >= C.spy_since_start ? "Ahead of" : "Behind"} the S&amp;P since start</span><span class="c">You ${pct(C.since_start, 2)} · S&amp;P ${pct(C.spy_since_start, 2)}</span></div>`
      : `<div class="tile"><span class="v">${pnum(0, 2)}</span><span class="l">Vs the S&amp;P since start</span><span class="c">Starts counting at the first market close</span></div>`}
  </div>
  ${parkPanel(d, ro)}
  ${sizePanel(d, ro)}
  ${(() => {
    const head = `<thead><tr><th>Ticker</th><th class="n">Shares</th><th class="n">Avg cost</th><th class="n">Value</th><th class="n">Gain</th><th>Held</th></tr></thead>`;
    const sec = (title, sub, list, none) => `<section class="panel"><div class="ph"><h2>${title}</h2><span class="sub">${sub}</span></div>
      ${list.length ? `<div class="tbl"><table>${head}<tbody>${list.map(row).join("")}</tbody></table></div>` : `<div class="empty">${none}</div>`}</section>`;
    const nothing = empty ? "Nothing bought yet: there's no money in the account yet." : V.hold ? "Nothing bought yet: trading hasn't started." : "Nothing bought yet. New picks are bought as they come in.";
    return sec("Main picks", `${GM.n} · ${pct(GM.pc, 2)} · ${(GM.g > 0 ? "+" : "") + money(GM.g)}`, mainP, nothing)
      + sec("Small-company buys", `${GS.n} · ${pct(GS.pc, 2)} · ${(GS.g > 0 ? "+" : "") + money(GS.g)}`, smallP, "None yet. Purchases of companies under $2B are bought here and held longer.")
      + (own.length || park.length ? sec("Other holdings", "Bought by you, and cash waiting for picks", [...own, ...park], "") : "");
  })()}
  <section class="panel"><div class="ph"><h2>How it works</h2></div>
    ${pos.length ? `
    <p class="foot">The tool buys each new pick with an equal share of the account, sells it at the end of its hold (or keeps a winner while it's ahead of the S&amp;P 500), and keeps spare cash in a Treasury-bill fund. Most picks are held about ${(d.adaptive?.policy?.hold_other) ?? L.hold_days ?? 60} trading days.${ro ? "" : " Buy more shows on stocks that are on today's Buy list."}</p>`
    : `<p class="foot">The tool buys each new pick with an equal share of the account and sells it at the end of its hold (or keeps a winner while it's ahead of the S&amp;P 500).</p>`}</section>
  ${vDaysSection(d)}
  ${divSection({ ...p, mode: "live" })}
  <section class="panel"><div class="ph"><h2>Recent orders</h2></div>
    ${ords.length ? `<div class="tbl scroll"><table><thead><tr><th>When</th><th>Ticker</th><th>Side</th><th class="n">Shares</th><th class="n">Fill price</th><th>Status</th></tr></thead><tbody>
    ${ords.filter(o => !/^(SGOV|BIL|SHV|SPY|VOO|IVV)$/.test(o.t) || o.by !== "tool").map(o => `<tr><td class="nw">${esc(fmtET(o.at))}</td><td class="mono">${tkBtn(o.t)}</td><td>${esc(o.side)}</td><td class="n">${shQty(o)}</td><td class="n">${o.filled_px ? money(+o.filled_px, 2) : "–"}</td><td>${esc(String(o.status).replace(/_/g, " "))}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="empty">No orders yet.</div>`}</section>
  ${ro || V.hold ? "" : pausePanel(V)}`;
}

// Sell: any position but the parked fund, whenever they want, with a note on what selling early means
function vSellRow(x) {
  if (VA.sellOpen !== x.t) return "";
  const t = x.t, mode = VA.sellMode[t] || "all", m = VA.msg[t], sh = Number(x.qty).toLocaleString(undefined, { maximumFractionDigits: 4 });
  const opt = (k, l) => `<button type="button" class="seg1" data-vsellmode="${esc(t)}|${k}" aria-pressed="${mode === k}">${l}</button>`;
  const cost = x.avg != null && x.qty ? +x.avg * +x.qty : null, up = cost != null && (+x.mv || 0) > cost * 1.01;
  return `<tr class="sellrow"><td colspan="6"><div class="sellbox">
    <div class="note warn" style="margin:0 0 10px"><b>Before you sell:</b> <button type="button" class="link" data-blog="buttons/sell-yourself">see the disclaimer on making your own sales</button></div>
    <div class="seg" role="group" aria-label="How much">${opt("all", "All")}${opt("shares", "Shares")}${opt("dollars", "$ amount")}${cost != null ? opt("initial", "Initial investment") : ""}</div>
    ${mode === "all" ? `<p style="margin:8px 0">Sell all ${sh} shares of ${esc(t)} (about ${money(x.mv)}).</p>`
      : mode === "initial" ? `<p style="margin:8px 0">${up ? `Sells about <b>${money(cost, 2)}</b> (what you paid) and keeps about <b class="pos">${money((+x.mv || 0) - cost, 2)}</b> in ${esc(t)}.` : `It isn't worth more than you paid, so this would sell all of it. Use All instead.`}</p>`
      : `<div style="margin-top:8px"><input id="vsell-${esc(t)}" class="amt" type="number" inputmode="decimal" min="0" step="any" value="${esc(VA.amt["s" + t] ?? "")}" placeholder="${mode === "shares" ? "Shares" : "Dollars"}" style="max-width:140px"></div>`}
    <div style="display:flex;gap:10px;align-items:center;margin-top:8px"><button type="button" class="vsell go" data-vsellsend="${esc(t)}"${VA.busy || (mode === "initial" && !up) ? " disabled" : ""}>${VA.busy ? "Sending…" : "Send sell order"}</button>
      <button type="button" class="link" data-vsell="${esc(t)}">Cancel</button></div>
    <div class="foot">Real money. Fills now if the market is open, otherwise at the next open.</div>
    ${m ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}</div></td></tr>`;
}

function renderConnect() {
  const m = VA.keyMsg;
  return `<section class="panel"><div class="ph"><h2>Connect your Alpaca account</h2></div>
  <p>The tool trades your own real-money Alpaca account: it buys the current picks as soon as you connect, then each new pick as it comes in, sized from your balance (pick a buy size below), sells on its schedule, and handles your spare cash the way you choose below. It needs a key from Alpaca to do that. You do this once.</p>
  <ol class="steps">
    <li>On a computer or phone browser, sign in at <b>app.alpaca.markets</b> and make sure you're in your <b>Live</b> account (not Paper) at the top left.</li>
    <li>On the home page, find <b>API Keys</b> on the right and tap <b>Generate New Keys</b>.</li>
    <li>Copy the <b>Key</b> and the <b>Secret</b> into the boxes below. Alpaca shows the secret only once.</li>
  </ol>
  <form id="vkeys" class="keysform" autocomplete="off">
    <input id="vk" type="text" placeholder="Key (starts with AK)" autocapitalize="characters" spellcheck="false" aria-label="Alpaca key" required>
    <input id="vs" type="password" placeholder="Secret" spellcheck="false" aria-label="Alpaca secret" required>
    <button type="submit" class="buybtn go"${VA.keyBusy ? " disabled" : ""}>${VA.keyBusy ? "Checking with Alpaca…" : "Connect"}</button>
  </form>
  ${m ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}
  <p class="foot">The key is stored encrypted and is never shown again, here or anywhere else. It lets the tool place trades; it can't move money out of your account. To stop the tool, delete the key in Alpaca at any time.</p></section>${parkPanel(state.data || {}, false)}${sizePanel(state.data || {}, false)}`;
}

document.addEventListener("submit", async ev => {
  if (ev.target.id !== "vkeys") return;
  ev.preventDefault();
  if (VA.keyBusy) return;
  // Nov 3: the boxes were read after render() had redrawn the form, so they were always empty and every try came back
  // "Neither box looks like an Alpaca key". Read them first; put them back if Alpaca says no, so they needn't re-paste.
  const key = $("#vk").value, secret = $("#vs").value;
  if (!VA.park) { VA.keyMsg = { ok: false, text: "Choose what happens to your spare cash (below) before connecting." }; render(); const k = $("#vk"), s2 = $("#vs"); if (k) k.value = key; if (s2) s2.value = secret; return; }
  VA.keyBusy = true; VA.keyMsg = null; render();
  let ok = false;
  try {
    const { js } = await api("/api/me/keys", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key, secret, size: VA.size || "standard", park: VA.park || "" }) });
    ok = true;
    VA.keyMsg = { ok: true, text: js.message || "Connected." };
    if (state.data) state.data.viewer = { ...(state.data.viewer || {}), connected: true };
    if (SESSION_ME) SESSION_ME.connected = true;
  } catch (e) { VA.keyMsg = { ok: false, text: e.message || "Couldn't connect. Try again." }; }
  VA.keyBusy = false; render();
  if (!ok) { const k = $("#vk"), s = $("#vs"); if (k) k.value = key; if (s) s.value = secret; }
});

const errText = e => e && e.message && !/^error \d+$/.test(e.message) ? e.message : (WHY_BUY[e && e.why] || (e && e.why) || "try again");
document.addEventListener("click", async ev => {
  if (!isViewer()) return;
  const o = ev.target.closest("[data-vbuy]");
  if (o) { const t = o.dataset.vbuy; VA.open = VA.open === t ? null : t; VA.sellOpen = null; delete VA.msg[t]; render(); return; }
  const so = ev.target.closest("[data-vsell]");
  if (so) { const t = so.dataset.vsell; VA.sellOpen = VA.sellOpen === t ? null : t; VA.open = null; delete VA.msg[t]; render(); return; }
  const sm = ev.target.closest("[data-vsellmode]");
  if (sm) { const [t, k] = sm.dataset.vsellmode.split("|"); VA.sellMode[t] = k; render(); return; }
  const s = ev.target.closest("[data-vbuysend]");
  if (s && !VA.busy) {
    const t = s.dataset.vbuysend, el = document.getElementById("vbuy-" + t), dollars = parseFloat(el && el.value);
    VA.amt[t] = el ? el.value : "";
    if (!(dollars >= 1)) { VA.msg[t] = { ok: false, text: "Enter an amount of $1 or more." }; render(); return; }
    VA.busy = true; render();
    try {
      const { js } = await api("/api/buy-more", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t, dollars }) });
      VA.msg[t] = { ok: true, text: `Order sent: ${money(dollars)} of ${t}` + (js.market_open ? "." : ". It fills at the next market open.") };
      VA.open = null; delete VA.amt[t];
      setTimeout(loadLive, 4000);
    } catch (e) { VA.msg[t] = { ok: false, text: "Not sent: " + errText(e) + "." }; }
    VA.busy = false; render(); return;
  }
  const ss = ev.target.closest("[data-vsellsend]");
  if (ss && !VA.busy) {
    const t = ss.dataset.vsellsend, mode = VA.sellMode[t] || "all", el = document.getElementById("vsell-" + t), amount = el ? parseFloat(el.value) : null;
    if (el) VA.amt["s" + t] = el.value;
    if (mode !== "all" && mode !== "initial" && !(amount > 0)) { VA.msg[t] = { ok: false, text: mode === "shares" ? "Enter how many shares." : "Enter a dollar amount." }; render(); return; }
    VA.busy = true; render();
    try {
      const { js } = await api("/api/sell", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t, mode, amount }) });
      VA.msg[t] = { ok: true, text: `Sell order sent${js.all ? ` for all of ${t}` : ` for ${js.qty ? js.qty + " shares" : money(js.notional)} of ${t}`}` + (js.market_open ? "." : ". It fills at the next market open.") };
      VA.sellOpen = null; delete VA.amt["s" + t];
      setTimeout(loadLive, 4000);
    } catch (e) { VA.msg[t] = { ok: false, text: "Not sent: " + errText(e) + "." }; }
    VA.busy = false; render();
  }
});

// ---------- their More menu and its pages ----------
function vBack() { return `<button type="button" class="moreback" data-vok="1" data-more="more">‹ More</button>`; }
function renderViewerMore() {
  return `<section class="panel"><div class="morelist">${VIEWER_MORE.map(m => `<button type="button" class="moreitem" data-vok="1" data-more="${m.k}"><span class="ml">${esc(m.l)}</span><span class="md">${esc(m.d)}</span><span class="mc" aria-hidden="true">›</span></button>`).join("")}</div></section>
  <div class="legal"><button type="button" class="link" data-vok="1" data-more="terms">Terms of Use</button><span aria-hidden="true">·</span><button type="button" class="link" data-vok="1" data-more="privacy">Privacy Policy</button></div>${versionLine()}`;
}
function renderViewerPage() {
  if (state.tab === "more") return renderViewerMore();
  if (state.tab === "vnotif") return vBack() + renderVNotif();
  if (state.tab === "vset") return vBack() + renderVSettings() + signInPanel();
  if (state.tab === "faq") return vBack() + renderFaq();
  if (state.tab === "blog") return renderBlog();
  if (state.tab === "bug") return vBack() + renderBug();
  if (state.tab === "terms") return vBack() + renderTermsPage();
  if (state.tab === "privacy") return vBack() + renderPrivacy();
  if (state.tab === "tax") return vBack() + renderVTax();
  return "";
}

const KIND_L = { fills: ["Trades", "when the tool buys or sells in your account, or an order of yours fills"],
  close: ["Market close", "a short summary after 4 PM: your value, the day's change and your gain since you started"],
  moves: ["Big moves", "a stock you hold moves 3%+ in a day or 7%+ in 5 days against the market, with why"],
  balance: ["Your account alerts", "the alerts you set up below"] };
const BAL_L = { above: "Account value goes above $", below: "Account value drops below $", day_up: "Up in a day by %", day_down: "Down in a day by %" };
function prefsLoad() {
  if (VA.prefs || VA.prefsLoading) return;
  VA.prefsLoading = true;
  api("/api/me/prefs").then(r => { VA.prefs = r.js; VA.prefsErr = null; }).catch(e => { VA.prefsErr = e; })
    .finally(() => { VA.prefsLoading = false; if (state.tab === "vnotif") render(); });
}
async function prefsSave(patch, okText) {
  try { const { js } = await api("/api/me/prefs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }); VA.prefs = js.prefs; VA.prefsMsg = { ok: true, text: okText || "Saved." }; }
  catch (e) { VA.prefsMsg = { ok: false, text: "Couldn't save. Try again." }; }
  render();
}
function renderVNotif() {
  prefsLoad();
  const P = VA.prefs, m = VA.prefsMsg;
  if (!P) return `<div class="empty">${VA.prefsErr ? esc(errorText(VA.prefsErr)) : "Loading…"}</div>`;
  const box = (side, k) => `<input type="checkbox" data-vpref="${side}|${k}"${P[side][k] ? " checked" : ""} aria-label="${side} for ${KIND_L[k][0]}">`;
  return `<section class="panel"><div class="ph"><h2>Notifications</h2><span class="sub">What reaches you, and how</span></div>
    ${m ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}
    <div class="tbl"><table><thead><tr><th>Alert</th><th class="n">Push</th><th class="n">Email</th></tr></thead><tbody>
    ${Object.keys(KIND_L).map(k => `<tr><td><b>${KIND_L[k][0]}</b><div class="foot" style="white-space:normal">${KIND_L[k][1]}</div></td><td class="n">${box("push", k)}</td><td class="n">${box("email", k)}</td></tr>`).join("")}
    </tbody></table></div>
    <p class="foot">Push alerts need alerts turned on in each phone or browser you use: tap <b>Turn on alerts</b> at the top of the app (on iPhone, from the app on your home screen). Everything also stays in the Alerts list. You only get alerts about your own account.</p></section>
  <section class="panel"><div class="ph"><h2>Your account alerts</h2><span class="sub">Each one fires at most once a day, during market hours</span></div>
    ${(P.balance || []).length ? `<ul class="vrules">${P.balance.map(r => `<li><span>${esc(BAL_L[r.kind].replace(/\$$/, "$" + Number(r.value).toLocaleString()).replace(/%$/, Number(r.value) + "%"))}</span><button type="button" class="link" data-vruledel="${esc(r.id)}">Remove</button></li>`).join("")}</ul>` : `<div class="empty">None yet.</div>`}
    <form id="vrule" class="keysform" style="grid-template-columns:minmax(0,1fr) 92px auto;align-items:center;max-width:520px">
      <select id="vrk" aria-label="Kind">${Object.entries(BAL_L).map(([k, l]) => `<option value="${k}">${esc(l.replace(/ (\$|%)$/, ""))}</option>`).join("")}</select>
      <input id="vrv" type="number" inputmode="decimal" min="0" step="any" placeholder="Amount" aria-label="Amount" required>
      <button type="submit" class="buybtn go">Add</button></form>
    <p class="foot">Dollar alerts use your whole account value; day alerts use the percent change since yesterday's close.</p></section>`;
}
document.addEventListener("change", ev => {
  const b = ev.target.closest && ev.target.closest("[data-vpref]");
  if (!b || !isViewer()) return;
  const [side, k] = b.dataset.vpref.split("|");
  prefsSave({ [side]: { [k]: b.checked } });
});
document.addEventListener("submit", ev => {
  if (ev.target.id !== "vrule" || !isViewer()) return;
  ev.preventDefault();
  const kind = $("#vrk").value, value = parseFloat($("#vrv").value);
  if (!(value > 0)) return;
  if (kind.startsWith("day") && value > 50) { VA.prefsMsg = { ok: false, text: "Day alerts go up to 50%." }; render(); return; }
  prefsSave({ balance: [...(VA.prefs.balance || []), { kind, value }] }, "Alert added.");
});
document.addEventListener("click", ev => {
  const d = ev.target.closest("[data-vruledel]");
  if (!d || !isViewer() || !VA.prefs) return;
  prefsSave({ balance: VA.prefs.balance.filter(r => r.id !== d.dataset.vruledel) }, "Alert removed.");
});

function renderVSettings() {
  const tips = !!(SESSION_ME && SESSION_ME.tips);
  const m = VA.photoMsg, ph = SESSION_ME && SESSION_ME.photo;
  return `<section class="panel"><div class="ph"><h2>Profile picture</h2></div>
    <div style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
      ${ph ? `<img src="${esc(myPhoto())}" alt="" class="vme">` : `<span class="vme none" aria-hidden="true"></span>`}
      <label class="buybtn go" style="cursor:pointer">${VA.photoBusy ? "Saving…" : ph ? "Change picture" : "Add a picture"}<input type="file" accept="image/*" id="vphoto" hidden></label>
      ${ph ? `<button type="button" class="link" data-vphotodel="1">Remove</button>` : ""}</div>
    ${m ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}
    <p class="foot">It shows on a stock's chart at the spots where you bought or sold it. Only you see it.</p></section>
  <section class="panel"><div class="ph"><h2>Extra explanations</h2></div>
    <label class="vswitch"><input type="checkbox" id="vtips"${tips ? " checked" : ""}> Show short explanations on Today and Account</label>
    <p class="foot">Handy at first; turn it off once you know your way around. Tap any ticker for its chart and "What's going on".</p></section>
  <section class="panel"><div class="ph"><h2>Home-screen widget</h2></div>
    <p>A widget for your iPhone home screen with your account value, today's change and you vs the S&amp;P 500. It only reads; it can't trade.</p>
    <button type="button" class="buybtn" data-vwidget="1">Set up the widget</button></section>
  <section class="panel"><div class="ph"><h2>Sign out</h2></div>
    <p class="foot">Signs this phone out. To sign back in, ask for a new link with your email.</p>
    <button type="button" class="vsell" data-vsignout="1">Sign out</button></section>`;
}
document.addEventListener("change", ev => {
  if (ev.target.id !== "vtips") return;
  const box = ev.target, on = box.checked;
  document.documentElement.classList.toggle("tips", on);
  box.disabled = true;
  api("/api/me/tips", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on }) })
    .then(() => { if (SESSION_ME) SESSION_ME.tips = on; })
    .catch(() => { box.checked = !on; document.documentElement.classList.toggle("tips", !on); alert("Couldn't save that. Try again."); })
    .finally(() => { box.disabled = false; });
});
document.addEventListener("click", async ev => {
  if (!isViewer()) return;
  if (ev.target.closest("[data-vwidget]")) { widgetModal(); return; }
  if (ev.target.closest("[data-vsignout]") && confirm("Sign out on this phone?")) {
    try { await fetch("/api/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", credentials: "same-origin" }); } catch (e) {}
    location.replace("/");
  }
});

function renderFaq() {
  const hold = state.data?.adaptive?.policy?.hold_other ?? 60;
  const qa = [
    ["What does the tool do?", "Members of Congress have to report their stock trades. The tool reads every report, scores each purchase on dozens of signals (the member's track record, their committees, the size and timing of the trade and more) and puts the ones that score near the top of all past purchases on the Buy list. It buys those for you automatically."],
    ["How much does it buy?", "Every pick gets the same share of your account. The weekly check keeps testing that against giving stronger picks more, and same-size has stayed ahead. About 30% of the money goes to a separate group of small-company picks held longer."],
    ["When does it sell?", `Most picks are held about ${hold} trading days (roughly ${Math.round(hold / 21)} months). At the end, a pick that's ahead of the S&P 500 is kept and checked every day, and sold once it falls behind. These rules change only when the weekly check finds something clearly better.`],
    ["Where's my cash?", "You choose, under Spare cash on your Account page. SGOV: money not in a pick waits in SGOV, a fund of short-term U.S. Treasury bills that earns about what they pay and barely moves in price; the tool sells some when a new pick needs the money. Cash: it stays as cash in your Alpaca account (turn on Alpaca's High-Yield Cash in your Alpaca dashboard to earn interest on it). You can switch any time."],
    ["Can I buy or sell myself?", "You can buy a stock that's on today's Buy list (it's then handled like the tool's own buy) and sell anything you hold, any time, from Account. Both show a note first: the tool's results come from sticking to its rules."],
    ["What if I sell a pick early?", "It's sold right away (or at the next open). The tool won't buy that stock back for the same pick for 45 days. If you sell part, the rest stays under the tool's rules."],
    ["What do I see on a chart?", "The stock's price with every member's buys and sales on it, your own buys and sales (with your picture, if you added one), and \"What's going on\": a recent big move explained and the latest headlines."],
    ["Why isn't a stock I like on the list?", "A stock is only on the Buy list when a member bought it recently and the purchase scores in the top of all past purchases. Company search shows any stock's chart and members' trades."],
    ["Is this guaranteed to make money?", "No. The strategy is tested on past years, and past results don't promise future ones. It can lose money, sometimes for a long stretch. Only put in money you're comfortable leaving for a while."],
    ["Taxes?", "Alpaca sends you a 1099 each year with every sale. Selling a stock you held under a year is a short-term gain or loss, taxed like regular income."],
    ["How do I stop it?", "Turn on Pause trading at the bottom of your Account page: the tool stops buying and selling and holds what you have until you resume. Deleting the key in your Alpaca account also stops it for good. What you hold stays yours either way."],
  ];
  return `<section class="panel"><div class="ph"><h2>Questions and answers</h2></div>
    <div class="faq">${qa.map(([q, a]) => `<details><summary>${esc(q)}</summary><p>${esc(a)}</p></details>`).join("")}</div>
    <p class="foot">Something not covered here? Use Report a bug or suggest a feature and ask; it goes to the Operator.</p></section>`;
}
function renderBug() {
  const m = VA.bugMsg, idea = VA.bugKind === "idea";
  const opt = (k, l) => `<button type="button" data-bugkind="${k}" aria-pressed="${VA.bugKind === k}">${l}</button>`;
  return `<section class="panel"><div class="ph"><h2>${idea ? "Suggest a feature" : "Report a bug"}</h2></div>
    <div class="seg" style="margin-bottom:4px;width:fit-content">${opt("bug", "Report a bug")}${opt("idea", "Suggest a feature")}</div>
    <p>${idea ? "What would you like the app to do? What would it help with? It's emailed to the Operator."
              : "What happened, and what did you expect? Which page were you on? It's emailed to the Operator along with your device and connection details."}</p>
    <form id="vbug" class="keysform" style="max-width:560px">
      <textarea id="vbugt" rows="6" maxlength="4000" placeholder="${idea ? "Describe your idea" : "Describe the problem"}" required style="font:15px/1.4 var(--body);padding:10px 12px;border:1px solid var(--rule);border-radius:10px;background:var(--surface);color:var(--ink);width:100%;box-sizing:border-box"></textarea>
      <button type="submit" class="buybtn go"${VA.bugBusy ? " disabled" : ""}>${VA.bugBusy ? "Sending…" : "Send"}</button></form>
    ${m ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}</section>`;
}
document.addEventListener("click", ev => {
  const b = ev.target.closest && ev.target.closest("[data-bugkind]");
  if (!b || !isViewer()) return;
  const t = $("#vbugt"), draft = t ? t.value : "";
  VA.bugKind = b.dataset.bugkind === "idea" ? "idea" : "bug"; VA.bugMsg = null; render();
  const t2 = $("#vbugt"); if (t2) t2.value = draft;
});
document.addEventListener("submit", async ev => {
  if (ev.target.id !== "vbug" || !isViewer() || VA.bugBusy) return;
  ev.preventDefault();
  const text = $("#vbugt").value;
  VA.bugBusy = true; VA.bugMsg = null; render();
  try { const { js } = await api("/api/bug", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, kind: VA.bugKind, page: VA.bugFrom || "" }) }); VA.bugMsg = { ok: true, text: js.message || "Sent." }; }
  catch (e) { VA.bugMsg = { ok: false, text: errText(e) }; }
  VA.bugBusy = false; render();
});

// ---------- the owner's Accounts page ----------
const ACC = { list: null, err: null, loading: false, busy: false, msg: null, view: null, data: null, live: null, adding: false };
function accLoad(force) {
  if (ACC.loading || (ACC.list && !force)) return;
  ACC.loading = true;
  api("/api/accounts").then(r => { ACC.list = r.js.accounts || []; ACC.hold = r.js.hold !== false; ACC.maint = r.js.maint || { on: true }; ACC.err = null; }).catch(e => { ACC.err = e; })
    .finally(() => { ACC.loading = false; if (state.tab === "accounts") render(); });
}
async function accPost(body) {
  ACC.busy = true; render();
  try { const { js } = await api("/api/accounts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); ACC.msg = { ok: true, text: js.message || "Done." }; }
  catch (e) { ACC.msg = { ok: false, text: e.message || "That didn't work. Try again." }; }
  ACC.busy = false; accLoad(true);
}
async function accOpen(id) {
  ACC.view = id; ACC.data = null; ACC.live = null; render(); window.scrollTo(0, 0);
  try { ACC.data = (await api("/api/accounts/view?id=" + encodeURIComponent(id))).js; } catch (e) { ACC.data = { error: e.message || "Couldn't load it." }; }
  render();
  try {
    const L = (await api("/api/accounts/live?id=" + encodeURIComponent(id))).js, p = ACC.data && ACC.data.portfolio;
    if (p && L) {
      const old = Object.fromEntries((p.positions || []).map(x => [x.t, x]));
      p.positions = (L.positions || []).map(x => ({ bot: false, sleeve: null, hold: null, bought: null, held: null, ...(old[x.t] || {}), ...x })).sort((a, b) => (b.mv || 0) - (a.mv || 0));
      if (L.equity != null) { p.equity = L.equity; p.cash = L.cash; if (L.last_equity) p.day_pl = L.equity - L.last_equity; }
      p.market_open = L.market_open; p.updated = L.at;
      render();
    }
  } catch (e) {}
}
function renderAccounts() {
  if (isViewer()) return "";
  accLoad(false);
  if (ACC.view) {
    const D = ACC.data, a = D && D.account;
    const head = `<button type="button" class="moreback" data-accback="1">‹ Accounts</button>`;
    if (!D) return head + `<div class="empty">Loading…</div>`;
    if (D.terms) return head + (D.signed ? termsSignedDoc(D.signed) : `<div class="empty">Not signed yet.</div>`);
    if (D.error) return head + `<div class="note err">${esc(D.error)}</div>`;
    return head + `<section class="panel"><div class="ph"><h2>${esc(a.label)}</h2><span class="sub">${esc(a.email)} · view only</span></div>
      ${D.problem ? `<div class="note warn">${esc(D.problem)}</div>` : ""}</section>` +
      renderViewerAccount({ viewer: { connected: a.keys, auto: a.auto, size: a.size, paused: a.paused, terms: !!a.terms_at, hold: ACC.hold, park: a.park }, portfolio: D.portfolio, close_report: D.close_report, close_history: D.close_history, adaptive: state.data?.adaptive }, { readOnly: true });
  }
  const m = ACC.msg;
  const list = ACC.list;
  const card = a => {
    const S = a.summary, st = a.status === "disabled" ? `<span class="chip avoid">Off</span>` : a.status === "invited" ? `<span class="chip watch">Invited</span>` : `<span class="chip BUY">Active</span>`;
    const conn = a.keys ? `Alpaca connected ${esc(a.key_hint || "")}` : "Hasn't connected Alpaca yet";
    // kind: "go" = the next step / turn something on, "" = neutral, "warn" = stops something, "danger" = can't be undone easily
    const btn = (act, l, extra = "", kind = "") => `<button type="button" class="btn sm ${kind === "go" ? "" : kind === "warn" ? "ghost bwarn" : kind === "danger" ? "ghost bdanger" : "ghost"}" data-acc="${act}" data-id="${esc(a.id)}"${ACC.busy ? " disabled" : ""}${extra}>${l}</button>`;
    // what the tool will do for them right now (the family hold comes first)
    const ready = a.keys && a.terms_at && a.status !== "disabled";
    const trade = a.paused ? `<span class="chip avoid">They paused trading</span>`
      : !a.keys ? "" : ACC.hold ? (a.auto ? `<span class="chip watch">On hold · ready${ready ? "" : " once terms are signed"}</span>` : `<span class="chip avoid">On hold · auto-buying off</span>`)
      : a.auto ? `<span class="chip BUY">Auto-buying on</span>` : `<span class="chip avoid">Auto-buying paused</span>`;
    const seenAgo = a.seen_at ? (Date.now() - Date.parse(a.seen_at)) / 60000 : null;
    const seen = seenAgo != null && seenAgo < 5 ? `<span class="chip BUY">Online now</span>` : "";
    const active = a.seen_at ? ` · last active ${esc(ago(a.seen_at))}` : "";
    return `<article class="acct">
      <div class="acct-h"><b>${esc(a.label)}</b>${seen}${st}${trade}${a.terms_at ? `<span class="chip watch">Terms signed</span>` : `<span class="chip avoid">Terms not signed</span>`}</div>
      <div class="foot">${esc(a.email)} · ${conn}${a.build ? ` · app ${esc(a.build)}${SERVER_BUILD && a.build !== SERVER_BUILD ? " (older)" : " (latest)"}` : ""}${seen ? "" : active}${a.last_login ? ` · last signed in ${esc(ago(a.last_login))}` : a.link_sent ? ` · invite sent ${esc(ago(a.link_sent))}` : ""}</div>
      ${S ? `<div class="acct-s"><span><b>${money(S.equity)}</b> value</span><span class="${cls(S.day_pl)}">${S.day_pl == null ? "–" : (S.day_pl > 0 ? "+" : "") + money(S.day_pl)} today</span><span>${money(S.invested)} in picks</span><span>${S.positions} position${S.positions === 1 ? "" : "s"}</span><span class="foot">updated ${esc(ago(S.updated))}</span></div>` : ""}
      ${a.problem ? `<div class="foot neg">${esc(a.problem)}</div>` : ""}
      <div class="acct-b">${a.keys ? `<button type="button" class="btn sm" data-accview="${esc(a.id)}">View portfolio</button>` : ""}
        ${a.terms_at ? `<button type="button" class="btn ghost sm" data-accterms="${esc(a.id)}">Signed terms</button>` : ""}
        ${a.keys ? btn("auto", a.auto ? (ACC.hold ? "Keep paused at launch" : "Pause auto-buying") : (ACC.hold ? "Start with everyone at launch" : "Turn auto-buying on"), ` data-on="${a.auto ? "0" : "1"}"`, a.auto ? "warn" : "go") : ""}
        ${btn("tips", a.tips ? "Extra explanations: on" : "Extra explanations: off", ` data-on="${a.tips ? "0" : "1"}"`)}
        ${a.status !== "disabled" ? btn("link", "Send invite") : ""}
        ${a.status !== "disabled" ? btn("signout", "Sign out everywhere", "", "warn") : ""}
        ${a.keys ? btn("reset-keys", "Reset keys", "", "warn") : ""}
        ${a.status === "disabled" ? btn("enable", "Turn back on", "", "go") : btn("disable", "Turn off", "", "danger")}
        ${btn("remove", "Remove", "", "danger")}</div>
    </article>`;
  };
  const holdBox = list ? `<div class="note ${ACC.hold ? "warn" : ""}" style="display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap">
      <span>${ACC.hold ? "<b>Family trading is on hold.</b> Every family account is view-only: they can sign the terms and connect Alpaca, but nothing is bought or sold for them and their Buy and Sell buttons are off." : "<b>Family trading is on.</b> The tool trades every connected family account that has signed the terms."}</span>
      <button type="button" class="btn sm${ACC.hold ? "" : " ghost"}" data-acchold="${ACC.hold ? "0" : "1"}"${ACC.busy ? " disabled" : ""}>${ACC.hold ? "Start family trading" : "Put on hold"}</button></div>` : "";
  const M = ACC.maint || { on: true };
  const maintBox = list ? `<div class="note ${M.on ? "err" : ""}" style="display:grid;gap:8px">
      <div style="display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap">
        <span>${M.on ? "<b>Maintenance is on.</b> Family accounts see a \"Down for maintenance\" screen instead of the app. Your app isn't affected." : "<b>Maintenance is off.</b> Family accounts can use the app."}</span>
        <button type="button" class="btn sm${M.on ? "" : " ghost bwarn"}" data-accmaint="${M.on ? "0" : "1"}"${ACC.busy ? " disabled" : ""}>${M.on ? "End maintenance" : "Start maintenance"}</button></div>
      ${M.on ? `<span class="foot">${M.msg ? `Their screen also says: "${esc(M.msg)}"` : "Optional: a note on their screen, like when you expect to be back."} <button type="button" class="link" data-accmaintmsg="1">${M.msg ? "Change note" : "Add a note"}</button></span>` : ""}</div>` : "";
  return `<section class="panel"><div class="ph"><h2>Accounts</h2><span class="sub">People you've added. They see Today, Members and their own Account only.</span></div>
    ${maintBox}
    ${holdBox}
    ${m ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}
    ${!list ? `<div class="empty">${ACC.err ? esc(errorText(ACC.err)) : "Loading…"}</div>` : list.length ? `<div class="accts">${list.map(card).join("")}</div>` : `<div class="empty">No accounts yet.</div>`}
  </section>
  <section class="panel"><div class="ph"><h2>Add someone</h2></div>
    <form id="accadd" class="keysform" autocomplete="off">
      <input id="accl" type="text" placeholder="Name, e.g. Mom" maxlength="40" required aria-label="Name">
      <input id="acce" type="email" placeholder="Their email" required aria-label="Email">
      <button type="submit" class="buybtn go"${ACC.busy ? " disabled" : ""}>Add and email an invite</button>
    </form>
    <p class="foot">They get an invite by email (from admin@capitolcapital.cc) that explains how to add the app to their home screen and set up a password (sign-in is password plus an emailed code, or Face ID once set up), then they connect their own real-money Alpaca account once in the app. From then on the tool buys the same picks for them, sized from their own balance, and sends alerts only to them. Don't charge for this: trading other people's money for a fee would need an adviser registration.</p>
  </section>`;
}
document.addEventListener("submit", ev => {
  if (ev.target.id !== "accadd" || isViewer()) return;
  ev.preventDefault();
  accPost({ action: "add", label: $("#accl").value, email: $("#acce").value });
});
document.addEventListener("click", ev => {
  if (isViewer()) return;
  const back = ev.target.closest("[data-accback]");
  if (back) { ACC.view = null; ACC.data = null; render(); window.scrollTo(0, 0); return; }
  const v = ev.target.closest("[data-accview]");
  if (v) { accOpen(v.dataset.accview); return; }
  const mb = ev.target.closest("[data-accmaint],[data-accmaintmsg]");
  if (mb && !ACC.busy) {
    const M = ACC.maint || { on: true };
    if (mb.dataset.accmaintmsg) { const t = prompt("A note on the maintenance screen (leave empty for none):", M.msg || ""); if (t == null) return; accPost({ action: "maint", on: true, msg: t }); return; }
    const on = mb.dataset.accmaint === "1";
    if (!confirm(on ? "Start maintenance? Every family account sees a maintenance screen instead of the app until you end it." : "End maintenance? Family accounts can use the app again right away, and each gets a notification that it's back.")) return;
    accPost({ action: "maint", on, msg: M.msg || "" }); return;
  }
  const hb = ev.target.closest("[data-acchold]");
  if (hb && !ACC.busy) {
    const on = hb.dataset.acchold === "1";
    if (!confirm(on ? "Put family trading on hold? Every family account becomes view-only: the tool stops buying and selling for them and their Buy and Sell buttons turn off."
      : "Start family trading? At the next update the tool buys the current picks for every connected family account that has signed the terms, then trades them like yours.")) return;
    accPost({ action: "hold", on }); return;
  }
  const tv = ev.target.closest("[data-accterms]");
  if (tv) {
    ACC.view = tv.dataset.accterms; ACC.data = null; render(); window.scrollTo(0, 0);
    api("/api/accounts/terms?id=" + encodeURIComponent(ACC.view)).then(r => { ACC.data = r.js; }).catch(e => { ACC.data = { error: e.message || "Couldn't load it." }; }).finally(render);
    return;
  }
  const b = ev.target.closest("[data-acc]");
  if (!b || ACC.busy) return;
  const act = b.dataset.acc, id = b.dataset.id, a = (ACC.list || []).find(x => x.id === id);
  if (!a) return;
  if (act === "remove") {
    const typed = prompt(`Remove ${a.label}? The tool stops trading their account (what they hold stays theirs in Alpaca). Type their email to confirm:`);
    if (typed == null) return;
    accPost({ action: "remove", id, confirm: typed.trim().toLowerCase() }); return;
  }
  const ask = { auto: b.dataset.on === "1" ? (ACC.hold ? `Start ${a.label} with everyone when family trading starts?` : `Turn automatic buying back on for ${a.label}? The tool starts buying new picks in their account again.`)
      : (ACC.hold ? `Keep ${a.label}'s automatic buying paused when family trading starts? You can turn it on any time.` : `Pause automatic buying for ${a.label}? The tool stops buying new picks in their account (it still sells on schedule).`),
    link: `Email ${a.label} (${a.email}) the invite again? It explains how to add the app and set up a password.`,
    signout: `Sign ${a.label} out on every device? They sign in again with their password and code (or Face ID).`,
    enable: `Turn ${a.label}'s account back on? They can then sign in again.` }[act];
  if (ask && !confirm(ask)) return;
  if (act === "reset-keys" && !confirm(`Remove ${a.label}'s saved Alpaca keys? The tool stops trading their account until they connect again. Nothing is sold.`)) return;
  if (act === "disable" && !confirm(`Turn off ${a.label}'s account? They're signed out and the tool stops trading it. Nothing is sold.`)) return;
  accPost({ action: act, id, on: b.dataset.on === "1" });
});


// ---------- Terms of Use: initial every section, then sign (owner, Nov 3 2026) ----------
// Shown on the Account tab before they can connect Alpaca (and again if the terms change), and under More → Terms of
// Use, where a signed copy is shown afterwards. The server checks everything again and keeps the exact text signed.
const TS = { doc: null, signed: null, err: null, loading: false, init: new Set(), name: "", busy: false, msg: null };
function termsLoad() {
  if (TS.loading || TS.doc) return;
  TS.loading = true;
  api("/api/me/terms").then(r => { TS.doc = r.js.terms; TS.signed = r.js.signed; }).catch(e => { TS.err = e; })
    .finally(() => { TS.loading = false; render(); });
}
const initialsOf = n => String(n || "").trim().split(/\s+/).filter(Boolean).map(w => w[0].toUpperCase()).join("").slice(0, 4);
function renderTermsSign(fromAccount) {
  termsLoad();
  if (!TS.doc) return `<div class="empty">${TS.err ? esc(errorText(TS.err)) : "Loading…"}</div>`;
  const T = TS.doc, ini = initialsOf(TS.name), all = T.sections.every((_, i) => TS.init.has(i + 1));
  const ready = all && /\S+\s+\S+/.test(TS.name.trim());
  const box = i => `<button type="button" class="tini${TS.init.has(i) ? " on" : ""}" data-tini="${i}" aria-label="Initial section ${i}" aria-pressed="${TS.init.has(i)}">${TS.init.has(i) ? esc(ini || "✓") : "Initial"}</button>`;
  return `${fromAccount ? `<p class="tlead">Before connecting your Alpaca account, please review and agree to the terms below.</p>` : ""}
  <article class="tdoc"><h1>${esc(T.title)}</h1><p class="tsub">Please review and agree to the terms below. Initial each section, then sign.</p>
    <p>${esc(T.intro)}</p>
    ${T.sections.map(([h, t], i) => `<div class="tcl"><div><b>${i + 1}. ${esc(h)}.</b> ${esc(t)}</div>${box(i + 1)}</div>`).join("")}
    <div class="tsig"><p>${esc(T.attest)}</p>
      <div class="tsg"><div><input id="tname" class="tline" type="text" autocomplete="name" maxlength="80" value="${esc(TS.name)}" placeholder="Full legal name" aria-label="Type your full legal name to sign"><div class="tcap">Signature: type your full legal name</div></div>
        <div><div class="tdate">${esc(new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }))}</div><div class="tcap">Date</div></div></div>
      <button type="button" class="buybtn go tgo" data-tsign="1"${ready && !TS.busy ? "" : " disabled"}>${TS.busy ? "Signing…" : "Agree and sign"}</button>
      ${!all ? `<p class="tcap" style="text-align:center">Initial all ${T.sections.length} sections to continue.</p>` : ""}
      ${TS.msg ? `<div class="tapmsg ${TS.msg.ok ? "pos" : "neg"}">${esc(TS.msg.text)}</div>` : ""}
      <p class="tcap" style="text-align:center">A signed copy is emailed to you and to the Operator, and kept in More → Terms of Use.</p></div>
  </article>`;
}
function termsSignedDoc(rec) {
  const T = rec.text, when = new Date(rec.at).toLocaleString("en-US", { timeZone: "America/New_York", dateStyle: "long", timeStyle: "short" }) + " ET";
  return `<article class="tdoc"><h1>${esc(T.title)}</h1><p class="tsub">Signed ${esc(when)}</p><p>${esc(T.intro)}</p>
    ${T.sections.map(([h, t], i) => `<div class="tcl"><div><b>${i + 1}. ${esc(h)}.</b> ${esc(t)}</div><span class="tini on">${esc(rec.initials)}</span></div>`).join("")}
    <div class="tsig"><p>${esc(T.attest)}</p>
      <div class="tsg"><div><div class="tline signed">${esc(rec.name)}</div><div class="tcap">Signature</div></div><div><div class="tdate">${esc(new Date(rec.at).toLocaleDateString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric" }))}</div><div class="tcap">Date</div></div></div></div></article>`;
}
function renderTermsPage() {
  termsLoad();
  if (!TS.doc) return `<div class="empty">${TS.err ? esc(errorText(TS.err)) : "Loading…"}</div>`;
  return TS.signed && TS.signed.v === TS.doc.v ? termsSignedDoc(TS.signed) : renderTermsSign(false);
}
document.addEventListener("input", ev => {
  if (ev.target.id !== "tname" || !isViewer()) return;
  TS.name = ev.target.value;
  // update the initials and the button in place (a full redraw would drop the cursor)
  const ini = initialsOf(TS.name);
  document.querySelectorAll(".tini.on[data-tini]").forEach(b => { b.textContent = ini || "✓"; });
  const go = document.querySelector("[data-tsign]"), all = TS.doc && TS.doc.sections.every((_, i) => TS.init.has(i + 1));
  if (go) go.disabled = !(all && /\S+\s+\S+/.test(TS.name.trim())) || TS.busy;
});
document.addEventListener("click", async ev => {
  if (!isViewer()) return;
  const b = ev.target.closest("[data-tini]");
  if (b) { const i = +b.dataset.tini; TS.init.has(i) ? TS.init.delete(i) : TS.init.add(i); const y = scrollY; render(); scrollTo(0, y); return; }
  if (ev.target.closest("[data-tsign]") && !TS.busy) {
    TS.busy = true; TS.msg = null; render();
    try {
      const { js } = await api("/api/me/terms", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ v: TS.doc.v, name: TS.name, initialed: [...TS.init] }) });
      TS.signed = js.signed; TS.msg = null;
      if (state.data) state.data.viewer = { ...(state.data.viewer || {}), terms: true };
      if (SESSION_ME) { SESSION_ME.terms = true; const w = TS.name.trim().split(/\s+/); SESSION_ME.initials = (w[0][0] + (w.length > 1 ? w[w.length - 1][0] : "")).toUpperCase(); }
      VA.keyMsg = { ok: true, text: js.message || "Signed." };
      if (state.tab !== "port") VA.termsDone = js.message;
    } catch (e) { TS.msg = { ok: false, text: e.message || "Couldn't sign. Try again." }; }
    TS.busy = false; render(); scrollTo(0, 0);
  }
});

// ---------- Privacy Policy ----------
function renderPrivacy() {
  const S = (h, body) => `<h3>${h}</h3>${body}`, ul = a => `<ul>${a.map(x => `<li>${x}</li>`).join("")}</ul>`;
  return `<article class="tdoc"><h1>Capitol Capital Privacy Policy</h1><p class="tsub">How your information is collected, stored and protected.</p>
  ${S("1. Information collected", ul([
    "Your name and email address, used to sign you in and send the notifications you choose.",
    "Your Alpaca API key, used only to place trades and read your account.",
    "Account information from Alpaca: account value, cash, positions, orders and trade history.",
    "Your signed Terms of Use, with the date, time, device and network address it was signed from.",
    "Bug reports and feature suggestions you send, with your device and browser type and your connection details (network address and approximate location) at the time.",
    "Optional: a profile picture, notification settings and account-balance alerts."]))}
  ${S("2. How it is stored", ul([
    "Your Alpaca key is encrypted with AES-256-GCM before it is saved. The encryption key is kept separately as a protected secret and is never stored with your data, and the key is never shown again after you enter it.",
    "Your information is kept in private cloud storage (Cloudflare R2) that is not publicly reachable. Every request is checked against your signed-in session.",
    "Your password is never stored, only a scrambled one-way fingerprint of it (salted PBKDF2-SHA256 with a secret key). Sign-in codes expire after 10 minutes, can be tried 5 times, and only a fingerprint of each is stored. Face ID or a passkey never leaves your device; the app keeps only its public key. Network addresses used to limit sign-in attempts are stored only in scrambled form.",
    "All connections to the app are encrypted with HTTPS."]))}
  ${S("3. Who can see it", ul([
    "You and the Operator. No other account holder can see your information.",
    "Your information is never sold, rented or used for advertising.",
    "The Service's activity logs never include your name, email address, balances or positions."]))}
  ${S("4. Service providers", `<p>Alpaca (your brokerage), Cloudflare (hosting and storage), Resend (email delivery), and Apple or Google (phone notifications). Each receives only the information it needs to provide its service.</p>`)}
  ${S("5. Your choices", `<p>You can change your notifications or remove your picture at any time in the app. Revoking your key in Alpaca immediately ends all access to your account. To close your account and have all of your stored information deleted, email the Operator at <a href="mailto:admin@capitolcapital.cc">admin@capitolcapital.cc</a>.</p>`)}
  </article>`;
}

// ---------- Pause trading (bottom of their Account page) and the banner on every page ----------
function pausePanel(V) {
  const on = !!V.paused;
  return `<section class="panel" data-hl="vpause" id="vpause"><div class="ph"><h2>Pause trading</h2></div>
    <label class="vswitch"><input type="checkbox" data-vpause="1"${on ? " checked" : ""}${VA.pauseBusy ? " disabled" : ""}> <span>${on ? "Trading is paused" : "Pause all trading in my account"}</span></label>
    <p class="foot" style="margin-top:6px">While paused, the tool doesn't buy or sell anything in your account, no matter what, and anything it had waiting to fill is cancelled. Everything you hold stays as it is until you turn this off or sell it yourself. Sell still works; Buy more is off.</p>
    ${VA.pauseMsg ? `<div class="tapmsg ${VA.pauseMsg.ok ? "pos" : "neg"}">${esc(VA.pauseMsg.text)}</div>` : ""}</section>`;
}
function pausedBanner() {
  const V = (state.data && state.data.viewer) || {};
  if (isViewer() && V.hold) return `<div class="pausebar"><b>View only for now.</b> Trading for your account hasn't started yet, so the tool isn't buying or selling and the Buy and Sell buttons are off. <b>Estimated beta launch: November 27, 2026</b> (the date may change). You'll get a heads-up when it starts.</div>`;
  if (!isViewer() || !V.paused) return "";
  return `<div class="pausebar"><b>Trading paused.</b> The tool isn't buying or selling in your account. <button type="button" class="link" data-tab-go="port">Resume on your Account page</button></div>`;
}
document.addEventListener("change", async ev => {
  if (!ev.target.matches || !ev.target.matches("[data-vpause]") || !isViewer() || VA.pauseBusy) return;
  const on = ev.target.checked;
  if (on && !confirm("Pause all trading? The tool won't buy or sell anything in your account until you turn this off. Anything it has waiting to fill is cancelled.")) { ev.target.checked = false; return; }
  VA.pauseBusy = true; VA.pauseMsg = null; render();
  try {
    const { js } = await api("/api/me/pause", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paused: on }) });
    if (state.data) state.data.viewer = { ...(state.data.viewer || {}), paused: js.paused };
    VA.pauseMsg = { ok: true, text: js.message };
  } catch (e) { VA.pauseMsg = { ok: false, text: e.message || "Couldn't change it. Try again." }; }
  VA.pauseBusy = false; render();
});
document.addEventListener("click", ev => {
  const g = ev.target.closest && ev.target.closest("[data-tab-go]");
  if (!g || !isViewer()) return;
  state.tab = g.dataset.tabGo; location.hash = state.tab; render();
  // after the page has drawn (the hash change redraws it once more): straight to the Pause switch
  setTimeout(() => { const el = document.querySelector("[data-vpause]"); if (el) el.scrollIntoView({ block: "center" }); else scrollTo(0, document.body.scrollHeight); }, 120);
});


// ---------- Pick days on their Account page: like the owner's, without the S&P ----------
const VDAYS = { sel: null };
function vDaysSection(d) {
  const hist = (d.close_history || []).filter(x => x.date).slice().reverse();
  if (!hist.length) return "";
  if (!VDAYS.sel || !VDAYS.sel.some(x => hist.find(h => h.date === x))) VDAYS.sel = [hist[0].date];
  const short = x => new Date(x + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const ords = (d.portfolio?.orders || []);
  const card = date => {
    const x = hist.find(h => h.date === date); if (!x) return "";
    const dayOrds = ords.filter(o => o.at && fmtETDate(o.at) === date && o.filled_px && !/^(SGOV|BIL|SHV)$/.test(o.t));
    return `<div class="dayc"><h3>${esc(fmtDate(x.date))}</h3>
      <div class="r"><span>Account</span><span>${money(x.equity)}</span></div>
      <div class="r"><span>That day</span><span class="${cls(x.day_pl)}">${x.day_pl == null ? "–" : (x.day_pl > 0 ? "+" : "") + money(x.day_pl)}</span></div>
      <div class="r"><span>Since start</span><span class="${cls(x.since)}">${x.since == null ? "–" : pct(x.since, 2)}</span></div>
      ${dayOrds.length ? `<div class="ords">${dayOrds.map(o => `<div>${esc(o.side === "buy" ? "Bought" : "Sold")} ${tkBtn(o.t)} ${esc(o.qty ?? "")} at ${money(+o.filled_px, 2)}</div>`).join("")}</div>` : ""}
    </div>`;
  };
  return `<section class="panel"><div class="ph"><h2>Pick days</h2><span class="sub">Tap one or more dates to compare them side by side. Deposits and withdrawals aren't counted as gains.</span></div>
  <div class="daypick">${hist.map(h => `<button type="button" data-vday="${esc(h.date)}" aria-pressed="${VDAYS.sel.includes(h.date)}">${esc(short(h.date))}</button>`).join("")}</div>
  <div class="days">${hist.filter(h => VDAYS.sel.includes(h.date)).reverse().map(h => card(h.date)).join("")}</div></section>`;
}
document.addEventListener("click", ev => {
  const b = ev.target.closest && ev.target.closest("[data-vday]"); if (!b) return;
  const k = b.dataset.vday, i = VDAYS.sel.indexOf(k);
  if (i >= 0) { if (VDAYS.sel.length > 1) VDAYS.sel.splice(i, 1); } else VDAYS.sel.push(k);
  const y = window.scrollY; render(); window.scrollTo(0, y);
});


// ---------- spare cash: parked in SGOV, or kept as cash (their choice; nothing preselected) ----------
const PARK_TXT = {
  sgov: ["Park spare cash in SGOV",
    "Your available balance is kept in SGOV, a fund of short-term U.S. Treasury bills, until it's needed for buying. It earns about what short-term Treasury bills pay (roughly 3 to 4% a year lately) and its price barely moves. The tool sells some whenever a new pick needs the money, so those trades show in your orders and on your tax forms. Interest from Treasury bills isn't taxed by states."],
  cash: ["Keep spare cash as cash",
    "Your available balance stays as cash in your Alpaca account until it's needed for buying, with no extra trades. It earns nothing unless you turn on Alpaca's High-Yield Cash in your Alpaca dashboard, which pays interest on uninvested cash (variable, about 3.5% a year lately) and keeps it available to use at any time."] };
function parkPanel(d, ro) {
  const V = d.viewer || {}, cur = V.connected ? (V.park || "sgov") : VA.park || null;
  const opt = k => `<button type="button" class="parkopt${cur === k ? " on" : ""}" data-vpark="${k}" aria-pressed="${cur === k}"${ro ? " disabled" : ""}>
      <span class="pr" aria-hidden="true"></span><span><b>${PARK_TXT[k][0]}</b><span class="foot">${PARK_TXT[k][1]}</span></span></button>`;
  return `<section class="panel"><div class="ph"><h2>Spare cash</h2>${V.connected ? "" : `<span class="sub">Choose one to continue</span>`}</div>
    <div class="parkopts">${opt("sgov")}${opt("cash")}</div>
    ${V.connected && !ro ? `<p class="foot">Switching to cash sells the SGOV the tool holds for you${V.paused ? " once you resume trading" : ""}; switching to SGOV parks spare cash at the next update.</p>` : ""}
    ${VA.parkMsg ? `<div class="tapmsg ${VA.parkMsg.ok ? "pos" : "neg"}">${esc(VA.parkMsg.text)}</div>` : ""}</section>`;
}
document.addEventListener("click", async ev => {
  const b = ev.target.closest && ev.target.closest("[data-vpark]");
  if (!b || !isViewer() || VA.parkBusy) return;
  const park = b.dataset.vpark, d = state.data || {};
  if (!(d.viewer && d.viewer.connected)) { VA.park = park; VA.keyMsg = null; const y = scrollY, k = $("#vk")?.value, s2 = $("#vs")?.value; render(); if ($("#vk")) $("#vk").value = k || ""; if ($("#vs")) $("#vs").value = s2 || ""; scrollTo(0, y); return; }
  if ((d.viewer.park || "sgov") === park) return;
  if (!confirm(park === "cash" ? "Keep spare cash as cash? The SGOV the tool holds for you is sold and stays as cash." : "Park spare cash in SGOV? The tool buys SGOV with your spare cash at the next update.")) return;
  VA.parkBusy = true; VA.parkMsg = null;
  try {
    const { js } = await api("/api/me/park", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ park }) });
    d.viewer.park = js.park; VA.parkMsg = { ok: true, text: js.message };
  } catch (e) { VA.parkMsg = { ok: false, text: e.message || "Couldn't change it. Try again." }; }
  VA.parkBusy = false; render();
});


// ---------- their profile picture at the top, next to the bell (owner, Nov 4 2026): opens Settings ----------
function vMeButton() {
  const b = document.getElementById("mebtn");
  if (!b || !isViewer()) return;
  const has = SESSION_ME && SESSION_ME.photo, src = has ? myPhoto() : "";
  b.setAttribute("aria-label", "Your settings"); b.title = "Your settings";
  const ini = (SESSION_ME && SESSION_ME.initials) || "";
  b.innerHTML = has ? `<img src="${esc(src)}" alt="">`
    : ini ? `<span class="meini">${esc(ini)}</span>`
    : `<svg viewBox="0 0 24 24" width="100%" height="100%" aria-hidden="true"><circle cx="12" cy="9" r="4" fill="var(--muted)"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7" fill="var(--muted)"/></svg>`;
  b.setAttribute("aria-pressed", String(state.tab === "vset"));
}
document.getElementById("mebtn")?.addEventListener("click", ev => {
  if (!isViewer()) return;
  ev.stopImmediatePropagation();
  state.tab = state.tab === "vset" ? "today" : "vset"; try { location.hash = state.tab; } catch (e) {} render(); scrollTo(0, 0);
}, true);


// ---------- their Taxes page: like the owner's; until there's a trade it just says so ----------
function renderVTax() {
  const html = renderTax();
  const J = TAX.js;
  if ((TAX.err && TAX.err.code === "no_file") || (J && !J.fills))
    return `<section class="panel"><div class="ph"><h2>Taxes</h2></div><div class="empty">No trades yet. This fills in after the market closes on a day the tool buys or sells for you: every sale's gain or loss, your Form 8949 statement and what to file.</div></section>`;
  return html;
}


// ---------- version: shown at the bottom of More; an app showing an older page offers to update ----------
let SERVER_BUILD = null;
function versionLine() {
  const when = (() => { try { return new Date(APP_BUILT).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }); } catch (e) { return ""; } })();
  return `<p class="verline">Alpha · version ${esc(APP_BUILD)}${when ? ` · ${esc(when)} ET` : ""}${SERVER_BUILD && SERVER_BUILD !== APP_BUILD ? ` · <b>newer: ${esc(SERVER_BUILD)}</b>` : ""}</p>`;
}
function checkBuild(js) { if (js && js.build) SERVER_BUILD = js.build; }
function updateBanner() {
  if (!SERVER_BUILD || SERVER_BUILD === APP_BUILD || /dev/.test(APP_BUILD)) return "";
  return `<div class="updbar"><span><b>A newer version is ready</b> (${esc(SERVER_BUILD)}; you have ${esc(APP_BUILD)}).</span><button type="button" class="btn sm" data-appupdate="1">Update</button></div>`;
}
document.addEventListener("click", ev => {
  if (!(ev.target.closest && ev.target.closest("[data-appupdate]"))) return;
  try { location.reload(); } catch (e) {}
});
// family accounts: a light check-in every 2 minutes while the app is on screen, so the owner's Accounts page can
// show "Online now" (and a newer version is noticed while it's open)
setInterval(() => {
  if (!isViewer() || document.visibilityState !== "visible") return;
  api("/api/session?b=" + encodeURIComponent(APP_BUILD)).then(r => { const had = SERVER_BUILD; checkBuild(r.js); if (SERVER_BUILD !== had) render(); }, () => {});
}, 120000);
// when the app comes back to the screen, check for a newer version (a home-screen app can stay open for days)
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  api("/api/session?b=" + encodeURIComponent(APP_BUILD)).then(r => { const had = SERVER_BUILD; checkBuild(r.js); if (SERVER_BUILD !== had) render(); }, () => {});
});


// ---------- Face ID / passkey sign-in: set up or remove (their Settings; the owner's More page) ----------
const PK = { items: null, loading: false, msg: null, busy: false, can: null };
const PK_NAME = /iPhone|iPad/.test(navigator.userAgent) ? "Face ID" : /Macintosh/.test(navigator.userAgent) ? "Touch ID" : "a passkey";
function pkLoad() {
  if (PK.loading || PK.items) return;
  PK.loading = true;
  (async () => { try { PK.can = !!(window.PublicKeyCredential && await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable()); } catch (e) { PK.can = false; } })();
  api("/api/me/passkeys").then(r => { PK.items = r.js.items || []; }).catch(() => { PK.items = []; }).finally(() => { PK.loading = false; render(); });
}
function signInPanel() {
  pkLoad();
  const L = PK.items || [];
  return `<section class="panel"><div class="ph"><h2>Sign-in</h2><span class="sub">${esc(PK_NAME === "a passkey" ? "Passkeys" : PK_NAME)}</span></div>
    <p class="foot" style="margin:0 0 8px">You sign in with your email and password plus a code we email you${isViewer() ? "" : " (or your passcode)"}, and stay signed in for a month. On a device with ${esc(PK_NAME)}, you can use it instead. To change your password, sign out and tap Forgot password? on the sign-in screen.</p>
    ${L.length ? `<ul class="vrules">${L.map(k => `<li><span>${esc(k.device || "Device")} · set up ${esc(fmtDate(String(k.at).slice(0, 10)))}</span><button type="button" class="link" data-pkdel="${esc(k.id)}">Remove</button></li>`).join("")}</ul>` : ""}
    ${PK.can === false ? `<p class="foot">This device doesn't have ${esc(PK_NAME)} or a passkey; keep using your password and code here.</p>`
      : `<button type="button" class="btn sm" data-pkadd="1"${PK.busy ? " disabled" : ""}>Set up ${esc(PK_NAME)} on this device</button>`}
    ${PK.msg ? `<div class="tapmsg ${PK.msg.ok ? "pos" : "neg"}">${esc(PK.msg.text)}</div>` : ""}</section>`;
}
const pkB64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const pkUnb64 = s => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), c => c.charCodeAt(0));
document.addEventListener("click", async ev => {
  const add = ev.target.closest && ev.target.closest("[data-pkadd]"), del = ev.target.closest && ev.target.closest("[data-pkdel]");
  if (!add && !del) return;
  if (del) {
    if (!confirm("Remove " + PK_NAME + " sign-in from that device? It will sign in with password and code instead.")) return;
    try { const { js } = await api("/api/me/passkey/remove", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: del.dataset.pkdel }) }); PK.msg = { ok: true, text: js.message }; }
    catch (e) { PK.msg = { ok: false, text: e.message || "Couldn't remove it." }; }
    PK.items = null; render(); return;
  }
  PK.busy = true; PK.msg = null; render();
  try {
    const o = (await api("/api/me/passkey/options")).js;
    const c = await navigator.credentials.create({ publicKey: { ...o, challenge: pkUnb64(o.challenge), user: { ...o.user, id: pkUnb64(o.user.id) },
      excludeCredentials: (o.excludeCredentials || []).map(x => ({ ...x, id: pkUnb64(x.id) })) } });
    const R = c.response, ua = navigator.userAgent;
    const { js } = await api("/api/me/passkey", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: c.id,
      clientDataJSON: pkB64(R.clientDataJSON), authenticatorData: pkB64(R.getAuthenticatorData()), publicKey: pkB64(R.getPublicKey()), alg: R.getPublicKeyAlgorithm(),
      device: /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Macintosh/.test(ua) ? "Mac" : /Android/.test(ua) ? "Android" : /Windows/.test(ua) ? "Windows" : "This device" }) });
    PK.msg = { ok: true, text: js.message };
  } catch (e) { PK.msg = { ok: false, text: e && e.name === "InvalidStateError" ? PK_NAME + " is already set up on this device." : e && e.name === "NotAllowedError" ? "Cancelled." : (e && e.message) || "Couldn't set it up." }; }
  PK.busy = false; PK.items = null; render();
});


// ---------- leak deterrents for family accounts (owner, Nov 4 2026) ----------
// A web app can't detect screenshots, so: (1) a faint watermark with their name and email over every screen, so a
// shared screenshot shows whose it was; (2) the screen goes blank when the app leaves the screen, so the app
// switcher's preview shows nothing; (3) text on the page can't be selected or copied.
function applyLeakGuards() {
  if (!isViewer()) return;
  const who = (SESSION_ME && SESSION_ME.mark) || "";
  let wm = document.getElementById("wmark");
  if (!wm) { wm = document.createElement("div"); wm.id = "wmark"; wm.setAttribute("aria-hidden", "true"); document.body.appendChild(wm); }
  const txt = (who + " · " + new Date().toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })).replace(/[<&>"]/g, "");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="460" height="190"><text x="20" y="105" transform="rotate(-20 230 95)" font-family="system-ui,sans-serif" font-size="13" fill="#808080">${txt}</text></svg>`;
  wm.style.backgroundImage = `url("data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}")`;
  if (!document.getElementById("vcover")) { const c = document.createElement("div"); c.id = "vcover"; c.innerHTML = `<img src="/icon-180.png" alt="">`; document.body.appendChild(c); }
}
const coverOn = () => { if (isViewer()) document.documentElement.classList.add("covered"); };
const coverOff = () => document.documentElement.classList.remove("covered");
addEventListener("pagehide", coverOn);
addEventListener("blur", coverOn);
addEventListener("focus", coverOff);
addEventListener("pageshow", coverOff);
document.addEventListener("visibilitychange", () => document.visibilityState === "hidden" ? coverOn() : coverOff());
// never left stuck: any touch or click takes the cover away
document.addEventListener("pointerdown", coverOff, true);
document.addEventListener("keydown", coverOff, true);
document.addEventListener("copy", ev => { if (isViewer() && !(ev.target.closest && ev.target.closest("input,textarea"))) ev.preventDefault(); });

// ---------- Blog (owner, Nov 11 2026): short how-it-works posts, in More for everyone; the info buttons next to
// greyed-out Buy/Sell open "How the Buy and Sell buttons work". Back returns to where the info button was tapped ----------
const BLOG = { k: null, from: null, fromPost: false };
function infoBtn() {
  return `<button type="button" class="infobtn" data-blog="buttons" aria-label="How the Buy and Sell buttons work" title="How the Buy and Sell buttons work"><svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8.25" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="10" cy="6.2" r="1.15" fill="currentColor"/><path d="M10 9v5.6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>`;
}
const BLOG_POSTS = [
  { k: "how", t: "How the tool works", d: "Picks, how long it holds them, when it sells, spare cash, the weekly check", body: blogHow },
  { k: "buttons", t: "How the Buy and Sell buttons work", d: "What they do, the disclaimers, and why they're sometimes greyed out", body: blogButtons },
  { k: "sizing", t: "Suggested sizing", d: "How much each pick gets, and the test behind it", body: blogSizing },
];
// a link to a post or to one section of it ("how/sells"): opens the post scrolled to that section
const bl = (to, label) => `<button type="button" class="link" data-blog="${to}">${label}</button>`;
const bsec = (id, title, html) => `<div class="bsec" data-hl="blog-${id}" id="blog-${id}"><h3 class="subh">${title}</h3>${html}</div>`;
function blogHow() {
  const P = (state.data?.adaptive?.policy) || {}, hold = P.hold_other ?? 60, small = P.hold_small;
  return `<nav class="bjump">${[["picks", "Picks"], ["size", "Size"], ["sells", "Selling"], ["small", "Small companies"], ["cash", "Spare cash"], ["weekly", "Weekly check"], ["pause", "Stopping it"], ["taxes", "Taxes"], ["risk", "Risk"]]
      .map(([id, l]) => bl("how/" + id, l)).join("")}</nav>
    ${bsec("picks", "Picks: the Buy list", `<ul class="blist">
      <li>Members of Congress must report their stock trades. The tool reads every report.</li>
      <li>Each purchase is scored on dozens of signals: the member's track record, their committees, the trade's size and timing, and more.</li>
      <li>Purchases that score near the top of all past ones go on the Buy list. The tool buys those for you.</li>
      <li>A stock you like isn't on it? A member hasn't bought it recently with a top score. Company search still shows any stock.</li></ul>`)}
    ${bsec("size", "Size", `<ul class="blist"><li>Every pick gets the same share of your account. ${bl("sizing", "Suggested sizing")}</li></ul>`)}
    ${bsec("sells", "When it sells", `<ul class="blist">
      <li>Most picks are held about <b>${hold} trading days</b> (roughly ${Math.round(hold / 21)} months).</li>
      <li>At the end, a pick that's ahead of the S&amp;P 500 is kept and checked every day, then sold once it falls behind.</li>
      <li>The rules change only when the ${bl("how/weekly", "weekly check")} finds something clearly better.</li></ul>`)}
    ${bsec("small", "Small companies", `<ul class="blist"><li>About 30% of the money goes to a separate group of small-company picks${small ? `, held about ${small} trading days` : " held longer"}.</li></ul>`)}
    ${bsec("cash", "Spare cash", `<ul class="blist">
      <li>Money not in a pick waits in <b>SGOV</b>, a fund of short-term U.S. Treasury bills, or as cash. You choose under Spare cash on Account.</li>
      <li>SGOV earns about what Treasury bills pay and barely moves in price. The tool sells some when a new pick needs the money.</li></ul>`)}
    ${bsec("weekly", "The weekly check", `<ul class="blist">
      <li>Every week the tool re-tests its settings (hold time, sizing and more) on the full trade history.</li>
      <li>It changes a setting only if another one wins on the earlier years and the last 3 years too.</li>
      <li>Example: ${bl("sizing/test", "the sizing test")}.</li></ul>`)}
    ${bsec("pause", "Stopping it", `<ul class="blist">
      <li>${isViewer() ? `<button type="button" class="link" data-vgopause="1">Pause trading</button>` : "Pause trading"} at the bottom of Account: no buying or selling until you resume. What you hold stays put.</li>
      <li>Deleting the key in your Alpaca account stops it for good. Everything stays yours either way.</li></ul>`)}
    ${bsec("taxes", "Taxes", `<ul class="blist">
      <li>Alpaca sends a 1099 each year listing every sale.</li>
      <li>A stock held under a year = short-term gain or loss, taxed like regular income.</li>${isViewer() ? `<li>Every sale and your Form 8949 statement are under More → Taxes.</li>` : ""}</ul>`)}
    ${bsec("risk", "Risk", `<ul class="blist"><li>Not guaranteed. It's tested on past years, and past results don't promise future ones.</li>
      <li>It can lose money, sometimes for a long stretch. Only put in money you're comfortable leaving for a while.</li></ul>`)}`;
}
function blogButtons() {
  const V = (isViewer() && state.data && state.data.viewer) || {};
  // the status line follows the account: trading not started (the family hold), their own pause, or normal
  const status = V.hold ? `Trading hasn't started yet, so Buy and Sell are off. They turn on when it starts.`
    : V.paused ? `You paused trading, so Buy is off. Sell still works. <button type="button" class="link" data-vgopause="1">Resume in Account</button>`
    : `Buy and Sell grey out while trading is paused.`;
  return `<nav class="bjump">${[["greyed", "Greyed out"], ["buy", "Buy"], ["buy-yourself", "Buying yourself"], ["sell", "Sell"], ["sell-yourself", "Selling yourself"], ["stop", "Stopping it"]]
      .map(([id, l]) => bl("buttons/" + id, l)).join("")}</nav>
    ${bsec("greyed", "Greyed out?", `<p>${status}</p>`)}
    <p><b>Automatic:</b> ${bl("how/picks", "picks")} are bought and ${bl("how/sells", "sold")} for you automatically, at a ${bl("sizing", "pre-set size")}.</p>
    ${bsec("buy", "Buy", `<ul class="blist">
      <li>Only for stocks on today's ${bl("how/picks", "Buy list")}.</li>
      <li>Enter a dollar amount. Fills now, or at the next open if the market's closed.</li>
      <li>Cash short? Some of your ${bl("how/cash", "spare cash fund")} is sold first.</li></ul>`)}
    ${bsec("buy-yourself", "Disclaimer: buying yourself", `<ul class="blist">
      <li>The tool already buys every pick, each with the ${bl("sizing", "same amount")}, on purpose.</li>
      <li>The ${bl("sizing/test", "weekly sizing test")} keeps finding same-size beats putting more into stronger picks. Extra buys by hand were never part of that test.</li>
      <li>Putting more into one stock makes your account ride more on it.</li>
      <li>What you buy is treated like the tool's own buy: same ${bl("how/sells", "hold time and sell rules")}, sold with the rest.</li></ul>`)}
    ${bsec("sell", "Sell", `<ul class="blist">
      <li>Any stock you hold, any time.</li>
      <li><b>All</b>, a number of <b>Shares</b>, a <b>$ amount</b>, or <b>Initial investment</b>: sells what you paid and keeps the gain invested. It only works when the stock is worth more than you paid; otherwise it would sell everything, so use All.</li>
      <li>Fills now, or at the next open if the market's closed.</li></ul>`)}
    ${bsec("sell-yourself", "Disclaimer: selling yourself", `<ul class="blist">
      <li>The tool holds each pick for a set time because that tested best, and sells it itself when the time comes. It keeps a winner longer while it's ahead of the S&amp;P 500. ${bl("how/sells", "When it sells")}</li>
      <li>Selling early means that pick won't get the result the strategy measured.</li>
      <li>The tool won't buy that stock back for that pick for 45 days.</li>
      <li>Selling part (including Initial investment) leaves the rest under the tool's rules.</li>
      <li>A stock you bought yourself outside the Buy list isn't managed by the tool.</li>
      <li>Held under a year = short-term gain or loss. ${bl("how/taxes", "Taxes")}</li></ul>`)}
    ${bsec("stop", "Want it to stop?", `<p>${isViewer() ? `<button type="button" class="link" data-vgopause="1">Pause trading</button>` : "Pause trading"} at the bottom of Account. ${bl("how/pause", "More on stopping")}</p>`)}`;
}
function blogSizing() {
  const d = state.data || {}, L = (d.portfolio || {}).limits || {}, a = d.adaptive || {};
  const rows = (a.evaluation || []).filter(r => r.Setting === "Position size by confidence");
  const cur = (a.policy || {}).sizing || "equal";
  const name = { equal: "the same amount for every pick", moderate: "0.75×–1.25× by score", strong: "0.5×–1.5× by score" }[cur] || cur;
  const per = L.per_pick != null ? ` (about ${money(L.per_pick)} a pick right now)` : "";
  return `<ul class="blist">
      <li><b>Now:</b> ${esc(name)}${per}.</li>
      <li><b>Amount:</b> your account split evenly across the picks one ${bl("how/sells", "holding period")} needs.</li>
      <li><b>Buy size</b> on Account scales it: Low ½×, Standard 1×, High 1.5×.</li>
      <li><b>Cap:</b> never more than ${Math.round(L.max_invested_pct || 90)}% invested. The rest is ${bl("how/cash", "spare cash")}.</li>
      <li><b>Buying extra</b> by hand puts more on one stock. That wasn't part of the test below. ${bl("buttons/buy-yourself", "Disclaimer")}</li></ul>
    ${bsec("test", "The test behind it", `<p class="foot">Every week the tool re-runs the full trade history with each way of sizing. It switches only if another way wins on the earlier years and the last 3 years too. ${bl("how/weekly", "The weekly check")}${a.evaluated_at ? ` · last check ${esc(fmtET(a.evaluated_at))}` : ""}</p>
    ${rows.length ? adjTable(rows) : `<div class="empty">This week's results aren't in yet.</div>`}`)}`;
}
function renderBlog() {
  const p = BLOG_POSTS.find(x => x.k === BLOG.k);
  const back = BLOG.from && (BLOG.from.tab !== "blog" || BLOG.fromPost) ? "‹ Back" : p ? "‹ Blog" : "‹ More";
  const head = `<button type="button" class="moreback" data-blogback="1">${back}</button>`;
  if (p) return `${head}<section class="panel blogpost"><div class="ph"><h2>${esc(p.t)}</h2></div>${p.body()}</section>`;
  return `${head}<section class="panel"><div class="ph"><h2>Blog</h2></div><div class="morelist">${BLOG_POSTS.map(x =>
    `<button type="button" class="moreitem" data-blog="${x.k}"><span class="ml">${esc(x.t)}</span><span class="md">${esc(x.d)}</span><span class="mc" aria-hidden="true">›</span></button>`).join("")}</div></section>`;
}
document.addEventListener("click", ev => {
  const b = ev.target.closest("[data-blog]");
  if (b) {
    ev.preventDefault(); ev.stopPropagation();
    BLOG.fromPost = state.tab === "blog" && !!BLOG.k;      // a link inside a post: Back returns to that post
    go("#blog/" + b.dataset.blog); scrollTo(0, 0); return;
  }
  const bk = ev.target.closest("[data-blogback]");
  if (bk) {
    ev.preventDefault();
    const f = BLOG.from;
    if (f && f.pushed && history.length > 1) { history.back(); return; }   // back to where it was opened from, same scroll
    if (BLOG.k) { BLOG.k = null; BLOG.from = null; try { history.replaceState({}, "", "#blog"); } catch (e) {} render(); scrollTo(0, 0); return; }
    goTab("more"); return;
  }
  const gp = ev.target.closest("[data-vgopause]");
  if (gp && isViewer()) {
    ev.preventDefault();
    go("#port"); TARGET = { sel: '[data-hl="vpause"]', until: Date.now() + 20000 };
  }
});
