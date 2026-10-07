// Capitol Capital on Cloudflare: serves the app, checks the passcode login, hands out the dashboard data
// from private R2 storage, reads live numbers from Alpaca and places Buy-button orders.
// Nothing under /api answers without a valid login cookie (except the login itself).

const COOKIE = "cc_s";
const SESSION_DAYS = 180;
const MAX_FAILS = 10;            // wrong passcodes allowed per hour before locking
const PREFIX = "cs-", PARK_PREFIX = "cs-park-", PARK_CHOICES = ["SPY", "VOO", "IVV"];
const enc = new TextEncoder();
import { vapid, saveSub, sendAll, getSubs } from "./push.js";

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (!url.pathname.startsWith("/api/")) return env.ASSETS.fetch(req);
    try {
      return await api(req, env, url);
    } catch (e) {
      return json({ error: "server", message: String(e && e.message || e).slice(0, 200) }, 500);
    }
  },
  // every minute: send alerts the GitHub runs queued; every 2 minutes in market hours: alert on new fills
  async scheduled(ev, env, ctx) {
    await drainOutbox(env);
    const min = new Date(ev.scheduledTime).getUTCMinutes();
    if (min % 2 === 0) await checkFills(env);
    await startQuickCheck(env, min);
  },
};

async function api(req, env, url) {
  const p = url.pathname;
  if (req.method === "POST") {
    // only the app itself may post (blocks other sites from using a logged-in browser)
    const origin = req.headers.get("Origin");
    if (origin && origin !== url.origin) return json({ error: "origin" }, 403);
    if (!(req.headers.get("Content-Type") || "").startsWith("application/json")) return json({ error: "content-type" }, 415);
  }
  if (p === "/api/login" && req.method === "POST") return login(req, env);
  if (p === "/api/logout" && req.method === "POST") return json({ ok: true }, 200, { "Set-Cookie": `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict` });
  if (p === "/api/widget" && req.method === "GET") return widget(req, env, url);   // its own read-only key
  if (!(await validSession(req, env))) return json({ error: "login" }, 401);

  if (p === "/api/session") return json({ ok: true, mode: (env.TRADING_MODE || "paper") });
  if (p === "/api/data" && req.method === "GET") return fromR2(req, env, "dashboard_data.json");
  if (p === "/api/charts" && req.method === "GET") return fromR2(req, env, "ticker_charts.json");
  if (p === "/api/live" && req.method === "GET") return live(env);
  if (p === "/api/buy" && req.method === "POST") return buy(req, env);
  if (p === "/api/widget/script" && req.method === "GET") return widgetScript(env, url, false);
  if (p === "/api/widget/script" && req.method === "POST") return widgetScript(env, url, true);   // new key
  if (p === "/api/push/key" && req.method === "GET") return json({ key: (await vapid(env)).pub, phones: (await getSubs(env)).length });
  if (p === "/api/push/subscribe" && req.method === "POST") {
    let b = {};
    try { b = await req.json(); } catch (e) {}
    try { await saveSub(env, b.sub, url.origin); } catch (e) { return json({ error: "bad-subscription" }, 400); }
    const r = await sendAll(env, { title: "Alerts are on", body: "You'll get the daily close report, Buy-button fills, the tool's own trades and Worth a look flags.", tag: "welcome", url: "/" });
    return json({ ok: true, ...r });
  }
  return json({ error: "not-found" }, 404);
}

