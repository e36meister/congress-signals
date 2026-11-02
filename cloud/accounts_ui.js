// ---------- family accounts (owner, Nov 2 2026) ----------
// A family account sees Today, Members, Alerts and its own Account, nothing else. The server only answers those
// (cloud/accounts.js); this hides every button and link that would point anywhere else, and render() sends any
// other page back to Today. The owner gets the Accounts page (under More) to add people and see each portfolio.
let ROLE = "owner", SESSION_ME = null;
const isViewer = () => ROLE === "viewer";
const VIEWER_TABS = new Set(["today", "members", "port", "alerts"]);
function applyRole(s) {
  ROLE = s && s.role === "viewer" ? "viewer" : "owner";
  SESSION_ME = s || null;
  document.documentElement.classList.toggle("viewer", isViewer());
  if (isViewer() && !VIEWER_TABS.has(state.tab)) state.tab = "today";
}
function guardTab() { if (isViewer() && !VIEWER_TABS.has(state.tab)) state.tab = "today"; }

const VA = { open: null, amt: {}, msg: {}, busy: false, keyMsg: null, keyBusy: false };

// ---------- the Account tab for a family account (also the owner's read-only view of it) ----------
// opts.readOnly: the owner looking at someone's account (no Buy more, no setup form)
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
    const btn = !ro && x.bot && x.sleeve !== "park" ? `<div style="margin-top:6px"><button type="button" class="buybtn more" data-vmore="${esc(x.t)}">${VA.open === x.t ? "Close" : "Buy more"}</button></div>` : "";
    const m = VA.msg[x.t];
    return `<tr data-hl="pos:${esc(x.t)}"><td class="mono">${tkBtn(x.t)}${m && VA.open !== x.t ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}</td><td class="n">${Number(x.qty).toLocaleString(undefined, { maximumFractionDigits: 4 })}</td>
      <td class="n">${money(x.avg, 2)}</td><td class="n">${money(x.mv)}</td><td class="n ${cls(x.pl)}">${pct(x.plpc)} · ${(x.pl > 0 ? "+" : "") + money(x.pl)}</td><td class="nw">${held}${btn}</td></tr>${!ro && VA.open === x.t ? moreRow(x, L) : ""}`;
  };
  return `<div class="filters"><span class="chip SHORT">Real money</span>${auto}<span class="foot">Updated ${esc(ago(p.updated))} (${esc(fmtET(p.updated))}) · market ${p.market_open ? "open" : "closed"}</span></div>
  <div class="tiles">
    <div class="tile"><span class="v">${money(p.equity)}</span><span class="l">Account value</span><span class="c">Cash ${money(p.cash)}</span></div>
    <div class="tile"><span class="v ${cls(p.day_pl)}">${p.day_pl == null ? "–" : (p.day_pl > 0 ? "+" : "") + money(p.day_pl)}</span><span class="l">${esc(dayLabel(p))}</span></div>
    <div class="tile"><span class="v ${cls(gain)}">${(gain > 0 ? "+" : "") + money(gain)}</span><span class="l">Gain on the tool's picks</span><span class="c">${money(invested)} in ${picks.length} pick${picks.length === 1 ? "" : "s"}</span></div>
    <div class="tile"><span class="v">${money(parked)}</span><span class="l">Waiting for picks</span><span class="c">In a Treasury-bill fund</span></div>
    ${C && C.since_start != null ? `<div class="tile"><span class="v ${cls(C.since_start - (C.spy_since_start ?? 0))}">${pct(C.since_start, 2)}</span><span class="l">Since ${esc(fmtDate(C.start))}</span><span class="c">S&amp;P 500 ${pct(C.spy_since_start, 2)} · last close ${esc(fmtDate(C.date))}</span></div>` : ""}
  </div>
  <section class="panel"><div class="ph"><h2>Positions</h2></div>
    ${pos.length ? `<div class="tbl"><table><thead><tr><th>Ticker</th><th class="n">Shares</th><th class="n">Avg cost</th><th class="n">Value</th><th class="n">Gain</th><th>Held</th></tr></thead><tbody>
    ${[...picks, ...own, ...park].map(row).join("")}</tbody></table></div>
    <p class="foot">The tool buys each new pick with an equal share of the account, sells it at the end of its hold (or keeps a winner while it's ahead of the S&amp;P 500), and keeps spare cash in a Treasury-bill fund. Most picks are held about ${(d.adaptive?.policy?.hold_other) ?? L.hold_days ?? 60} trading days.</p>`
    : `<div class="empty">Nothing bought yet. New picks are bought as they come in.</div>`}</section>
  <section class="panel"><div class="ph"><h2>Recent orders</h2></div>
    ${ords.length ? `<div class="tbl scroll"><table><thead><tr><th>When</th><th>Ticker</th><th>Side</th><th class="n">Shares</th><th class="n">Fill price</th><th>Status</th></tr></thead><tbody>
    ${ords.filter(o => !/^(SGOV|BIL|SHV|SPY|VOO|IVV)$/.test(o.t) || o.by !== "tool").map(o => `<tr><td class="nw">${esc(fmtET(o.at))}</td><td class="mono">${tkBtn(o.t)}</td><td>${esc(o.side)}</td><td class="n">${shQty(o)}</td><td class="n">${o.filled_px ? money(+o.filled_px, 2) : "–"}</td><td>${esc(String(o.status).replace(/_/g, " "))}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="empty">No orders yet.</div>`}</section>`;
}

