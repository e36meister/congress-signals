// ---------- family accounts (owner, Nov 2 2026) ----------
// A family account sees Today (Buy candidates only), Members, Alerts, its own Account and a short More menu (company
// search, notifications, settings, questions, bug report). The server only answers those (cloud/accounts.js); this
// hides every button and link that would point anywhere else, and render() sends any other page back to Today.
// The owner gets the Accounts page (under More) to add people and see each portfolio.
let ROLE = "owner", SESSION_ME = null;
const isViewer = () => ROLE === "viewer";
const VIEWER_TABS = new Set(["today", "members", "port", "alerts", "more", "search", "vnotif", "vset", "faq", "bug"]);
const VIEWER_MORE = [
  { k: "search", l: "Company search", d: "Any ticker or company: its chart, what's going on, and every member's trades" },
  { k: "vnotif", l: "Notifications", d: "Push and email: trades, the daily close, big moves, your own account alerts" },
  { k: "vset", l: "Settings", d: "Profile picture, extra explanations, home-screen widget, sign out" },
  { k: "faq", l: "Questions and answers", d: "How the tool picks, buys, sells and sizes, and what you can do" },
  { k: "bug", l: "Report a bug", d: "Something broken or confusing? It goes straight to the owner" },
];
const VIEWER_SUB = new Set(["search", "vnotif", "vset", "faq", "bug"]);
function applyRole(s) {
  ROLE = s && s.role === "viewer" ? "viewer" : "owner";
  SESSION_ME = s || null;
  document.documentElement.classList.toggle("viewer", isViewer());
  let tips = false;
  try { tips = localStorage.getItem("cc-tips") === "1"; } catch (e) {}
  document.documentElement.classList.toggle("tips", isViewer() && tips);
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

const VA = { open: null, sellOpen: null, sellMode: {}, amt: {}, msg: {}, busy: false, keyMsg: null, keyBusy: false,
  prefs: null, prefsErr: null, prefsMsg: null, photoMsg: null, photoBusy: false, bugMsg: null, bugBusy: false, bugFrom: null };

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

// ---------- Today: a Buy button on each Buy candidate ----------
function vBuyArea(r) {
  if (!state.data?.viewer?.connected) return "";
  const held = (state.data?.portfolio?.positions || []).some(x => x.t === r.t);
  return `<button type="button" class="buybtn vb" data-vbuy="${esc(r.t)}">${VA.open === r.t ? "Close" : held ? "Buy more" : "Buy"}</button>`;
}
function vBuyBox(t, inTable) {
  const m = VA.msg[t];
  if (VA.open !== t) return m && !inTable ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : "";
  const L = state.data?.portfolio?.limits || {}, v = VA.amt[t] ?? (L.per_pick ? Math.round(L.per_pick * 0.25) : "");
  const held = (state.data?.portfolio?.positions || []).some(x => x.t === t);
  const box = `<div class="sellbox">
    <div class="note warn" style="margin:0 0 10px"><b>Before you ${held ? "add more" : "buy"}:</b> the tool already buys every pick for you, each with the same amount, on purpose. Its weekly check keeps testing same-size picks against putting more into the stronger ones, and same-size has stayed ahead. Extra buys by hand were never part of that test, and putting more into one stock makes your account ride more on it. What you buy here is handled like the tool's own buy of ${esc(t)}: the same hold and sell rules, sold with the rest.</div>
    <label class="foot" for="vbuy-${esc(t)}">Amount in dollars</label>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:4px">
      <input id="vbuy-${esc(t)}" class="amt" type="number" inputmode="decimal" min="1" step="1" value="${esc(v)}" style="max-width:140px">
      <button type="button" class="buybtn go" data-vbuysend="${esc(t)}"${VA.busy ? " disabled" : ""}>${VA.busy ? "Sending…" : "Buy " + esc(t)}</button>
      <button type="button" class="link" data-vbuy="${esc(t)}">Cancel</button></div>
    <div class="foot">Real money. Fills now if the market is open, otherwise at the next open. If cash is short, some of the Treasury-bill fund is sold first.</div>
    ${m ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}</div>`;
  return inTable ? `<tr class="sellrow"><td colspan="6">${box}</td></tr>` : box;
}

// ---------- the Account tab for a family account (also the owner's read-only view of it) ----------
// opts.readOnly: the owner looking at someone's account (no buttons, no setup form)
function renderViewerAccount(d, opts = {}) {
  const V = d.viewer || {}, p = d.portfolio, ro = !!opts.readOnly;
  if (!V.connected && !ro) return renderConnect();
  if (!V.connected && ro) return `<div class="empty">They haven't connected their Alpaca account yet.</div>`;
  if (!p) return `<section class="panel"><div class="ph"><h2>Connected</h2></div><p>Your Alpaca account is connected. It shows up here after the next update (a few times a day). The tool buys new picks as they come in and parks spare cash in a Treasury-bill fund until picks need it.</p></section>`;
  const pos = p.positions || [], ords = p.orders || [], L = p.limits || {};
  const picks = pos.filter(x => x.bot && x.sleeve !== "park"), park = pos.filter(x => x.sleeve === "park"), own = pos.filter(x => !x.bot && x.sleeve !== "park");
  const invested = picks.reduce((a, x) => a + (x.mv || 0), 0), gain = picks.reduce((a, x) => a + (x.pl || 0), 0), parked = park.reduce((a, x) => a + (x.mv || 0), 0);
  const C = d.close_report;
  const auto = V.auto !== false ? `<span class="chip BUY">Auto-buying on</span>` : `<span class="chip avoid">Auto-buying paused</span>`;
  const row = x => {
    const held = x.sleeve === "park" ? `Cash waiting for picks<div class="foot">In a Treasury-bill fund until the tool needs it</div>`
      : x.bot ? `${x.sleeve === "small" ? "Small-company pick · " : ""}${x.held ?? 0} of ${x.hold ?? L.hold_days ?? 60} days${x.bought ? `<div class="foot">Tool sells ~${esc(fmtDate(addTradingDays(x.bought, x.hold ?? L.hold_days ?? 60)))}; a winner may be kept longer</div>` : ""}`
      : `Yours<div class="foot">You bought this yourself; the tool leaves it alone</div>`;
    const btns = ro || x.sleeve === "park" ? "" : `<div class="vbtns">${buyListHas(x.t) ? `<button type="button" class="buybtn vb" data-vbuy="${esc(x.t)}">${VA.open === x.t ? "Close" : "Buy more"}</button>` : ""}<button type="button" class="vsell" data-vsell="${esc(x.t)}">${VA.sellOpen === x.t ? "Close" : "Sell"}</button></div>`;
    const m = VA.msg[x.t];
    return `<tr data-hl="pos:${esc(x.t)}"><td class="mono">${tkBtn(x.t)}${m && VA.open !== x.t && VA.sellOpen !== x.t ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}</td><td class="n">${Number(x.qty).toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
      <td class="n">${money(x.avg, 2)}</td><td class="n">${money(x.mv)}</td><td class="n ${cls(x.pl)}">${pct(x.plpc)} · ${(x.pl > 0 ? "+" : "") + money(x.pl)}</td><td class="nw">${held}${btns}</td></tr>${ro ? "" : vBuyBox(x.t, true) + vSellRow(x)}`;
  };
  return `${ro ? "" : tip("<b>Your Account.</b> Everything here is your own real-money Alpaca account. The tool buys new picks for you as they're found, each with an equal share of your balance; spare cash waits in a Treasury-bill fund (it earns about what short-term Treasury bills pay). Tap a ticker for its chart.")}
  <div class="filters"><span class="chip SHORT">Real money</span>${auto}<span class="foot">Updated ${esc(ago(p.updated))} (${esc(fmtET(p.updated))}) · market ${p.market_open ? "open" : "closed"}</span></div>
  <div class="tiles">
    <div class="tile"><span class="v">${money(p.equity)}</span><span class="l">Account value</span><span class="c">Cash ${money(p.cash)}</span></div>
    <div class="tile"><span class="v ${cls(p.day_pl)}">${p.day_pl == null ? "–" : (p.day_pl > 0 ? "+" : "") + money(p.day_pl)}</span><span class="l">${esc(dayLabel(p))}</span></div>
    <div class="tile"><span class="v ${cls(gain)}">${(gain > 0 ? "+" : "") + money(gain)}</span><span class="l">Gain on the tool's picks</span><span class="c">${money(invested)} in ${picks.length} pick${picks.length === 1 ? "" : "s"}</span></div>
    <div class="tile"><span class="v">${money(parked)}</span><span class="l">Waiting for picks</span><span class="c">In a Treasury-bill fund</span></div>
    ${C && C.since_start != null ? `<div class="tile"><span class="v ${cls(C.gain ?? C.since_start)}">${C.gain != null ? (C.gain > 0 ? "+" : "") + money(C.gain) : pct(C.since_start, 2)}</span><span class="l">Gain since ${esc(fmtDate(C.start))}</span><span class="c">${pct(C.since_start, 2)}${C.net_in ? ` · deposits not counted` : ""} · last close ${esc(fmtDate(C.date))}</span></div>` : ""}
    ${C && C.since_start != null && C.spy_since_start != null ? `<div class="tile"><span class="v ${cls(C.since_start - C.spy_since_start)}">${pnum((C.since_start - C.spy_since_start) * 100, 2)}</span><span class="l">${C.since_start >= C.spy_since_start ? "Ahead of" : "Behind"} the S&amp;P since start</span><span class="c">You ${pct(C.since_start, 2)} · S&amp;P ${pct(C.spy_since_start, 2)}</span></div>` : ""}
  </div>
  <section class="panel"><div class="ph"><h2>Positions</h2></div>
    ${pos.length ? `<div class="tbl"><table><thead><tr><th>Ticker</th><th class="n">Shares</th><th class="n">Avg cost</th><th class="n">Value</th><th class="n">Gain</th><th>Held</th></tr></thead><tbody>
    ${[...picks, ...own, ...park].map(row).join("")}</tbody></table></div>
    <p class="foot">The tool buys each new pick with an equal share of the account, sells it at the end of its hold (or keeps a winner while it's ahead of the S&amp;P 500), and keeps spare cash in a Treasury-bill fund. Most picks are held about ${(d.adaptive?.policy?.hold_other) ?? L.hold_days ?? 60} trading days.${ro ? "" : " Buy more shows on stocks that are on today's Buy list."}</p>`
    : `<div class="empty">Nothing bought yet. New picks are bought as they come in.</div>`}</section>
  <section class="panel"><div class="ph"><h2>Recent orders</h2></div>
    ${ords.length ? `<div class="tbl scroll"><table><thead><tr><th>When</th><th>Ticker</th><th>Side</th><th class="n">Shares</th><th class="n">Fill price</th><th>Status</th></tr></thead><tbody>
    ${ords.filter(o => !/^(SGOV|BIL|SHV|SPY|VOO|IVV)$/.test(o.t) || o.by !== "tool").map(o => `<tr><td class="nw">${esc(fmtET(o.at))}</td><td class="mono">${tkBtn(o.t)}</td><td>${esc(o.side)}</td><td class="n">${shQty(o)}</td><td class="n">${o.filled_px ? money(+o.filled_px, 2) : "–"}</td><td>${esc(String(o.status).replace(/_/g, " "))}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="empty">No orders yet.</div>`}</section>`;
}

// Sell: any position but the parked fund, whenever they want, with a note on what selling early means
function vSellRow(x) {
  if (VA.sellOpen !== x.t) return "";
  const t = x.t, mode = VA.sellMode[t] || "all", m = VA.msg[t], sh = Number(x.qty).toLocaleString(undefined, { maximumFractionDigits: 4 });
  const opt = (k, l) => `<button type="button" class="seg1" data-vsellmode="${esc(t)}|${k}" aria-pressed="${mode === k}">${l}</button>`;
  const note = x.bot
    ? `<b>Before you sell:</b> the tool holds each pick for a set time because that's what tested best, and it sells ${esc(t)} itself when the time comes (or keeps it longer while it's beating the S&amp;P 500). Selling early means this pick won't get the result the strategy measured, and the tool won't buy ${esc(t)} back for this pick for 45 days. Selling part leaves the rest under the tool's rules.`
    : `<b>Before you sell:</b> this is a stock you bought yourself; the tool doesn't manage it.`;
  return `<tr class="sellrow"><td colspan="6"><div class="sellbox">
    <div class="note warn" style="margin:0 0 10px">${note} A stock held under a year counts as a short-term gain or loss for taxes.</div>
    <div class="seg" role="group" aria-label="How much">${opt("all", "All")}${opt("shares", "Shares")}${opt("dollars", "$ amount")}</div>
    ${mode === "all" ? `<p style="margin:8px 0">Sell all ${sh} shares of ${esc(t)} (about ${money(x.mv)}).</p>`
      : `<div style="margin-top:8px"><input id="vsell-${esc(t)}" class="amt" type="number" inputmode="decimal" min="0" step="any" value="${esc(VA.amt["s" + t] ?? "")}" placeholder="${mode === "shares" ? "Shares" : "Dollars"}" style="max-width:140px"></div>`}
    <div style="display:flex;gap:10px;align-items:center;margin-top:8px"><button type="button" class="vsell go" data-vsellsend="${esc(t)}"${VA.busy ? " disabled" : ""}>${VA.busy ? "Sending…" : "Send sell order"}</button>
      <button type="button" class="link" data-vsell="${esc(t)}">Cancel</button></div>
    <div class="foot">Real money. Fills now if the market is open, otherwise at the next open.</div>
    ${m ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}</div></td></tr>`;
}

function renderConnect() {
  const m = VA.keyMsg;
  return `<section class="panel"><div class="ph"><h2>Connect your Alpaca account</h2></div>
  <p>The tool trades your own real-money Alpaca account: it buys each new pick with an equal share of your balance, sells on its schedule and keeps spare cash in a Treasury-bill fund. It needs a key from Alpaca to do that. You do this once.</p>
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
  <p class="foot">The key is stored encrypted and is never shown again, here or anywhere else. It lets the tool place trades; it can't move money out of your account. To stop the tool, delete the key in Alpaca at any time.</p></section>`;
}

document.addEventListener("submit", async ev => {
  if (ev.target.id !== "vkeys") return;
  ev.preventDefault();
  if (VA.keyBusy) return;
  VA.keyBusy = true; VA.keyMsg = null; render();
  try {
    const { js } = await api("/api/me/keys", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: $("#vk").value, secret: $("#vs").value }) });
    VA.keyMsg = { ok: true, text: js.message || "Connected." };
    if (state.data) state.data.viewer = { ...(state.data.viewer || {}), connected: true };
    if (SESSION_ME) SESSION_ME.connected = true;
  } catch (e) { VA.keyMsg = { ok: false, text: e.message || "Couldn't connect. Try again." }; }
  VA.keyBusy = false; render();
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
    if (mode !== "all" && !(amount > 0)) { VA.msg[t] = { ok: false, text: mode === "shares" ? "Enter how many shares." : "Enter a dollar amount." }; render(); return; }
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
  return `<section class="panel"><div class="morelist">${VIEWER_MORE.map(m => `<button type="button" class="moreitem" data-vok="1" data-more="${m.k}"><span class="ml">${esc(m.l)}</span><span class="md">${esc(m.d)}</span><span class="mc" aria-hidden="true">›</span></button>`).join("")}</div></section>`;
}
function renderViewerPage() {
  if (state.tab === "more") return renderViewerMore();
  if (state.tab === "vnotif") return vBack() + renderVNotif();
  if (state.tab === "vset") return vBack() + renderVSettings();
  if (state.tab === "faq") return vBack() + renderFaq();
  if (state.tab === "bug") return vBack() + renderBug();
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
  let tips = false;
  try { tips = localStorage.getItem("cc-tips") === "1"; } catch (e) {}
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
  try { localStorage.setItem("cc-tips", ev.target.checked ? "1" : "0"); } catch (e) {}
  document.documentElement.classList.toggle("tips", ev.target.checked);
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
    ["Where's my cash?", "Money not in a pick waits in a Treasury-bill fund (SGOV), which earns about what short-term Treasury bills pay and barely moves in price. The tool sells some of it when a new pick needs the money."],
    ["Can I buy or sell myself?", "You can buy a stock that's on today's Buy list (it's then handled like the tool's own buy) and sell anything you hold, any time, from Account. Both show a note first: the tool's results come from sticking to its rules."],
    ["What if I sell a pick early?", "It's sold right away (or at the next open). The tool won't buy that stock back for the same pick for 45 days. If you sell part, the rest stays under the tool's rules."],
    ["What do I see on a chart?", "The stock's price with every member's buys and sales on it, your own buys and sales (with your picture, if you added one), and \"What's going on\": a recent big move explained and the latest headlines."],
    ["Why isn't a stock I like on the list?", "A stock is only on the Buy list when a member bought it recently and the purchase scores in the top of all past purchases. Company search shows any stock's chart and members' trades."],
    ["Is this guaranteed to make money?", "No. The strategy is tested on past years, and past results don't promise future ones. It can lose money, sometimes for a long stretch. Only put in money you're comfortable leaving for a while."],
    ["Taxes?", "Alpaca sends you a 1099 each year with every sale. Selling a stock you held under a year is a short-term gain or loss, taxed like regular income."],
    ["How do I stop it?", "Ask the owner to pause your auto-buying, or delete the key in your Alpaca account; the tool can't trade without it. What you hold stays yours either way."],
  ];
  return `<section class="panel"><div class="ph"><h2>Questions and answers</h2></div>
    <div class="faq">${qa.map(([q, a]) => `<details><summary>${esc(q)}</summary><p>${esc(a)}</p></details>`).join("")}</div>
    <p class="foot">Something not covered here? Use Report a bug and ask; it goes to the owner.</p></section>`;
}
function renderBug() {
  const m = VA.bugMsg;
  return `<section class="panel"><div class="ph"><h2>Report a bug</h2></div>
    <p>What happened, and what did you expect? Which page were you on? It's emailed to the owner along with which phone you're using.</p>
    <form id="vbug" class="keysform" style="max-width:560px">
      <textarea id="vbugt" rows="6" maxlength="4000" placeholder="Describe the problem" required style="font:15px/1.4 var(--body);padding:10px 12px;border:1px solid var(--rule);border-radius:10px;background:var(--surface);color:var(--ink);width:100%;box-sizing:border-box"></textarea>
      <button type="submit" class="buybtn go"${VA.bugBusy ? " disabled" : ""}>${VA.bugBusy ? "Sending…" : "Send"}</button></form>
    ${m ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}</section>`;
}
document.addEventListener("submit", async ev => {
  if (ev.target.id !== "vbug" || !isViewer() || VA.bugBusy) return;
  ev.preventDefault();
  const text = $("#vbugt").value;
  VA.bugBusy = true; VA.bugMsg = null; render();
  try { const { js } = await api("/api/bug", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, page: VA.bugFrom || "" }) }); VA.bugMsg = { ok: true, text: js.message || "Sent." }; }
  catch (e) { VA.bugMsg = { ok: false, text: errText(e) }; }
  VA.bugBusy = false; render();
});