// ---------- helpers ----------
function json(obj, status = 200, headers = {}) {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers } });
}
const b64url = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
async function sha256(s) { return crypto.subtle.digest("SHA-256", enc.encode(s)); }
async function hmacKey(env) {
  if (!env.APP_PASSCODE) throw new Error("APP_PASSCODE is not set");
  // derived from the passcode, so changing the passcode logs every device out
  return crypto.subtle.importKey("raw", await sha256("cc-session-v1|" + env.APP_PASSCODE), { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}
async function sign(env, msg) { return b64url(await crypto.subtle.sign("HMAC", await hmacKey(env), enc.encode(msg))); }
function getCookie(req, name) {
  const m = (req.headers.get("Cookie") || "").match(new RegExp("(?:^|;\\s*)" + name + "=([^;]+)"));
  return m ? m[1] : null;
}
async function validSession(req, env) {
  const c = getCookie(req, COOKIE);
  if (!c) return false;
  const [exp, sig] = c.split(".");
  if (!exp || !sig || !(+exp > Date.now() / 1000)) return false;
  return timingSafeEqual(sig, await sign(env, "s." + exp));
}
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

// ---------- login ----------
async function login(req, env) {
  const now = Date.now() / 1000;
  let fails = { start: now, n: 0 };
  try { const o = await env.DATA.get("auth/fails.json"); if (o) fails = await o.json(); } catch (e) {}
  if (now - fails.start > 3600) fails = { start: now, n: 0 };
  if (fails.n >= MAX_FAILS) {
    const mins = Math.ceil((fails.start + 3600 - now) / 60);
    return json({ error: "locked", message: `Too many wrong tries. Try again in ${mins} min.` }, 429);
  }
  let body = {};
  try { body = await req.json(); } catch (e) {}
  const given = String(body.passcode || "");
  // compare hashes so the check takes the same time whatever was typed
  const ok = timingSafeEqual(b64url(await sha256("pc|" + given)), b64url(await sha256("pc|" + (env.APP_PASSCODE || "\u0000unset"))));
  if (!ok || !env.APP_PASSCODE) {
    fails.n += 1;
    await env.DATA.put("auth/fails.json", JSON.stringify(fails));
    const left = MAX_FAILS - fails.n;
    return json({ error: "wrong", message: left > 0 ? `Wrong passcode. ${left} ${left === 1 ? "try" : "tries"} left this hour.` : "Too many wrong tries. Try again in an hour." }, 401);
  }
  if (fails.n) await env.DATA.put("auth/fails.json", JSON.stringify({ start: now, n: 0 }));
  const exp = Math.floor(now + SESSION_DAYS * 86400);
  const val = exp + "." + await sign(env, "s." + exp);
  return json({ ok: true }, 200, { "Set-Cookie": `${COOKIE}=${val}; Path=/; Max-Age=${SESSION_DAYS * 86400}; HttpOnly; Secure; SameSite=Strict` });
}

// ---------- data files ----------
async function fromR2(req, env, key) {
  const inm = req.headers.get("If-None-Match");
  const obj = await env.DATA.get(key, inm ? { onlyIf: { etagDoesNotMatch: inm.replace(/"/g, "") } } : undefined);
  if (obj === null) return json({ error: "no-file", message: `${key} hasn't been uploaded yet` }, 404);
  const headers = { "Content-Type": "application/json", "Cache-Control": "private, no-cache", ETag: obj.httpEtag,
    "X-Uploaded": obj.uploaded.toISOString() };
  if (!("body" in obj) || !obj.body) return new Response(null, { status: 304, headers });
  return new Response(obj.body, { headers });
}

// ---------- Alpaca ----------
function alpaca(env) {
  const mode = (env.TRADING_MODE || "paper").toLowerCase() === "live" ? "live" : "paper";
  const base = mode === "live" ? "https://api.alpaca.markets" : "https://paper-api.alpaca.markets";
  const h = { "APCA-API-KEY-ID": env.ALPACA_KEY || "", "APCA-API-SECRET-KEY": env.ALPACA_SECRET || "" };
  const call = async (method, path, body) => {
    const r = await fetch((path.startsWith("http") ? "" : base) + path, { method, headers: { ...h, ...(body ? { "Content-Type": "application/json" } : {}) }, body: body ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await r.json(); } catch (e) {}
    return { status: r.status, ok: r.ok, data };
  };
  return { mode, ready: !!(env.ALPACA_KEY && env.ALPACA_SECRET), get: p => call("GET", p), post: (p, b) => call("POST", p, b) };
}

async function live(env) {
  const a = alpaca(env);
  if (!a.ready) return json({ error: "no-keys" }, 503);
  const after = new Date(Date.now() - 7 * 864e5).toISOString();
  const [acct, pos, clock, ords] = await Promise.all([
    a.get("/v2/account"), a.get("/v2/positions"), a.get("/v2/clock"),
    a.get(`/v2/orders?status=all&limit=200&direction=desc&after=${encodeURIComponent(after)}`)]);
  if (!acct.ok || !pos.ok) return json({ error: "alpaca", status: acct.status }, 502);
  const n = v => v == null ? null : +v;
  return json({
    at: new Date().toISOString(), mode: a.mode,
    equity: n(acct.data.equity), cash: n(acct.data.cash), last_equity: n(acct.data.last_equity), buying_power: n(acct.data.buying_power),
    market_open: !!(clock.data && clock.data.is_open), next_open: clock.data && clock.data.next_open,
    positions: (pos.data || []).map(p => ({ t: p.symbol.replace(/\./g, "-"), qty: n(p.qty), avg: n(p.avg_entry_price), px: n(p.current_price),
      mv: n(p.market_value), pl: n(p.unrealized_pl), plpc: n(p.unrealized_plpc), day: n(p.change_today) })),
    taps: (ords.data || []).filter(o => (o.client_order_id || "").startsWith("tap-") && o.side === "buy").map(o => ({
      t: o.symbol.replace(/\./g, "-"), status: o.status, notional: n(o.notional), qty: n(o.qty), filled_qty: n(o.filled_qty),
      filled_px: n(o.filled_avg_price), at: o.filled_at || o.submitted_at })),
  });
}

async function buy(req, env) {
  const a = alpaca(env);
  if (!a.ready) return json({ ok: false, why: "no-keys" }, 503);
  let body = {};
  try { body = await req.json(); } catch (e) {}
  const t = String(body.t || "").toUpperCase();
  const dollars = Math.round(+body.dollars * 100) / 100;
  if (!/^[A-Z][A-Z0-9-]{0,6}$/.test(t)) return json({ ok: false, why: "bad-request" }, 400);
  const cap = +(env.MAX_TAP_DOLLARS || 25000) || 25000;
  if (!(dollars >= 1 && dollars <= cap)) return json({ ok: false, why: "amount-over-limit", cap }, 400);
  const sym = t.replace(/-/g, ".");
  const [asset, acct, clock] = await Promise.all([a.get(`/v2/assets/${encodeURIComponent(sym)}`), a.get("/v2/account"), a.get("/v2/clock")]);
  if (!asset.ok || !acct.ok) return json({ ok: false, why: "not-found" }, 400);
  if (!asset.data.tradable) return json({ ok: false, why: "not-tradable" }, 400);
  if (dollars > +acct.data.buying_power) return json({ ok: false, why: "not-enough-cash" }, 400);
  const reqId = String(Date.now());
  // short on cash: sell some of the tool's parked S&P fund first, never your own shares (same as the GitHub version)
  try {
    const cash = +acct.data.cash;
    if (dollars > cash) {
      const since = new Date(Date.now() - 500 * 864e5).toISOString();
      const hist = await a.get(`/v2/orders?status=closed&limit=500&direction=desc&symbols=${PARK_CHOICES.join(",")}&after=${encodeURIComponent(since)}`);
      const first = {};
      for (const o of hist.data || []) {
        if (o.side !== "buy" || !o.filled_at || !(o.client_order_id || "").startsWith(PREFIX)) continue;
        if (!first[o.symbol] || o.filled_at < first[o.symbol].filled_at) first[o.symbol] = o;
      }
      const pk = Object.keys(first).find(s => first[s].client_order_id.startsWith(PARK_PREFIX));
      if (pk) {
        const pos = await a.get(`/v2/positions/${pk}`);
        const amt = Math.min(+(pos.data && pos.data.market_value) || 0, dollars - cash + 50);
        if (pos.ok && amt > 1) await a.post("/v2/orders", { symbol: pk, side: "sell", notional: amt.toFixed(2), type: "market",
          time_in_force: "day", client_order_id: `${PARK_PREFIX}tap-${reqId}`.slice(0, 48) });
      }
    }
  } catch (e) {}
  const order = { symbol: sym, side: "buy", type: "market", time_in_force: "day", client_order_id: `tap-${t}-${reqId}`.slice(0, 48) };
  if (asset.data.fractionable) order.notional = dollars.toFixed(2);
  else {
    const tr = await a.get(`https://data.alpaca.markets/v2/stocks/${encodeURIComponent(sym)}/trades/latest?feed=iex`);
    const price = tr.data && tr.data.trade && +tr.data.trade.p;
    if (!price) return json({ ok: false, why: "no-price" }, 400);
    const qty = Math.floor(dollars / price);
    if (qty < 1) return json({ ok: false, why: "amount-too-small" }, 400);
    order.qty = String(qty);
  }
  const r = await a.post("/v2/orders", order);
  if (!r.ok) return json({ ok: false, why: "rejected-" + r.status, message: r.data && r.data.message }, 400);
  return json({ ok: true, status: r.data.status, market_open: !!(clock.data && clock.data.is_open), next_open: clock.data && clock.data.next_open,
    qty: order.qty ? +order.qty : null });
}

// ---------- alerts ----------
const ET = () => new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
const money0 = v => "$" + Math.round(v).toLocaleString("en-US");

async function drainOutbox(env) {
  const list = await env.DATA.list({ prefix: "push/outbox/", limit: 50 });
  for (const o of list.objects) {
    try {
      const age = Date.now() - o.uploaded.getTime();
      if (age < 6 * 3600e3) {
        const msg = await (await env.DATA.get(o.key))?.json();
        if (msg && msg.title) await sendAll(env, msg);
      }
    } catch (e) {}
    await env.DATA.delete(o.key);
  }
}

async function reasons(env) {
  // the daily run saves why the tool bought or sold each stock (newest files win)
  const out = {};
  const list = await env.DATA.list({ prefix: "push/why/", limit: 100 });
  const keys = list.objects.map(o => o.key).sort().slice(-6);
  for (const k of keys) {
    try { Object.assign(out, await (await env.DATA.get(k)).json()); } catch (e) {}
  }
  return out;
}

async function checkFills(env) {
  const et = ET(), h = et.getHours() + et.getMinutes() / 60, wd = et.getDay();
  if (wd === 0 || wd === 6 || h < 9 || h > 20.5) return;
  const a = alpaca(env);
  if (!a.ready) return;
  const stObj = await env.DATA.get("push/fills_state.json");
  const st = stObj ? await stObj.json() : null;
  const after = new Date(Date.now() - 4 * 864e5).toISOString();
  const r = await a.get(`/v2/orders?status=closed&direction=desc&limit=500&after=${encodeURIComponent(after)}`);
  if (!r.ok || !Array.isArray(r.data)) return;
  const filled = r.data.filter(o => o.status === "filled");
  if (!st) {     // first check: remember what's already filled, alert on nothing old
    await env.DATA.put("push/fills_state.json", JSON.stringify({ seen: filled.map(o => o.id) }));
    return;
  }
  const seen = new Set(st.seen || []);
  const fresh = filled.filter(o => !seen.has(o.id)).reverse();
  if (!fresh.length) return;
  const why = await reasons(env);
  const msgs = [];
  for (const o of fresh) {
    seen.add(o.id);
    const cid = o.client_order_id || "";
    if (cid.startsWith("cs-park-")) continue;              // the tool parking spare cash in the S&P fund
    const t = o.symbol.replace(/\./g, "-"), q = +o.filled_qty, p = +o.filled_avg_price;
    const sh = `${q % 1 ? q.toFixed(4).replace(/0+$/, "") : q} share${q === 1 ? "" : "s"} at $${p.toFixed(2)} (${money0(q * p)})`;
    const w = why[`${t}|${o.side}`];
    if (o.side === "buy" && cid.startsWith("tap-")) msgs.push({ s: `bought ${t}`, title: `Bought ${t}`, body: `Your Buy-button order filled: ${sh}.` });
    else if (o.side === "buy" && cid.startsWith("cs-")) msgs.push({ s: `bought ${t}`, title: `Tool bought ${t}`, body: sh + (w ? ` · ${w}` : "") + "." });
    else if (o.side === "sell") msgs.push({ s: `sold ${t}`, title: `Sold ${t}`, body: sh + (w ? ` · ${w}` : "") + "." });
  }
  await env.DATA.put("push/fills_state.json", JSON.stringify({ seen: [...seen].slice(-1500) }));
  if (msgs.length > 3) {
    await sendAll(env, { title: `${msgs.length} trades filled`, body: msgs.map(m => m.s).join(", ").replace(/^./, c => c.toUpperCase()) + ".", tag: "fills", url: "/#port" });
  } else {
    for (const m of msgs) await sendAll(env, { title: m.title, body: m.body, tag: "fill-" + m.title, url: "/#port" });
  }
}

// ---------- fast filing check ----------
// GitHub's own 15-minute schedule often runs 15-40 minutes late. This starts the quick check (new House and Senate
// filings, Buy orders, portfolio) on time: every 5 minutes on weekdays 6 AM-9 PM Eastern, every 30 minutes otherwise.
// A new filing then starts the full update within a few minutes of being posted.
async function startQuickCheck(env, min) {
  if (!env.GH_DISPATCH_TOKEN) return;
  const et = ET(), h = et.getHours(), wd = et.getDay();
  const busy = wd >= 1 && wd <= 5 && h >= 6 && h < 21;
  if (min % (busy ? 5 : 30) !== 0) return;
  try {
    await fetch(`https://api.github.com/repos/${env.GH_REPO || "e36meister/congress-signals"}/actions/workflows/quick.yml/dispatches`, {
      method: "POST",
      headers: { Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`, Accept: "application/vnd.github+json",
                 "User-Agent": "capitol-capital-worker", "Content-Type": "application/json" },
      body: JSON.stringify({ ref: "main" }),
    });
  } catch (e) {}
}

// ---------- home-screen widget (Scriptable) ----------
// The widget has its own key, separate from the login: it can only read the summary below, never trade.
async function widgetKey(env, renew) {
  const o = renew ? null : await env.DATA.get("widget/key.txt");
  if (o) return (await o.text()).trim();
  const k = b64url(crypto.getRandomValues(new Uint8Array(24)));
  await env.DATA.put("widget/key.txt", k);
  return k;
}

async function widget(req, env, url) {
  const given = req.headers.get("X-Widget-Key") || url.searchParams.get("k") || "";
  const o = await env.DATA.get("widget/key.txt");
  const key = o ? (await o.text()).trim() : "";
  if (!key || !timingSafeEqual(given, key)) return json({ error: "key" }, 401);
  const dobj = await env.DATA.get("dashboard_data.json");
  const d = dobj ? await dobj.json() : {};
  const cr = d.close_report || {}, port = d.portfolio || {};
  const out = { at: new Date().toISOString(), equity: port.equity ?? null, day_pl: port.day_pl ?? null,
    market_open: !!port.market_open, live: false };
  const a = alpaca(env);
  if (a.ready) {
    try {
      const [acct, clock] = await Promise.all([a.get("/v2/account"), a.get("/v2/clock")]);
      if (acct.ok) {
        out.equity = +acct.data.equity;
        if (+acct.data.last_equity) out.day_pl = out.equity - +acct.data.last_equity;
        out.live = true;
      }
      if (clock.ok) out.market_open = !!clock.data.is_open;
    } catch (e) {}
  }
  if (out.equity != null && out.day_pl != null && out.equity - out.day_pl) out.day_pct = out.day_pl / (out.equity - out.day_pl) * 100;
  const s = cr.series || {};
  if ((s.account || []).length) {
    out.since = { date: cr.date, acct: cr.since_start, spy: cr.spy_since_start,
      gap: cr.since_start != null && cr.spy_since_start != null ? cr.since_start - cr.spy_since_start : null };
    out.spark = { acct: s.account.slice(-60), spy: (s.spy || []).slice(-60) };
  }
  const park = new Set(["SPY", "VOO", "IVV"]), seen = new Set();
  out.buys = (port.orders || []).filter(x => x.side === "buy" && (x.by === "tool" || x.by === "tap") && x.status === "filled" && !park.has(x.t))
    .filter(x => !seen.has(x.t) && seen.add(x.t)).slice(0, 4).map(x => ({ t: x.t, at: x.at, by: x.by }));
  return json(out);
}

async function widgetScript(env, url, renew) {
  const key = await widgetKey(env, renew);
  const js = WIDGET_JS.replace("__URL__", url.origin).replace("__KEY__", key);
  return new Response(js, { headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" } });
}

const WIDGET_JS = `// Capitol Capital widget for Scriptable (large size). Read-only: it can't trade.
const URL_ = "__URL__", KEY = "__KEY__";
const BG = new Color("#07090A"), GREEN = new Color("#39FF88"), RED = new Color("#FF3B4E"),
      INK = new Color("#E9EEF2"), DIM = new Color("#7C8696"), GRID = new Color("#1A2027");
const fm = FileManager.local(), cache = fm.joinPath(fm.documentsDirectory(), "capitol-capital-widget.json");
async function load() {
  try {
    const r = new Request(URL_ + "/api/widget"); r.headers = { "X-Widget-Key": KEY }; r.timeoutInterval = 20;
    const d = await r.loadJSON();
    if (d && d.error) throw new Error(d.error);
    fm.writeString(cache, JSON.stringify(d)); return d;
  } catch (e) { return fm.fileExists(cache) ? Object.assign(JSON.parse(fm.readString(cache)), { stale: true }) : null; }
}
const money = v => v == null ? "–" : (v < 0 ? "-$" : "$") + Math.abs(Math.round(v)).toLocaleString("en-US");
const pct = (v, dp = 2) => v == null ? "–" : (v > 0 ? "+" : "") + v.toFixed(dp) + "%";
const col = v => v == null || v === 0 ? DIM : v > 0 ? GREEN : RED;
function spark(a, b, w, h) {
  const dc = new DrawContext(); dc.size = new Size(w, h); dc.opaque = false; dc.respectScreenScale = true;
  const all = a.concat(b).filter(x => x != null); if (all.length < 2) return dc.getImage();
  let lo = Math.min(...all, 0), hi = Math.max(...all, 0); if (hi - lo < 0.5) { hi += 0.25; lo -= 0.25; }
  const y = v => h - 4 - (v - lo) / (hi - lo) * (h - 8);
  const zero = new Path(); zero.move(new Point(0, y(0))); zero.addLine(new Point(w, y(0)));
  dc.addPath(zero); dc.setStrokeColor(GRID); dc.setLineWidth(1); dc.strokePath();
  const line = (arr, c, lw) => { const n = arr.length; if (n < 2) return; const p = new Path();
    arr.forEach((v, i) => { const pt = new Point(i / (n - 1) * w, y(v ?? 0)); i ? p.addLine(pt) : p.move(pt); });
    dc.addPath(p); dc.setStrokeColor(c); dc.setLineWidth(lw); dc.strokePath(); };
  line(b, DIM, 1.5); line(a, GREEN, 2.5);
  return dc.getImage();
}
const d = await load();
const w = new ListWidget(); w.backgroundColor = BG; w.url = URL_; w.setPadding(16, 16, 16, 16);
w.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
const head = w.addStack(); head.centerAlignContent();
const t = head.addText("CAPITOL CAPITAL"); t.font = Font.semiboldMonospacedSystemFont(11); t.textColor = GREEN;
head.addSpacer();
const st = head.addText(d ? (d.stale ? "offline · " : "") + (d.market_open ? "market open" : "market closed") : ""); st.font = Font.systemFont(10); st.textColor = DIM;
w.addSpacer(8);
if (!d) { const e = w.addText("Can't reach the app yet"); e.textColor = INK; }
else {
  const row = w.addStack(); row.bottomAlignContent();
  const eq = row.addText(money(d.equity)); eq.font = Font.boldMonospacedSystemFont(30); eq.textColor = INK; eq.minimumScaleFactor = 0.6;
  row.addSpacer(10);
  const dy = row.addText((d.day_pl > 0 ? "+" : "") + money(d.day_pl) + " · " + pct(d.day_pct) + " today"); dy.font = Font.mediumMonospacedSystemFont(13); dy.textColor = col(d.day_pl);
  w.addSpacer(10);
  if (d.spark) { const img = w.addImage(spark(d.spark.acct, d.spark.spy, 320, 120)); img.imageSize = new Size(320, 120); }
  w.addSpacer(6);
  if (d.since) {
    const s = w.addStack();
    const a = s.addText("You " + pct(d.since.acct)); a.font = Font.mediumMonospacedSystemFont(12); a.textColor = GREEN;
    s.addSpacer(10);
    const b = s.addText("S&P " + pct(d.since.spy)); b.font = Font.mediumMonospacedSystemFont(12); b.textColor = DIM;
    s.addSpacer();
    const g = s.addText((d.since.gap >= 0 ? "ahead " : "behind ") + Math.abs(d.since.gap ?? 0).toFixed(2) + " pts"); g.font = Font.mediumMonospacedSystemFont(12); g.textColor = col(d.since.gap);
  }
  w.addSpacer();
  const nb = w.addText("NEW BUYS"); nb.font = Font.semiboldMonospacedSystemFont(10); nb.textColor = DIM;
  w.addSpacer(3);
  const list = (d.buys || []).map(b => b.t).join("   ") || "none yet";
  const l = w.addText(list); l.font = Font.boldMonospacedSystemFont(16); l.textColor = INK;
  w.addSpacer(6);
  const up = w.addText("since start = last close" + (d.since && d.since.date ? " (" + d.since.date + ")" : "")); up.font = Font.systemFont(9); up.textColor = DIM;
}
if (config.runsInWidget) Script.setWidget(w); else await w.presentLarge();
Script.complete();
`;