// Buy more: the owner asked for a clear note that equal sizing is what tested best
function moreRow(x, L) {
  const t = x.t, v = VA.amt[t] ?? (L.per_pick ? Math.round(L.per_pick * 0.25) : ""), m = VA.msg[t];
  return `<tr class="sellrow"><td colspan="6"><div class="sellbox">
    <div class="note warn" style="margin:0 0 10px"><b>Before you add more:</b> the tool gives every pick the same amount on purpose. Its weekly check keeps testing same-size picks against putting more into the stronger ones, and same-size has stayed ahead. Extra buys by hand were never part of that test, and putting more into one stock makes your account ride more on it. The extra shares are sold with the rest when the tool sells ${esc(t)}.</div>
    <label class="foot" for="vmore-${esc(t)}">Amount in dollars</label>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:4px">
      <input id="vmore-${esc(t)}" class="amt" type="number" inputmode="decimal" min="1" step="1" value="${esc(v)}" style="max-width:140px">
      <button type="button" class="buybtn go" data-vmoresend="${esc(t)}"${VA.busy ? " disabled" : ""}>${VA.busy ? "Sending…" : "Buy more " + esc(t)}</button>
      <button type="button" class="link" data-vmore="${esc(t)}">Cancel</button></div>
    <div class="foot">Real money. Fills now if the market is open, otherwise at the next open. If cash is short, some of the Treasury-bill fund is sold first.</div>
    ${m ? `<div class="tapmsg ${m.ok ? "pos" : "neg"}">${esc(m.text)}</div>` : ""}
  </div></td></tr>`;
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

document.addEventListener("click", async ev => {
  const o = ev.target.closest("[data-vmore]");
  if (o && isViewer()) { const t = o.dataset.vmore; VA.open = VA.open === t ? null : t; delete VA.msg[t]; render(); return; }
  const s = ev.target.closest("[data-vmoresend]");
  if (!s || !isViewer() || VA.busy) return;
  const t = s.dataset.vmoresend, el = document.getElementById("vmore-" + t), dollars = parseFloat(el && el.value);
  VA.amt[t] = el ? el.value : "";
  if (!(dollars >= 1)) { VA.msg[t] = { ok: false, text: "Enter an amount of $1 or more." }; render(); return; }
  VA.busy = true; render();
  try {
    const { js } = await api("/api/buy-more", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t, dollars }) });
    VA.msg[t] = { ok: true, text: `Order sent: ${money(dollars)} more of ${t}` + (js.market_open ? "." : ". It fills at the next market open.") };
    VA.open = null; delete VA.amt[t];
    setTimeout(loadLive, 4000);
  } catch (e) {
    VA.msg[t] = { ok: false, text: "Not sent: " + (e.message && e.message !== "error 400" ? e.message : (WHY_BUY[e.why] || e.why || "try again")) + "." };
  }
  VA.busy = false; render();
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