// ---------- the owner's Accounts page ----------
const ACC = { list: null, err: null, loading: false, busy: false, msg: null, view: null, data: null, live: null, adding: false };
function accLoad(force) {
  if (ACC.loading || (ACC.list && !force)) return;
  ACC.loading = true;
  api("/api/accounts").then(r => { ACC.list = r.js.accounts || []; ACC.err = null; }).catch(e => { ACC.err = e; })
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
    if (D.error) return head + `<div class="note err">${esc(D.error)}</div>`;
    return head + `<section class="panel"><div class="ph"><h2>${esc(a.label)}</h2><span class="sub">${esc(a.email)} · view only</span></div>
      ${D.problem ? `<div class="note warn">${esc(D.problem)}</div>` : ""}</section>` +
      renderViewerAccount({ viewer: { connected: a.keys, auto: a.auto }, portfolio: D.portfolio, close_report: D.close_report, adaptive: state.data?.adaptive }, { readOnly: true });
  }
  const m = ACC.msg;
  const list = ACC.list;
  const card = a => {
    const S = a.summary, st = a.status === "disabled" ? `<span class="chip avoid">Off</span>` : a.status === "invited" ? `<span class="chip watch">Invited</span>` : `<span class="chip BUY">Active</span>`;
    const conn = a.keys ? `Alpaca connected ${esc(a.key_hint || "")}` : "Hasn't connected Alpaca yet";
    const btn = (act, l, extra = "") => `<button type="button" class="btn ghost sm" data-acc="${act}" data-id="${esc(a.id)}"${ACC.busy ? " disabled" : ""}${extra}>${l}</button>`;
    return `<article class="acct">
      <div class="acct-h"><b>${esc(a.label)}</b>${st}${a.keys ? (a.auto ? `<span class="chip BUY">Auto-buying on</span>` : `<span class="chip avoid">Auto-buying paused</span>`) : ""}</div>
      <div class="foot">${esc(a.email)} · ${conn}${a.last_login ? ` · last sign-in ${esc(ago(a.last_login))}` : a.link_sent ? ` · link sent ${esc(ago(a.link_sent))}` : ""}</div>
      ${S ? `<div class="acct-s"><span><b>${money(S.equity)}</b> value</span><span class="${cls(S.day_pl)}">${S.day_pl == null ? "–" : (S.day_pl > 0 ? "+" : "") + money(S.day_pl)} today</span><span>${money(S.invested)} in picks</span><span>${S.positions} position${S.positions === 1 ? "" : "s"}</span><span class="foot">updated ${esc(ago(S.updated))}</span></div>` : ""}
      ${a.problem ? `<div class="foot neg">${esc(a.problem)}</div>` : ""}
      <div class="acct-b">${a.keys ? `<button type="button" class="btn sm" data-accview="${esc(a.id)}">View portfolio</button>` : ""}
        ${a.keys ? btn("auto", a.auto ? "Pause auto-buying" : "Turn auto-buying on", ` data-on="${a.auto ? "0" : "1"}"`) : ""}
        ${a.status !== "disabled" ? btn("link", "Send sign-in link") : ""}
        ${a.status !== "disabled" ? btn("signout", "Sign out everywhere") : ""}
        ${a.keys ? btn("reset-keys", "Reset keys") : ""}
        ${btn(a.status === "disabled" ? "enable" : "disable", a.status === "disabled" ? "Turn back on" : "Turn off")}
        ${btn("remove", "Remove")}</div>
    </article>`;
  };
  return `<section class="panel"><div class="ph"><h2>Accounts</h2><span class="sub">People you've added. They see Today, Members and their own Account only.</span></div>
    ${m ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}
    ${!list ? `<div class="empty">${ACC.err ? esc(errorText(ACC.err)) : "Loading…"}</div>` : list.length ? `<div class="accts">${list.map(card).join("")}</div>` : `<div class="empty">No accounts yet.</div>`}
  </section>
  <section class="panel"><div class="ph"><h2>Add someone</h2></div>
    <form id="accadd" class="keysform" autocomplete="off">
      <input id="accl" type="text" placeholder="Name, e.g. Mom" maxlength="40" required aria-label="Name">
      <input id="acce" type="email" placeholder="Their email" required aria-label="Email">
      <button type="submit" class="buybtn go"${ACC.busy ? " disabled" : ""}>Add and email a sign-in link</button>
    </form>
    <p class="foot">They get a sign-in link by email (from your Gmail), then connect their own real-money Alpaca account once in the app. From then on the tool buys the same picks for them, sized from their own balance, and sends alerts only to them. Don't charge for this: trading other people's money for a fee would need an adviser registration.</p>
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
  const b = ev.target.closest("[data-acc]");
  if (!b || ACC.busy) return;
  const act = b.dataset.acc, id = b.dataset.id, a = (ACC.list || []).find(x => x.id === id);
  if (!a) return;
  if (act === "remove") {
    const typed = prompt(`Remove ${a.label}? The tool stops trading their account (what they hold stays theirs in Alpaca). Type their email to confirm:`);
    if (typed == null) return;
    accPost({ action: "remove", id, confirm: typed.trim().toLowerCase() }); return;
  }
  if (act === "reset-keys" && !confirm(`Remove ${a.label}'s saved Alpaca keys? The tool stops trading their account until they connect again. Nothing is sold.`)) return;
  if (act === "disable" && !confirm(`Turn off ${a.label}'s account? They're signed out and the tool stops trading it. Nothing is sold.`)) return;
  accPost({ action: act, id, on: b.dataset.on === "1" });
});
