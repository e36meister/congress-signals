// Capitol Capital on Cloudflare: serves the app, checks the passcode login, hands out the dashboard data
// from private R2 storage, reads live numbers from Alpaca and places Buy-button orders.
// Nothing under /api answers without a valid login cookie (except the login itself).

const COOKIE = "cc_s";
const SESSION_DAYS = 180;
const MAX_FAILS = 10;            // wrong passcodes allowed per hour before locking
const PREFIX = "cs-", PARK_PREFIX = "cs-park-", PARK_CHOICES = ["SPY", "VOO", "IVV", "SGOV", "BIL", "SHV"];
const enc = new TextEncoder();
import { vapid, saveSub, sendAll, getSubs } from "./push.js";

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (!url.pathname.startsWith("/api/")) return page(req, env, url);
    try {
      return await api(req, env, url);
    } catch (e) {
      return json({ error: "server", message: String(e && e.message || e).slice(0, 200) }, 500);
    }
  },
  // every minute: send alerts the GitHub runs queued; every 2 minutes in market hours: alert on new fills
  // each step reports to Data health when it fails, instead of failing silently
  async scheduled(ev, env, ctx) {
    const step = async (src, label, fn) => { try { await fn(); await wOk(env, src); } catch (e) { await wProblem(env, src, `${label} failed: ${String(e && e.message || e).slice(0, 160)}`); } };
    await step("outbox", "Sending phone alerts", () => drainOutbox(env));
    const min = new Date(ev.scheduledTime).getUTCMinutes();
    if (min % 2 === 0) await step("fills", "Checking for new fills", () => checkFills(env));
    if (min % 3 === 0) await step("buy", "Managing pre-market and after-hours orders", () => extendedHours(env, new Date(ev.scheduledTime)));
    await step("quick", "Starting the filings check", () => startQuickCheck(env, min));
    await step("daily", "Starting the daily update", () => startDailyOnTime(env, new Date(ev.scheduledTime)));
    if (min === 5) await step("substack", "Reading members' Substack feeds", () => mirrorSubstack(env));
    await step("senate-reminder", "Sending the Senate filings reminder", () => senateReminder(env, new Date(ev.scheduledTime)));
    await step("digest", "Sending the data-health summary", () => sendDigest(env, new Date(ev.scheduledTime)));
    if (min === 7) await step("posts", "Starting the hourly posts job", () => dispatch(env, "posts.yml", {}));
  },
};

// ---------- the app itself: only the icon, manifest and alert worker are public ----------
// Everything else (the app page, its code, the profile photo) needs the login cookie.
// Without it, a page visit gets a small login page that holds nothing about the app.
const PUBLIC_FILES = new Set(["/manifest.webmanifest", "/icon-180.png", "/icon-192.png", "/icon-512.png", "/icon.svg", "/sw.js", "/favicon.ico"]);
async function page(req, env, url) {
  if (PUBLIC_FILES.has(url.pathname)) return env.ASSETS.fetch(req);
  if (await validSession(req, env)) {
    const r = await env.ASSETS.fetch(req);
    const h = new Headers(r.headers);
    h.set("Cache-Control", "private, no-store");   // never left in a shared or offline cache
    return new Response(r.body, { status: r.status, statusText: r.statusText, headers: h });
  }
  const html = req.method === "GET" && (req.headers.get("Accept") || "").includes("text/html");
  if (!html) return new Response("Log in first", { status: 401, headers: { "Cache-Control": "no-store" } });
  return new Response(LOGIN_PAGE, { status: 200, headers: {
    "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer" } });
}
const LOGIN_PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow"><meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><meta name="apple-mobile-web-app-title" content="Capitol Capital">
<link rel="manifest" href="/manifest.webmanifest"><link rel="apple-touch-icon" href="/icon-180.png">
<title>Capitol Capital</title><style>
:root{--ground:#F3F5F8;--surface:#FFF;--ink:#152033;--ink2:#46536A;--rule:#DCE1E8;--accent:#1D4F91;--on:#fff}
@media (prefers-color-scheme:dark){:root{--ground:#0E131A;--surface:#151C26;--ink:#E6EBF2;--ink2:#B4BECC;--rule:#27303D;--accent:#7FAEEA;--on:#0E131A}}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--ground);color:var(--ink);font:16px system-ui,-apple-system,"Segoe UI",sans-serif}
main{min-height:100%;display:grid;place-items:center;padding:24px 16px}
form{width:100%;max-width:340px;display:grid;gap:14px;text-align:center}
img{width:84px;height:84px;border-radius:20px;justify-self:center;box-shadow:0 6px 20px rgba(0,0,0,.15)}
h1{margin:0;font-weight:600;font-size:26px}p{margin:0;color:var(--ink2);font-size:14px}
input{font:500 18px ui-monospace,Menlo,monospace;padding:12px 14px;border:1px solid var(--rule);border-radius:10px;background:var(--surface);color:var(--ink);text-align:center;width:100%}
button{font:600 16px inherit;padding:12px;border-radius:10px;border:0;background:var(--accent);color:var(--on);cursor:pointer}
button:disabled{opacity:.6}.msg{min-height:1.2em;font-size:14px;color:#C0392B}
</style></head><body><main><form id="f" autocomplete="on">
<img src="/icon-180.png" alt=""><h1>Capitol Capital</h1>
<p>Enter your passcode. This phone stays logged in for 6 months.</p>
<input type="text" name="username" value="Capitol Capital" autocomplete="username" hidden>
<input type="password" id="pc" name="password" autocomplete="current-password" placeholder="Passcode" required aria-label="Passcode" autofocus>
<button type="submit" id="go">Log in</button><div class="msg" id="msg" role="alert"></div>
</form></main><script>
document.getElementById("f").addEventListener("submit", async e => {
  e.preventDefault(); const go = document.getElementById("go"), msg = document.getElementById("msg");
  go.disabled = true; msg.textContent = "";
  try {
    const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
      body: JSON.stringify({ passcode: document.getElementById("pc").value }) });
    if (r.ok) { location.reload(); return; }
    let j = {}; try { j = await r.json(); } catch (x) {}
    msg.textContent = j.message || "Couldn't log in. Try again.";
  } catch (x) { msg.textContent = "No connection. Try again."; }
  go.disabled = false;
});
</script></body></html>`;

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
  if (p === "/api/senate-import" && req.method === "POST") return senateImport(req, env);   // iPhone Shortcut, its own key
  if (!(await validSession(req, env))) return json({ error: "login" }, 401);

  if (p === "/api/session") return json({ ok: true, mode: (env.TRADING_MODE || "paper") });
  if (p === "/api/data" && req.method === "GET") return fromR2(req, env, "dashboard_data.json");
  if (p === "/api/charts" && req.method === "GET") return fromR2(req, env, "ticker_charts.json");
  if (p === "/api/members" && req.method === "GET") return fromR2(req, env, "members/index.json");
  if (p === "/api/posts" && req.method === "GET") return fromR2(req, env, "posts/feed.json");
  if (p === "/api/movers" && req.method === "GET") return fromR2(req, env, "movers/latest.json");
  if (p === "/api/bonds" && req.method === "GET") return fromR2(req, env, "bonds/latest.json");
  if (p === "/api/notes" && req.method === "GET") return fromR2(req, env, "notes/summary.json");
  if (p === "/api/member" && req.method === "GET") {
    const k = url.searchParams.get("k") || "";
    if (!/^([A-Z]\d{6}|n-[a-z0-9-]{1,40})$/.test(k)) return json({ error: "bad-member" }, 400);
    return fromR2(req, env, `members/${k}.json`);
  }
  if (p === "/api/notifications" && req.method === "GET") return json({ items: await visibleAlerts(env) });
  if (p === "/api/notifications/clear" && req.method === "POST") return clearAlerts(req, env);
  if (p === "/api/search" && req.method === "GET") return tickerSearch(env, url);
  if (p === "/api/chart" && req.method === "GET") return oneChart(env, url);
  if (p === "/api/news" && req.method === "GET") return stockNews(env, url);
  if (p === "/api/about" && req.method === "GET") return about(env, url);
  if (p === "/api/tax" && req.method === "GET") return taxIndex(env);
  if (p === "/api/tax/file" && req.method === "GET") return taxFile(env, url);
  if (p === "/api/tie-reviews" && req.method === "GET") return json(await tieReviews(env));
  if (p === "/api/context" && req.method === "GET") return tradeContext(env, url);
  if (p === "/api/health" && req.method === "GET") return health(env);
  if (p === "/api/people-review" && req.method === "GET") return peopleReviewList(env);
  if (p === "/api/people-review" && req.method === "POST") return peopleReview(req, env);
  if (p === "/api/tie-review" && req.method === "POST") return tieReview(req, env);
  if (p === "/api/live" && req.method === "GET") return live(env);
  if (p === "/api/buy" && req.method === "POST") return buy(req, env);
  if (p === "/api/sell" && req.method === "POST") return sell(req, env);
  if (p === "/api/manual" && req.method === "GET") return manualState(env);
  if (p === "/api/manual/keep" && req.method === "POST") return manualKeep(req, env);
  if (p === "/api/senate-import/setup" && req.method === "GET") return json({ url: url.origin + "/api/senate-import", key: await importKey(env, false), recent: await importLog(env) });
  if (p === "/api/senate-import/setup" && req.method === "POST") return json({ url: url.origin + "/api/senate-import", key: await importKey(env, true), recent: await importLog(env) });
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
  return { mode, ready: !!(env.ALPACA_KEY && env.ALPACA_SECRET), get: p => call("GET", p), post: (p, b) => call("POST", p, b),
           patch: (p, b) => call("PATCH", p, b), del: p => call("DELETE", p) };
}

async function live(env) {
  const a = alpaca(env);
  if (!a.ready) return json({ error: "no-keys" }, 503);
  const after = new Date(Date.now() - 7 * 864e5).toISOString();
  const [acct, pos, clock, ords, spy] = await Promise.all([
    a.get("/v2/account"), a.get("/v2/positions"), a.get("/v2/clock"),
    a.get(`/v2/orders?status=all&limit=200&direction=desc&after=${encodeURIComponent(after)}`),
    a.get("https://data.alpaca.markets/v2/stocks/SPY/snapshot?feed=iex")]);
  if (!acct.ok || !pos.ok) {
    await wProblem(env, "alpaca-live", `Alpaca didn't answer the live account check (HTTP ${acct.status}/${pos.status})`);
    return json({ error: "alpaca", status: acct.status }, 502);
  }
  await wOk(env, "alpaca-live");
  if (!spy.ok || !spy.data?.latestTrade) await wProblem(env, "spy-live", `Live S&P price didn't load (HTTP ${spy.status}); the Account "vs the S&P" box is using the last close`);
  else await wOk(env, "spy-live");
  const n = v => v == null ? null : +v;
  return json({
    at: new Date().toISOString(), mode: a.mode,
    equity: n(acct.data.equity), cash: n(acct.data.cash), last_equity: n(acct.data.last_equity), buying_power: n(acct.data.buying_power),
    market_open: !!(clock.data && clock.data.is_open), next_open: clock.data && clock.data.next_open,
    // S&P 500 (SPY) now, at the last close and today's bar, for the live "vs the S&P" number on Account
    spy: spy.ok && spy.data ? { px: n(spy.data.latestTrade?.p), prev: n(spy.data.prevDailyBar?.c), day: n(spy.data.dailyBar?.c),
      day_t: spy.data.dailyBar?.t || null } : null,
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
  // short on cash: sell some of the tool's parked fund first, never your own shares (same as the GitHub version)
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

// ---------- Sell button (Account tab): all of a position, a number of shares, or a dollar amount ----------
// Orders are marked tap-sell-…, so the tool won't buy the stock back for the same pick (broker.py).
// ---------- your own buys under the tool's sell rules (owner, Oct 30; manual_rules.py) ----------
// manual/state.json (written by the daily run): positions flagged to sell at the next open and why. Keep stores the
// flag's reason set in manual/keep.json; the next run leaves the position alone until a different reason comes up.
async function manualState(env) {
  const [st, keep] = await Promise.all([r2json(env, "manual/state.json"), r2json(env, "manual/keep.json")]);
  return json({ pending: st.pending || {}, keep, ruled: st.ruled || [], updated: st.updated || null });
}
async function manualKeep(req, env) {
  let body = {};
  try { body = await req.json(); } catch (e) {}
  const t = String(body.t || "").toUpperCase();
  if (!/^[A-Z][A-Z0-9-]{0,6}$/.test(t)) return json({ ok: false, why: "bad-request" }, 400);
  const sym = t.replace(/-/g, ".");
  const st = await r2json(env, "manual/state.json"), p = (st.pending || {})[sym];
  if (!p) return json({ ok: false, why: "not-flagged" }, 400);
  const keep = await r2json(env, "manual/keep.json");
  keep[sym] = { key: p.key, at: new Date().toISOString() };
  await env.DATA.put("manual/keep.json", JSON.stringify(keep));
  return json({ ok: true });
}

async function sell(req, env) {
  const a = alpaca(env);
  if (!a.ready) return json({ ok: false, why: "no-keys" }, 503);
  let body = {};
  try { body = await req.json(); } catch (e) {}
  const t = String(body.t || "").toUpperCase(), mode = String(body.mode || "all"), amount = +body.amount;
  if (!/^[A-Z][A-Z0-9-]{0,6}$/.test(t) || !["all", "shares", "dollars"].includes(mode)) return json({ ok: false, why: "bad-request" }, 400);
  const sym = t.replace(/-/g, ".");
  const [pos, asset, clock] = await Promise.all([a.get(`/v2/positions/${encodeURIComponent(sym)}`), a.get(`/v2/assets/${encodeURIComponent(sym)}`), a.get("/v2/clock")]);
  if (pos.status === 404) return json({ ok: false, why: "not-held" }, 400);
  if (!pos.ok || !asset.ok) { await wProblem(env, "sell", `Sell button couldn't read the ${t} position from Alpaca (HTTP ${pos.status})`); return json({ ok: false, why: "alpaca" }, 502); }
  await wOk(env, "sell");
  const held = +pos.data.qty_available || +pos.data.qty || 0, px = +pos.data.current_price || 0, frac = !!asset.data.fractionable;
  if (!(held > 0)) return json({ ok: false, why: "already-selling" }, 400);
  let qty = null, notional = null;
  if (mode === "all") qty = held;
  else if (mode === "shares") {
    if (!(amount > 0)) return json({ ok: false, why: "bad-amount" }, 400);
    qty = Math.min(amount, held);
    if (!frac) qty = Math.floor(qty);
  } else {
    if (!(amount >= 1)) return json({ ok: false, why: "bad-amount" }, 400);
    if (px && amount >= held * px * 0.995) qty = held;            // about the whole position: sell it all
    else if (frac) notional = Math.round(amount * 100) / 100;
    else qty = px ? Math.floor(amount / px) : 0;
  }
  if (qty != null && !(qty > 0)) return json({ ok: false, why: "amount-too-small" }, 400);
  const order = { symbol: sym, side: "sell", type: "market", time_in_force: "day", client_order_id: `tap-sell-${t}-${Date.now()}`.slice(0, 48) };
  if (notional != null) order.notional = notional.toFixed(2); else order.qty = String(qty);
  const r = await a.post("/v2/orders", order);
  if (!r.ok) return json({ ok: false, why: "rejected-" + r.status, message: r.data && r.data.message }, 400);
  return json({ ok: true, status: r.data.status, qty: order.qty ? +order.qty : null, notional: order.notional ? +order.notional : null,
    all: order.qty != null && +order.qty >= held, value: px ? (order.qty ? +order.qty * px : +order.notional) : null,
    market_open: !!(clock.data && clock.data.is_open), next_open: clock.data && clock.data.next_open });
}

// ---------- About a company (owner, Oct 26): what it does in plain words, and its CEO ----------
// Summary: Wikipedia's opening paragraph (plain language) → Yahoo's business summary → the SEC's industry name.
// CEO: Yahoo's officer list (kept current) → Wikidata's current CEO (no end date). Every source that errors is
// reported to Data health; "this company has no Wikipedia page" is not an error. Cached 14 days (misses: 1 day).
const WIKI_UA = "CapitolCapital/1.0 (personal research app; contact via GitHub e36meister)";
const EXCH = ["Q13677", "Q82059", "Q846626", "Q2338019", "Q1477147"];   // NYSE, Nasdaq, NYSE American, NYSE Arca, Cboe
function firstSentences(t, n) {
  // sentence ends: ". " followed by a capital, so "Inc. is" and "U.S. company" don't split
  const parts = String(t || "").replace(/\s+/g, " ").trim().split(/(?<=[.!?])\s+(?=[A-Z"“(])/);
  let out = "";
  for (const p of parts.slice(0, n)) { if (out && out.length + p.length > 480) break; out += (out ? " " : "") + p; }
  return out;
}
// Wikidata's query service rate-limits bursts (HTTP 429, Oct 30 for ACN): wait as long as it asks (capped, since
// the phone is waiting on this answer) and try again; same for brief 5xx outages.
async function wikiGet(url, accept) {
  let r;
  for (let i = 0; i < 3; i++) {
    r = await fetch(url, { headers: { "User-Agent": WIKI_UA, Accept: accept } });
    if (r.status !== 429 && r.status < 500) return r;
    if (i < 2) await new Promise(ok => setTimeout(ok, Math.min(5, Math.max(1, parseInt(r.headers.get("Retry-After") || "", 10) || 2 * (i + 1))) * 1000));
  }
  return r;
}
async function aboutWiki(t) {
  const sym = t.replace(/-/g, ".");
  const q = `SELECT ?item ?itemLabel ?ceoLabel ?article WHERE {
    VALUES ?exch { ${EXCH.map(x => "wd:" + x).join(" ")} }
    ?item p:P414 ?ex . ?ex ps:P414 ?exch ; pq:P249 "${sym}" .
    OPTIONAL { ?item p:P169 ?cs . ?cs ps:P169 ?ceo . FILTER NOT EXISTS { ?cs pq:P582 ?end } }
    OPTIONAL { ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> }
    SERVICE wikibase:label { bd:serviceParam wikibase:language "en" } } LIMIT 10`;
  const r = await wikiGet("https://query.wikidata.org/sparql?format=json&query=" + encodeURIComponent(q), "application/sparql-results+json");
  if (!r.ok) throw new Error(`Wikidata HTTP ${r.status}`);
  const rows = ((await r.json()).results || {}).bindings || [];
  if (!rows.length) return null;
  const row = rows.find(x => x.article) || rows[0];
  const ceo = [...new Set(rows.filter(x => x.item.value === row.item.value && x.ceoLabel && !/^Q\d+$/.test(x.ceoLabel.value)).map(x => x.ceoLabel.value))];
  const out = { name: row.itemLabel && row.itemLabel.value, ceo: ceo.join(" and ") || null, wiki: row.article ? row.article.value : null };
  if (out.wiki) {
    const title = out.wiki.split("/wiki/")[1];
    const s = await wikiGet(`https://en.wikipedia.org/api/rest_v1/page/summary/${title}`, "application/json");
    if (!s.ok) throw new Error(`Wikipedia HTTP ${s.status}`);
    out.summary = firstSentences((await s.json()).extract, 3) || null;
  }
  return out;
}
async function aboutYahoo(t) {
  const c = await fetch("https://fc.yahoo.com/", { headers: { "User-Agent": "Mozilla/5.0" }, redirect: "manual" });
  const cookie = (c.headers.get("set-cookie") || "").split(";")[0];
  const cr = await fetch("https://query1.finance.yahoo.com/v1/test/getcrumb", { headers: { "User-Agent": "Mozilla/5.0", Cookie: cookie } });
  const crumb = cr.ok ? (await cr.text()).trim() : "";
  if (!crumb || crumb.includes("<")) throw new Error(`Yahoo sign-in step failed (HTTP ${cr.status})`);
  const r = await fetch(`https://query2.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(t)}?modules=assetProfile&crumb=${encodeURIComponent(crumb)}`,
    { headers: { "User-Agent": "Mozilla/5.0", Cookie: cookie } });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`Yahoo HTTP ${r.status}`);
  const ap = ((((await r.json()).quoteSummary || {}).result || [])[0] || {}).assetProfile;
  if (!ap) return null;
  const off = (ap.companyOfficers || []).find(o => /\b(CEO|chief executive)\b/i.test(o.title || ""));
  return { ceo: off ? String(off.name).replace(/^(Mr\.|Ms\.|Mrs\.|Dr\.)\s+/i, "") : null, summary: firstSentences(ap.longBusinessSummary, 3) || null,
           industry: ap.industry || null, site: ap.website || null };
}
async function aboutSec(env, t) {
  const cik = await secCik(env, t);
  if (!cik) return null;
  const r = await secFetch(env, `https://data.sec.gov/submissions/CIK${String(cik).padStart(10, "0")}.json`);
  if (!r.ok) throw new Error(`SEC HTTP ${r.status}`);
  const j = await r.json();
  return { name: j.name, industry: j.sicDescription || null, summary: j.description || null };
}
async function about(env, url) {
  const t = String(url.searchParams.get("t") || "").toUpperCase();
  if (!/^[A-Z][A-Z0-9-]{0,6}$/.test(t)) return json({ error: "bad-ticker" }, 400);
  const key = `cache/about/${t}.json`, cached = await r2json(env, key, null);
  // a result a broken source left partial is only kept a day, so the missing part is filled in soon after the source recovers
  if (cached && Date.now() - Date.parse(cached.at) < ((cached.summary || cached.ceo) && !cached.partial ? 14 : 1) * 864e5) return json(cached);
  const tryIt = async (src, label, fn) => { try { const v = await fn(); await wOk(env, src); return v; }
    catch (e) { await wProblem(env, src, `Company About: ${label} failed for ${t} (${String(e && e.message || e).slice(0, 80)})`); return { err: true }; } };
  const [w, y] = await Promise.all([tryIt("about-wiki", "Wikipedia/Wikidata", () => aboutWiki(t)), tryIt("about-yahoo", "Yahoo profile", () => aboutYahoo(t))]);
  const W = w && !w.err ? w : {}, Y = y && !y.err ? y : {};
  let S = {};
  if (!W.summary && !Y.summary) { const s = await tryIt("about-sec", "SEC company record", () => aboutSec(env, t)); S = s && !s.err ? s : {}; }
  const out = { t, at: new Date().toISOString(), name: W.name || S.name || null,
    summary: W.summary || Y.summary || S.summary || null, from: W.summary ? "Wikipedia" : Y.summary ? "Yahoo Finance" : S.summary ? "SEC" : null,
    ceo: Y.ceo || W.ceo || null, ceo_from: Y.ceo ? "Yahoo Finance" : W.ceo ? "Wikidata" : null,
    industry: Y.industry || S.industry || null, wiki: W.wiki || null, site: Y.site || null,
    partial: !!((w && w.err) || (y && y.err)) };
  if (!out.partial || out.summary) await env.DATA.put(key, JSON.stringify(out));   // don't cache a result a broken source left thin
  return json(out);
}

// ---------- a trade's context: the company's SEC filings around the trade date (8-K items, big holders, insiders) ----------
const ITEMS = { "1.01": "material agreement", "1.02": "agreement ended", "1.03": "bankruptcy", "2.01": "acquisition or sale completed",
  "2.02": "earnings results", "2.03": "new debt", "2.05": "restructuring", "2.06": "impairment", "3.01": "listing notice",
  "3.02": "share sale", "4.01": "auditor change", "4.02": "restatement", "5.01": "change in control", "5.02": "executive or director change",
  "5.03": "bylaws change", "5.07": "shareholder vote", "7.01": "investor disclosure", "8.01": "other event" };
async function secFetch(env, u) {
  return fetch(u, { headers: { "User-Agent": env.SEC_USER_AGENT || "Capitol Capital research app", Accept: "application/json" } });
}
async function secCik(env, t) {
  let m = await r2json(env, "cache/sec_tickers.json", null);
  if (!m || !m.at || Date.now() - Date.parse(m.at) > 7 * 864e5) {
    const r = await secFetch(env, "https://www.sec.gov/files/company_tickers.json");
    if (!r.ok) await wProblem(env, "sec-tickers", `SEC company list didn't load (HTTP ${r.status}); trade Context uses the copy from ${m && m.at ? m.at.slice(0, 10) : "never"}`);
    else await wOk(env, "sec-tickers");
    if (r.ok) {
      const js = await r.json(), map = {};
      for (const v of Object.values(js)) map[String(v.ticker).toUpperCase().replace(/\./g, "-")] = v.cik_str;
      m = { at: new Date().toISOString(), map };
      await env.DATA.put("cache/sec_tickers.json", JSON.stringify(m));
    }
  }
  return m ? m.map[t] : null;
}
async function tradeContext(env, url) {
  const t = String(url.searchParams.get("t") || "").toUpperCase(), d = String(url.searchParams.get("d") || "");
  if (!/^[A-Z][A-Z0-9-]{0,7}$/.test(t) || !/^\d{4}-\d{2}-\d{2}$/.test(d)) return json({ error: "bad-request" }, 400);
  const cik = await secCik(env, t);
  if (!cik) return json({ t, d, filings: [], note: "No SEC filer found for this ticker (it may have been renamed or delisted)." });
  const td = Date.parse(d + "T12:00:00Z"), lo = new Date(td - 45 * 864e5).toISOString().slice(0, 10), hi = new Date(td + 60 * 864e5).toISOString().slice(0, 10);
  const pad = String(cik).padStart(10, "0");
  const r = await secFetch(env, `https://data.sec.gov/submissions/CIK${pad}.json`);
  if (!r.ok) {
    await wProblem(env, "sec-filings", `SEC filings for trade Context didn't load (HTTP ${r.status})`);
    return json({ t, d, filings: [], error: "sec", note: "The SEC didn't answer, so this trade's filings aren't shown. It's been logged in Data health; try again in a minute." });
  }
  await wOk(env, "sec-filings");
  const js = await r.json();
  const blocks = [js.filings?.recent || {}];
  let partial = false;
  // older trades: the filings list for that period is in a separate file
  const oldest = (blocks[0].filingDate || []).slice(-1)[0];
  if (oldest && oldest > lo) {
    for (const f of js.filings?.files || []) {
      if (f.filingFrom <= hi && f.filingTo >= lo) {
        const r2 = await secFetch(env, `https://data.sec.gov/submissions/${f.name}`);
        if (r2.ok) blocks.push(await r2.json());
        else { partial = true; await wProblem(env, "sec-older", `SEC older filings list didn't load (HTTP ${r2.status}); some trade Context lists were partial`); }
      }
    }
  }
  const out = [], ins = { before: 0, after: 0 };
  for (const b of blocks) {
    const n = (b.form || []).length;
    for (let i = 0; i < n; i++) {
      const fd = b.filingDate[i], form = b.form[i];
      if (fd < lo || fd > hi) continue;
      const off = Math.round((Date.parse(fd + "T12:00:00Z") - td) / 864e5);
      if (form === "4") { off < 0 ? ins.before++ : ins.after++; continue; }
      if (!/^(8-K|8-K\/A|SC 13D|SC 13G|SCHEDULE 13D|SCHEDULE 13G|SC 13D\/A|10-Q|10-K|S-1|S-3|424B\d?)$/.test(form)) continue;
      const items = String(b.items?.[i] || "").split(",").map(x => x.trim()).filter(x => x && x !== "9.01").map(x => ITEMS[x] || x);
      const acc = String(b.accessionNumber?.[i] || "").replace(/-/g, "");
      out.push({ d: fd, off, form, items, u: acc ? `https://www.sec.gov/Archives/edgar/data/${cik}/${acc}/${b.primaryDocument?.[i] || ""}` : "" });
    }
  }
  out.sort((a, b) => a.d.localeCompare(b.d));
  return json({ t, d, company: js.name, filings: out, insiders: ins, ...(partial ? { note: "Part of this company's older filings list didn't load; the list may be incomplete. Logged in Data health." } : {}) });
}

// ---------- Data health from the app's server side: failures are saved and alerted, never silently skipped ----------
const WHEALTH = "health/app.json";
// owner, Oct 27: only problems that break something alert right away (trading, the account, orders, the updates
// themselves); everything else waits for the data-health summary every 2 hours
const URGENT_SRC = new Set(["alpaca-live", "sell", "buy", "fills", "outbox", "quick", "daily", "senate-import"]);
async function wProblem(env, src, text) {
  try {
    const st = await r2json(env, WHEALTH, {}), now = new Date().toISOString();
    const cur = st[src], isNew = !cur || !cur.open;
    const recentFix = cur && cur.fixed && (Date.now() - Date.parse(cur.fixed)) < 24 * 3600e3;
    // owner, Oct 21: push only when it fails twice in a row (a one-off blip still shows on Data health);
    // records from before carry no `alerted` and were already alerted when they opened
    const n = ((cur && cur.open && cur.n) || 0) + 1;
    const alerted = isNew ? !!recentFix : (cur.alerted !== undefined ? cur.alerted : true);
    const push = !alerted && n >= 2;
    st[src] = { text, open: true, first: isNew ? now : cur.first, last: now, n, fixed: null, alerted: alerted || push };
    await env.DATA.put(WHEALTH, JSON.stringify(st));
    if (push) {
      const key = `push/outbox/${Date.now()}-${Math.floor(Math.random() * 1e5)}.json`;
      await env.DATA.put(key, JSON.stringify({ title: ("Data problem: " + text).slice(0, 120), body: text.slice(0, 600),
        tag: URGENT_SRC.has(src) ? "health-urgent" : "health", url: "/#health" }));
    }
  } catch (e) {}
}
async function health(env) {
  const list = await env.DATA.list({ prefix: "health/jobs/", limit: 100 });
  const jobs = {}, fixed = [];
  for (const o of list.objects || []) {
    const j = await r2json(env, o.key, null);
    if (!j) continue;
    const name = j.job || o.key.replace(/^health\/jobs\/|\.json$/g, "");
    jobs[name] = { at: j.at, open: j.open || [], minutes: j.minutes };
    for (const f of j.fixed || []) fixed.push({ ...f, job: name });
  }
  return json({ jobs: { jobs, fixed }, app: await r2json(env, WHEALTH, {}) });
}
async function wOk(env, src) {
  try {
    const st = await r2json(env, WHEALTH, null);
    if (st && st[src] && st[src].open) { st[src] = { ...st[src], open: false, fixed: new Date().toISOString() }; await env.DATA.put(WHEALTH, JSON.stringify(st)); }
  } catch (e) {}
}

// ---------- possible company people in posts (a first or last name alone): the owner says "it's them" or not ----------
const PEOPLE_FILE = "inputs/people_reviews.json", PEOPLE_QUEUE = "posts/review.json";
// Substack shows GitHub's servers a "Just a moment..." challenge (HTTP 403, since Oct 20), so the posts jobs can't read
// members' newsletters directly. Hourly, the Worker reads each feed (the list is saved by the posts jobs) and keeps a
// copy in R2 (feeds/substack/<name>.json), which the jobs read when the direct read is refused.
async function mirrorSubstack(env) {
  const subs = (await r2json(env, "feeds/substack/list.json", [])).filter(s => /^[a-z0-9-]{1,63}$/.test(s)).slice(0, 60);
  const bad = [];
  for (const s of subs) {
    try {
      // Oct 29: Substack started answering the Worker with HTTP 429 (too many requests) for some feeds. Wait as long
      // as it asks (up to 20 s) and try again; if the feed still won't load, read the newsletter's post list
      // (/api/v1/archive, a different Substack address) and keep it in the same RSS shape the posts jobs read.
      let r = await substackGet(`https://${s}.substack.com/feed`, "application/rss+xml, application/xml");
      let t = r.ok ? await r.text() : "", via = "feed", why = `HTTP ${r.status}`;
      if (!r.ok || !t.includes("<rss")) {
        const a = await substackGet(`https://${s}.substack.com/api/v1/archive?sort=new&limit=20`, "application/json");
        const posts = a.ok ? await a.json().catch(() => null) : null;
        if (Array.isArray(posts) && posts.length) { t = substackRss(posts); via = "archive"; }
        else { bad.push(`${s} (${why}; backup HTTP ${a.status})`); continue; }
      }
      await env.DATA.put(`feeds/substack/${s}.json`, JSON.stringify({ at: new Date().toISOString(), via, xml: t }), { httpMetadata: { contentType: "application/json" } });
    } catch (e) { bad.push(`${s} (${String(e && e.message || e).slice(0, 40)})`); }
  }
  if (bad.length) throw new Error(`${bad.length} of ${subs.length} feeds didn't load: ${bad.slice(0, 6).join(", ")}`);
}
async function substackGet(url, accept) {
  let r;
  for (let i = 0; i < 3; i++) {
    r = await fetch(url, { headers: { "User-Agent": "Mozilla/5.0 (compatible; CapitolCapital/1.0)", Accept: accept } });
    if (r.status !== 429 && r.status < 500) return r;
    const wait = Math.min(20, Math.max(2, parseInt(r.headers.get("Retry-After") || "", 10) || 5 * (i + 1)));
    await new Promise(ok => setTimeout(ok, wait * 1000));
  }
  return r;
}
function substackRss(posts) {
  const x = v => String(v || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const items = posts.filter(p => p && p.canonical_url && p.post_date).map(p =>
    `<item><title>${x(p.title)}</title><link>${x(p.canonical_url)}</link><pubDate>${new Date(p.post_date).toUTCString()}</pubDate>` +
    `<description>${x([p.subtitle || p.description, p.truncated_body_text].filter(Boolean).join(" "))}</description></item>`);
  return `<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>${items.join("")}</channel></rss>`;
}

async function r2json(env, key, dflt = {}) { try { const o = await env.DATA.get(key); if (o) return await o.json(); } catch (e) {} return dflt; }   // missing or unreadable → dflt ({} unless given)
async function peopleReviewList(env) {
  return json({ items: await r2json(env, PEOPLE_QUEUE, []), verdicts: await r2json(env, PEOPLE_FILE, {}) });
}
async function peopleReview(req, env) {
  let b = {};
  try { b = await req.json(); } catch (e) {}
  const key = String(b.key || "").toLowerCase(), verdict = String(b.verdict || "");
  if (!/^[a-z][a-z'\-]{1,30}\|[a-z0-9.\-]{1,12}$/.test(key)) return json({ ok: false, why: "bad-key" }, 400);
  if (!["yes", "no", "relative", "clear"].includes(verdict)) return json({ ok: false, why: "bad-verdict" }, 400);
  const REL = ["son", "daughter", "child", "brother", "sister", "sibling", "father", "mother", "parent", "in-law", "relative"];
  const rel = String(b.rel || "");
  if (verdict === "relative" && !REL.includes(rel)) return json({ ok: false, why: "bad-relation" }, 400);
  const all = await r2json(env, PEOPLE_FILE, {});
  if (verdict === "clear") delete all[key];
  else all[key] = { verdict, n: String(b.n || "").slice(0, 80), at: new Date().toISOString() };
  await env.DATA.put(PEOPLE_FILE, JSON.stringify(all), { httpMetadata: { contentType: "application/json" } });
  return json({ ok: true, review: all[key] || null });
}

// ---------- family-tie reviews (the owner says whether a name match is really the member's family) ----------
const TIE_FILE = "inputs/tie_reviews.json";
async function tieReviews(env) {
  try { const o = await env.DATA.get(TIE_FILE); if (o) return await o.json(); } catch (e) {}
  return {};
}
async function tieReview(req, env) {
  let b = {};
  try { b = await req.json(); } catch (e) {}
  const key = String(b.key || ""), verdict = String(b.verdict || "");
  if (!/^[A-Za-z0-9-]{1,20}\|[A-Za-z0-9.\-]{1,12}\|[A-Za-z0-9 .'\-]{1,60}$/.test(key)) return json({ ok: false, why: "bad-key" }, 400);
  if (!["yes", "no", "relative", "clear"].includes(verdict)) return json({ ok: false, why: "bad-verdict" }, 400);
  const REL = ["son", "daughter", "child", "brother", "sister", "sibling", "father", "mother", "parent", "in-law", "relative"];
  const rel = String(b.rel || "");
  if (verdict === "relative" && !REL.includes(rel)) return json({ ok: false, why: "bad-relation" }, 400);
  const all = await tieReviews(env);
  if (verdict === "clear") delete all[key];
  else all[key] = { verdict, ...(verdict === "relative" ? { rel } : {}), note: String(b.note || "").slice(0, 500), at: new Date().toISOString() };
  await env.DATA.put(TIE_FILE, JSON.stringify(all), { httpMetadata: { contentType: "application/json" } });
  return json({ ok: true, review: all[key] || null });
}

// ---------- alerts ----------
// Every alert is kept (newest 400) for the app's Alerts tab, then pushed to the phones. Tapping it opens `url`
// in the app: one post, one flagged trade, one position... An alert covering several things lists them as
// `items` and, without a url of its own, opens in the Alerts tab.
const LOG_KEY = "push/log.json";
async function alertLog(env) {
  try { const o = await env.DATA.get(LOG_KEY); const js = o ? await o.json() : []; return Array.isArray(js) ? js : []; } catch (e) { return []; }
}
// Clearing an alert in the app (swipe left, or Clear all) only hides it from the list; the log itself is kept.
const CLEARED = "push/cleared.json";
async function visibleAlerts(env) {
  const [log, c] = await Promise.all([alertLog(env), r2json(env, CLEARED, { ids: [], before: null })]);
  const ids = new Set(c.ids || []), before = c.before ? Date.parse(c.before) : 0;
  return log.filter(a => !ids.has(a.id) && !(Date.parse(a.at) <= before));
}
async function clearAlerts(req, env) {
  let b = {};
  try { b = await req.json(); } catch (e) {}
  const c = await r2json(env, CLEARED, { ids: [], before: null });
  if (b.all === true) { c.before = new Date().toISOString(); c.ids = []; }
  else if (typeof b.id === "string" && /^[a-z0-9]{4,24}$/.test(b.id)) {
    const ids = (c.ids || []).filter(x => x !== b.id); ids.push(b.id); c.ids = ids.slice(-500);
  } else return json({ ok: false, why: "bad-id" }, 400);
  await env.DATA.put(CLEARED, JSON.stringify(c), { httpMetadata: { contentType: "application/json" } });
  return json({ ok: true });
}

// ---------- Company search (More tab): any ticker or company name, then the same chart popup ----------
// charts/search_index.json (daily update): [[ticker, company, member trades]] for every stock members traded;
// the SEC's list adds every other listed company.
async function secNames(env) {
  let m = await r2json(env, "cache/sec_names.json", null);
  if (!m || !m.at || Date.now() - Date.parse(m.at) > 7 * 864e5) {
    const r = await secFetch(env, "https://www.sec.gov/files/company_tickers.json");
    if (r.ok) {
      const js = await r.json(), list = [];
      for (const v of Object.values(js)) list.push([String(v.ticker).toUpperCase().replace(/\./g, "-"), String(v.title || "")]);
      m = { at: new Date().toISOString(), list };
      await env.DATA.put("cache/sec_names.json", JSON.stringify(m));
      await wOk(env, "sec-names");
    } else await wProblem(env, "sec-names", `SEC company list didn't load for Company search (HTTP ${r.status})`);
  }
  return (m && m.list) || [];
}
async function tickerSearch(env, url) {
  const q = String(url.searchParams.get("q") || "").trim().toUpperCase().slice(0, 40);
  if (!q) return json({ items: [] });
  const [idx, sec] = await Promise.all([r2json(env, "charts/search_index.json", { list: [] }), secNames(env)]);
  const seen = new Map();
  for (const [t, co, n] of idx.list || []) seen.set(t, { t, co, n });
  for (const [t, co] of sec) if (!seen.has(t)) seen.set(t, { t, co, n: 0 });
  const score = x => {
    const co = String(x.co || "").toUpperCase();
    if (x.t === q) return 0;
    if (x.t.startsWith(q)) return 1;
    if (co.startsWith(q)) return 2;
    if (co.includes(" " + q) || co.includes(q)) return 3;
    return 9;
  };
  const hits = [...seen.values()].map(x => [score(x), x]).filter(([s]) => s < 9)
    .sort((a, b) => a[0] - b[0] || b[1].n - a[1].n || a[1].t.length - b[1].t.length).slice(0, 15).map(([, x]) => x);
  return json({ items: hits });
}
// One stock's chart in the same shape as ticker_charts.json: from that file when the stock is in it, otherwise
// 5 years of daily closes from Alpaca (adjusted for splits and dividends) plus members' trades of it.
// latest headlines about one stock (Alpaca's news feed), for the chart popup's "What's going on"
async function stockNews(env, url) {
  const t = String(url.searchParams.get("t") || "").toUpperCase().trim();
  if (!/^[A-Z][A-Z0-9-]{0,9}$/.test(t)) return json({ error: "bad-ticker" }, 400);
  const a = alpaca(env);
  if (!a.ready) return json({ error: "no-keys" }, 503);
  const start = new Date(Date.now() - 10 * 864e5).toISOString().slice(0, 19) + "Z";
  const r = await a.get(`https://data.alpaca.markets/v1beta1/news?symbols=${encodeURIComponent(t.replace(/-/g, "."))}&start=${start}&limit=12&sort=desc`);
  if (!r.ok) { await wProblem(env, "news", `Headlines for the chart popup didn't load from Alpaca (HTTP ${r.status})`); return json({ error: "news", status: r.status }, 502); }
  await wOk(env, "news");
  const junk = /(acquires new|increases holdings|raises holdings|stake in|shares (?:sold|bought|acquired) by|[\d,]+ shares of|interactive stock chart|stock price, news, quote)/i;
  const items = ((r.data && r.data.news) || []).filter(n => n.headline && !junk.test(n.headline))
    .sort((x, y) => (x.symbols || []).length - (y.symbols || []).length || String(y.created_at).localeCompare(String(x.created_at)))
    .slice(0, 6).sort((x, y) => String(y.created_at).localeCompare(String(x.created_at)))
    .map(n => ({ h: n.headline, src: n.source || "", u: n.url || "", at: n.created_at }));
  return json({ t, items });
}
async function oneChart(env, url) {
  const t = String(url.searchParams.get("t") || "").toUpperCase().trim();
  if (!/^[A-Z][A-Z0-9-]{0,9}$/.test(t)) return json({ error: "bad-ticker" }, 400);
  const tc = await r2json(env, "ticker_charts.json", null);
  if (tc && tc.px && tc.px[t]) {
    return json({ dates: tc.dates, px: { [t]: tc.px[t] }, trades: { [t]: (tc.trades || {})[t] || [] }, photos: tc.photos || {}, co: "" });
  }
  const a = alpaca(env);
  if (!a.ready) return json({ error: "no-keys" }, 503);
  const start = new Date(Date.now() - 5 * 365.25 * 864e5).toISOString().slice(0, 10);
  const end = new Date(Date.now() - 16 * 60e3).toISOString();
  const sym = t.replace(/-/g, ".");
  const bars = [];
  let tok = null, pages = 0;
  do {
    const u = `https://data.alpaca.markets/v2/stocks/bars?symbols=${encodeURIComponent(sym)}&timeframe=1Day&start=${start}&end=${encodeURIComponent(end)}&adjustment=all&feed=sip&limit=10000${tok ? "&page_token=" + encodeURIComponent(tok) : ""}`;
    const r = await a.get(u);
    if (r.status === 400 || r.status === 404 || r.status === 422) return json({ error: "no-prices" }, 404);    // not a ticker Alpaca knows
    if (!r.ok) { await wProblem(env, "chart", `Company search chart for ${t} didn't load from Alpaca (HTTP ${r.status})`); return json({ error: "prices", status: r.status }, 502); }
    for (const b of ((r.data && r.data.bars) || {})[sym] || []) bars.push(b);
    tok = r.data && r.data.next_page_token; pages++;
  } while (tok && pages < 5);
  await wOk(env, "chart");
  if (bars.length < 2) return json({ error: "no-prices" }, 404);
  const dates = bars.map(b => new Date(b.t).toLocaleDateString("en-CA", { timeZone: "America/New_York" }));
  const px = bars.map(b => Math.round(b.c * 1e4) / 1e4);
  const all = await r2json(env, "charts/trades_by_ticker.json", { trades: {} });
  const cols = all.cols || ["n", "b", "ty", "td", "d", "lo", "hi", "own"];
  const rows = ((all.trades || {})[t] || []).map(r => Object.fromEntries(cols.map((c, i) => [c, r[i]])));
  // the trade's price: that day's close (or the last one before it)
  for (const tr of rows) {
    let lo = 0, hi = dates.length - 1, k = -1;
    while (lo <= hi) { const m = (lo + hi) >> 1; if (dates[m] <= tr.td) { k = m; lo = m + 1; } else hi = m - 1; }
    tr.p = k >= 0 ? px[k] : null;
  }
  const idx = await r2json(env, "charts/search_index.json", { list: [] });
  let co = ((idx.list || []).find(x => x[0] === t) || [])[1] || "";
  if (!co) co = ((await secNames(env)).find(x => x[0] === t) || [])[1] || "";
  return json({ dates, px: { [t]: px }, trades: { [t]: rows }, photos: {}, co });
}

// ---------- Taxes (More tab): written by tax_report.py after each market close ----------
const taxMode = env => (env.TRADING_MODE || "paper").toLowerCase() === "live" ? "live" : "paper";
async function taxIndex(env) {
  const idx = await r2json(env, `tax/${taxMode(env)}/index.json`, null);
  return idx ? json(idx) : json({ error: "no-file" }, 404);
}
const TAX_FILES = { "statement.pdf": "application/pdf", "form8949.csv": "text/csv", "sales.json": "application/json" };
async function taxFile(env, url) {
  const y = String(url.searchParams.get("y") || ""), f = String(url.searchParams.get("f") || "");
  if (!/^20\d\d$/.test(y) || !TAX_FILES[f]) return json({ error: "bad-request" }, 400);
  const mode = taxMode(env);
  const o = await env.DATA.get(`tax/${mode}/${y}/${f}`);
  if (!o) return json({ error: "no-file" }, 404);
  const name = f === "sales.json" ? f : `capitol-capital-${mode}-${y}-${f.replace(".", "-").replace("-pdf", ".pdf").replace("-csv", ".csv")}`;
  return new Response(o.body, { headers: { "Content-Type": TAX_FILES[f], "Cache-Control": "private, no-store",
    ...(f === "sales.json" ? {} : { "Content-Disposition": `attachment; filename="${name}"` }) } });
}

// quiet: kept in the app's Alerts tab (the notification center) without a phone push (owner, Oct 28: per-member
// Instagram check-list lines)
async function deliver(env, msg, quiet = false) {
  const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  const items = (Array.isArray(msg.items) ? msg.items : []).slice(0, 20)
    .map(x => ({ l: String(x.l || "").slice(0, 160), u: String(x.u || "").slice(0, 200), x: x.x ? String(x.x).slice(0, 500) : undefined }))
    .filter(x => x.l);
  let url = msg.url && msg.url !== "/" ? msg.url : "";
  if (!url) url = items.length === 1 && items[0].u ? items[0].u : items.length > 1 ? "/#alerts/" + id : "/#alerts/" + id;
  const entry = { id, at: new Date().toISOString(), title: String(msg.title || "").slice(0, 120), body: String(msg.body || "").slice(0, 600),
                  kind: msg.tag || "", url, items };
  const log = await alertLog(env);
  log.unshift(entry);
  await env.DATA.put(LOG_KEY, JSON.stringify(log.slice(0, 400)), { httpMetadata: { contentType: "application/json" } });
  // the id rides along so opening the alert marks it read in the app
  if (quiet) return;
  const u = url.includes("#") ? url.replace("#", `?n=${id}#`) : url + `?n=${id}`;
  return sendAll(env, { title: entry.title, body: entry.body, tag: msg.tag || "", url: u });
}

const ET = () => new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
const money0 = v => "$" + Math.round(v).toLocaleString("en-US");

async function drainOutbox(env) {
  const list = await env.DATA.list({ prefix: "push/outbox/", limit: 50 });
  for (const o of list.objects) {
    try {
      const age = Date.now() - o.uploaded.getTime();
      if (age < 6 * 3600e3) {
        const msg = await (await env.DATA.get(o.key))?.json();
        if (msg && msg.title && msg.tag === "health") await holdForDigest(env, msg);
        else if (msg && msg.title && msg.tag === "health-quiet") await deliver(env, msg, true);
        else if (msg && msg.title) await deliver(env, msg);
      }
    } catch (e) {}
    await env.DATA.delete(o.key);
  }
}

// ---------- data-health summary (owner, Oct 27): routine data problems are held and sent together ----------
const DIGEST_KEY = "push/health_digest.json";
async function holdForDigest(env, msg) {
  const q = await r2json(env, DIGEST_KEY, []);
  const items = (msg.items && msg.items.length ? msg.items : [{ l: String(msg.body || msg.title).split("\n")[0].replace(/^•\s*/, ""), u: "/#health" }]);
  for (const it of items) q.push({ at: new Date().toISOString(), l: String(it.l || "").slice(0, 200) });
  await env.DATA.put(DIGEST_KEY, JSON.stringify(q.slice(-200)));
}
async function sendDigest(env, when) {
  const et = new Date(when.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const h = et.getHours();
  if (et.getMinutes() !== 0 || h % 2 !== 0 || h < 8 || h > 22) return;      // every 2 hours, 8 AM-10 PM
  const q = await r2json(env, DIGEST_KEY, []);
  if (!q.length) return;
  const uniq = [...new Map(q.map(x => [x.l, x])).values()];
  await deliver(env, { title: `Data health: ${uniq.length} new problem${uniq.length === 1 ? "" : "s"} (nothing broken)`,
    body: uniq.slice(0, 6).map(x => "• " + x.l.slice(0, 150)).join("\n") + (uniq.length > 6 ? `\n…and ${uniq.length - 6} more` : ""),
    tag: "health-digest", url: "", items: uniq.slice(0, 20).map(x => ({ l: x.l, u: "/#health" })) });
  await env.DATA.put(DIGEST_KEY, "[]");
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
    if (o.side === "buy" && cid.startsWith("tap-")) msgs.push({ t, s: `bought ${t}`, title: `Bought ${t}`, body: `Your Buy-button order filled: ${sh}.` });
    else if (o.side === "buy" && cid.startsWith("cs-")) msgs.push({ t, s: `bought ${t}`, title: `Tool bought ${t}`, body: sh + (w ? ` · ${w}` : "") + "." });
    else if (o.side === "sell" && cid.startsWith("tap-sell-")) msgs.push({ t, s: `sold ${t}`, title: `Sold ${t}`, body: `Your Sell-button order filled: ${sh}.` });
    else if (o.side === "sell") msgs.push({ t, s: `sold ${t}`, title: `Sold ${t}`, body: sh + (w ? ` · ${w}` : "") + "." });
  }
  await env.DATA.put("push/fills_state.json", JSON.stringify({ seen: [...seen].slice(-1500) }));
  if (msgs.length > 3) {
    await deliver(env, { title: `${msgs.length} trades filled`, body: msgs.map(m => m.s).join(", ").replace(/^./, c => c.toUpperCase()) + ".", tag: "fills",
                         url: "", items: msgs.map(m => ({ l: `${m.title}: ${m.body}`, u: `/#port/${m.t}` })) });
  } else {
    for (const m of msgs) await deliver(env, { title: m.title, body: m.body, tag: "fill-" + m.title, url: `/#port/${m.t}` });
  }
}

// ---------- pre-market and after-hours trading (owner, Oct 29) ----------
// Alpaca only fills orders outside regular hours if they're limit orders marked for extended hours. So, every
// 3 minutes outside regular hours, our waiting market orders (Buy button "tap-", Sell button "tap-sell-", the tool's
// buys "cs-", but not the parked-cash fund "cs-park-") are swapped for extended-hours limit orders: buys at up to
// 0.5% above the last trade, sells at up to 0.5% below, re-priced as the price moves. After-hours (4-8 PM) orders
// expire at 8 PM; unfilled ones are placed again at 4 AM. Anything still unfilled at the 9:30 open becomes a market
// order. The swapped order keeps its prefix, so fills, alerts and the tool's bookkeeping treat it the same.
const EXT_KEY = "ext/intents.json", EXT_PAD = 0.005, EXT_REPRICE_MIN = 15;
const extOurs = cid => /^(tap-|cs-)/.test(cid || "") && !(cid || "").startsWith(PARK_PREFIX);
function extCid(cid, tag) {          // same prefix and hold suffix ("-h60"), a new unique id
  const m = String(cid).match(/^(.*?)(-h\d+)?$/), stamp = tag + Date.now().toString(36).slice(-5);
  const base = m[1].slice(0, 48 - stamp.length - (m[2] || "").length - 1);
  return `${base}-${stamp}${m[2] || ""}`.slice(0, 48);
}
async function extLast(a, sym) {
  const r = await a.get(`https://data.alpaca.markets/v2/stocks/${encodeURIComponent(sym)}/snapshot?feed=iex`);
  const d = r.ok ? r.data || {} : {};
  return +(d.latestTrade && d.latestTrade.p) || +(d.minuteBar && d.minuteBar.c) || +(d.dailyBar && d.dailyBar.c) || +(d.prevDailyBar && d.prevDailyBar.c) || null;
}
const extPx = (p, side) => { const v = side === "buy" ? p * (1 + EXT_PAD) : p * (1 - EXT_PAD); return v >= 1 ? Math.round(v * 100) / 100 : Math.round(v * 1e4) / 1e4; };
async function extendedHours(env, when) {
  const a = alpaca(env);
  if (!a.ready) return;
  const et = new Date(when.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const hm = et.getHours() * 60 + et.getMinutes();
  const st = await r2json(env, EXT_KEY, { intents: {} });
  const I = st.intents || {};
  const clock = await a.get("/v2/clock");
  if (!clock.ok) throw new Error(`Alpaca clock didn't answer (HTTP ${clock.status})`);
  const open = !!clock.data.is_open;
  // a trading day: the next open is today (before the open) or the market opened today (after the close)
  const today = et.toLocaleDateString("en-CA");
  const nextOpenDay = new Date(clock.data.next_open).toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  let tradingDay = nextOpenDay === today;
  if (!tradingDay && hm >= 16 * 60) {
    const cal = await a.get(`/v2/calendar?start=${today}&end=${today}`);
    tradingDay = cal.ok && Array.isArray(cal.data) && cal.data.length > 0;
  }
  const session = open ? "open" : !tradingDay ? "none" : hm >= 4 * 60 && hm < 9 * 60 + 30 ? "pre" : hm >= 16 * 60 && hm < 20 * 60 ? "after" : "none";
  if (session === "none" && !Object.keys(I).length) return;
  const ords = await a.get("/v2/orders?status=open&limit=200&nested=false");
  if (!ords.ok) throw new Error(`Alpaca orders didn't answer (HTTP ${ords.status})`);
  const live = {};
  for (const o of ords.data || []) live[o.id] = o;
  let changed = false;
  const cancel = async id => {          // cancel and confirm; a replacement never goes in while the old order can still fill
    const r = await a.del(`/v2/orders/${id}`);
    for (let k = 0; k < 4; k++) {
      const o = (await a.get(`/v2/orders/${id}`)).data || {};
      if (["canceled", "expired", "rejected", "replaced"].includes(o.status)) return true;
      if (o.status === "filled") return false;
      await new Promise(res => setTimeout(res, 700));
    }
    throw new Error(`couldn't cancel order ${id} (HTTP ${r.status}: ${String(r.data && r.data.message || "").slice(0, 80)}); nothing new was placed`);
  };
  // safety net: a waiting market order of ours whose extended-hours replacement already exists is a duplicate
  const recent = await a.get(`/v2/orders?status=all&limit=200&direction=desc&after=${encodeURIComponent(new Date(Date.now() - 3 * 864e5).toISOString())}`);
  const extOf = {};
  for (const o of recent.data || []) {
    const m = String(o.client_order_id || "").match(/^(.*)-[xm][0-9a-z]{5}(-h\d+)?$/);
    if (m && ["filled", "partially_filled", "new", "accepted", "pending_new"].includes(o.status)) extOf[m[1] + (m[2] || "")] = o;
  }
  const dupes = [];
  for (const o of Object.values(live)) {
    if (o.type === "market" && !o.extended_hours && extOurs(o.client_order_id) && extOf[o.client_order_id]) {
      try { await cancel(o.id); dupes.push(`${o.symbol} (${o.client_order_id.split("-")[0]})`); delete live[o.id];
            for (const [k, it] of Object.entries(I)) if (it.oid === o.id) delete I[k]; changed = true; }
      catch (e) { throw new Error(`a duplicate ${o.side} order for ${o.symbol} is still waiting and couldn't be cancelled: ${e.message}`); }
    }
  }
  if (dupes.length) await deliver(env, { title: "Cancelled a duplicate order", body: `A waiting order for ${dupes.join(", ")} was a duplicate of one that already went in before the open, so it was cancelled.`, tag: "fills", url: "/#port" });
  const place = async (it, extended, px) => {
    const body = { symbol: it.sym, side: it.side, type: extended ? "limit" : "market", time_in_force: "day",
                   client_order_id: extCid(it.cid, extended ? "x" : "m") };
    if (extended) { body.limit_price = String(px); body.extended_hours = true; }
    if (it.notional != null) body.notional = Math.max(1, it.notional).toFixed(2); else body.qty = String(it.qty);
    let r = await a.post("/v2/orders", body);
    if (!r.ok && extended && it.notional != null && r.status === 422) {          // no fractional orders out of hours for this stock: whole shares
      const q = Math.floor(it.notional / px);
      if (q >= 1) { delete body.notional; body.qty = String(q); r = await a.post("/v2/orders", body); }
    }
    if (!r.ok) throw new Error(`${it.side} ${it.sym}: Alpaca refused the ${extended ? "extended-hours" : "market"} order (HTTP ${r.status}: ${String(r.data && r.data.message || "").slice(0, 80)})`);
    it.oid = r.data.id; it.px = extended ? px : null; it.at = new Date().toISOString(); it.ext = extended;
    changed = true;
  };
  // 1) new waiting market orders of ours -> intents (only outside regular hours)
  if (session !== "open") {
    for (const o of Object.values(live)) {
      if (o.type !== "market" || o.extended_hours || !extOurs(o.client_order_id) || Object.values(I).some(x => x.oid === o.id)) continue;
      if (!["buy", "sell"].includes(o.side)) continue;
      const key = o.client_order_id;
      I[key] = { cid: key, sym: o.symbol, side: o.side, notional: o.notional != null ? +o.notional : null, qty: o.qty != null ? +o.qty : null,
                 oid: o.id, ext: false, created: new Date().toISOString() };
      changed = true;
    }
  }
  const failures = [];
  for (const [key, it] of Object.entries(I)) {
    try {
      const o = it.oid ? (live[it.oid] || (await a.get(`/v2/orders/${it.oid}`)).data) : null;
      const filled = o ? +o.filled_qty || 0 : 0;
      if (o && o.status === "filled") { delete I[key]; changed = true; continue; }
      if (o && filled > 0) {                                       // partly filled: carry on with what's left
        if (it.notional != null) it.notional = Math.max(0, it.notional - filled * (+o.filled_avg_price || 0)); else it.qty = Math.max(0, (+o.qty || it.qty) - filled);
      }
      const isLive = o && ["new", "accepted", "pending_new", "partially_filled", "held", "accepted_for_bidding", "calculated"].includes(o.status);
      if ((it.notional != null && it.notional < 1) || (it.qty != null && it.qty <= 0)) { if (isLive) await cancel(o.id); delete I[key]; changed = true; continue; }
      if (o && ["canceled", "rejected"].includes(o.status)) { delete I[key]; changed = true; continue; }   // cancelled by you or the tool: drop it (an 8 PM expiry is "expired")
      if (session === "open") {
        if (isLive && it.ext) { if (await cancel(o.id)) await place(it, false); }
        else if (!isLive) await place(it, false);
        delete I[key]; changed = true; continue;                    // a market order now: the open takes it from here
      }
      if (session === "none") continue;                             // 8 PM-4 AM and holidays: wait for the next session
      const last = await extLast(a, it.sym);
      if (!last) { failures.push(`${it.sym}: no last price`); continue; }
      const px = extPx(last, it.side);
      if (isLive && !it.ext) { if (await cancel(o.id)) await place(it, true, px); else { delete I[key]; changed = true; } continue; }
      if (!isLive) { await place(it, true, px); continue; }        // expired at 8 PM, or the first time this session
      const age = (Date.now() - Date.parse(it.at || 0)) / 60000;
      if (age >= EXT_REPRICE_MIN && Math.abs(px - (it.px || 0)) / px > 0.001) {
        const r = await a.patch(`/v2/orders/${o.id}`, { limit_price: String(px) });
        if (r.ok) { it.oid = r.data.id; it.px = px; it.at = new Date().toISOString(); changed = true; }
      }
    } catch (e) { failures.push(String(e && e.message || e).slice(0, 160)); }
  }
  if (changed) { st.intents = I; st.updated = new Date().toISOString(); await env.DATA.put(EXT_KEY, JSON.stringify(st)); }
  if (failures.length) throw new Error(failures.join("; "));
}

// ---------- fast filing check ----------
// GitHub's own 15-minute schedule often runs 15-40 minutes late. This starts the quick check (new House and Senate
// filings, Buy orders, portfolio) on time: every 5 minutes on weekdays 6 AM-9 PM Eastern, every 30 minutes otherwise.
// A new filing then starts the full update within a few minutes of being posted.
// The full update starts exactly on time from here (GitHub's own schedule runs 10-25 minutes late and is only a
// fallback that skips itself when this one already started): 10:43 and 23:43 UTC.
async function dispatch(env, wf, inputs) {
  if (!env.GH_DISPATCH_TOKEN) throw new Error("the GitHub start key (GH_DISPATCH_TOKEN) isn't set");
  {
    const r = await fetch(`https://api.github.com/repos/${env.GH_REPO || "e36meister/congress-signals"}/actions/workflows/${wf}/dispatches`, {
      method: "POST",
      headers: { Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`, Accept: "application/vnd.github+json",
                 "User-Agent": "capitol-capital-worker", "Content-Type": "application/json" },
      body: JSON.stringify({ ref: "main", inputs }),
    });
    if (!r.ok) throw new Error(`GitHub refused to start the job (HTTP ${r.status}): ${(await r.text()).slice(0, 120)}`);
  }
}

async function startDailyOnTime(env, now) {
  if (!env.GH_DISPATCH_TOKEN) throw new Error("the GitHub start key (GH_DISPATCH_TOKEN) isn't set");
  const h = now.getUTCHours(), m = now.getUTCMinutes();
  if (!((h === 10 || h === 23) && m === 43)) return;
  {
    const r = await fetch(`https://api.github.com/repos/${env.GH_REPO || "e36meister/congress-signals"}/actions/workflows/daily.yml/dispatches`, {
      method: "POST",
      headers: { Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`, Accept: "application/vnd.github+json",
                 "User-Agent": "capitol-capital-worker", "Content-Type": "application/json" },
      body: JSON.stringify({ ref: "main", inputs: { chain: "0", why: "scheduled" } }),
    });
    if (!r.ok) throw new Error(`GitHub refused to start the job (HTTP ${r.status}): ${(await r.text()).slice(0, 120)}`);
  }
}

async function startQuickCheck(env, min) {
  if (!env.GH_DISPATCH_TOKEN) throw new Error("the GitHub start key (GH_DISPATCH_TOKEN) isn't set");
  const et = ET(), h = et.getHours(), wd = et.getDay();
  const busy = wd >= 1 && wd <= 5 && h >= 6 && h < 21;
  if (min % (busy ? 5 : 30) !== 0) return;
  {
    const r = await fetch(`https://api.github.com/repos/${env.GH_REPO || "e36meister/congress-signals"}/actions/workflows/quick.yml/dispatches`, {
      method: "POST",
      headers: { Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`, Accept: "application/vnd.github+json",
                 "User-Agent": "capitol-capital-worker", "Content-Type": "application/json" },
      body: JSON.stringify({ ref: "main" }),
    });
    if (!r.ok) throw new Error(`GitHub refused to start the job (HTTP ${r.status}): ${(await r.text()).slice(0, 120)}`);
  }
}

// ---------- Senate filings sent from Safari by the iPhone Shortcut (owner, Oct 27) ----------
// The Senate site blocks automated reads (since Oct 23), but it opens normally for the owner. The Shortcut sends the
// filing page the owner has open; this checks it is a Senate trade report, saves it in R2 for the next full update
// (which reads the trades with the same parser as before and adds them as official Senate filings) and starts that
// update right away. The Shortcut uses its own key (not the login), which can only send filings.
async function importKey(env, renew) {
  const o = renew ? null : await env.DATA.get("senate_import/key.txt");
  if (o) return (await o.text()).trim();
  const k = b64url(crypto.getRandomValues(new Uint8Array(24)));
  await env.DATA.put("senate_import/key.txt", k);
  return k;
}
async function importLog(env) { return (await r2json(env, "senate_import/log.json", [])).slice(0, 15); }
const txt = (s, status = 200) => new Response(s, { status, headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" } });
const deTag = h => h.replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/&amp;/g, "&").replace(/&#39;|&#x27;/g, "'").replace(/\s+/g, " ").trim();
async function senateImport(req, env) {
  const given = req.headers.get("X-Import-Key") || "";
  const key = await importKey(env, false);
  if (!given || !timingSafeEqual(given, key))
    return txt("Not sent: the Shortcut's key doesn't match. Open Capitol Capital → More → Senate import and copy the key again.", 401);
  let b = {};
  try { b = JSON.parse(await req.text()); } catch (e) { return txt("Not sent: the Shortcut didn't send the page. Check the 'Run JavaScript' step.", 400); }
  const page = String(b.url || ""), html = String(b.html || "");
  const m = page.match(/^https:\/\/efdsearch\.senate\.gov(\/search\/view\/(ptr|paper)\/[A-Za-z0-9-]+\/?)/);
  if (!m) return txt("Not sent: this isn't a Senate filing page. Open the filing itself on efdsearch.senate.gov (the 'Periodic Transaction Report' link), then run the Shortcut.", 400);
  if (html.length > 3e6) return txt("Not sent: the page is unusually large.", 400);
  const link = m[1].endsWith("/") ? m[1] : m[1] + "/";
  if (m[2] === "paper") return txt("This is a scanned paper filing. The app can't read scanned filings from the Shortcut yet; nothing was saved.", 400);
  if (!/Transaction Date/i.test(html) || !/<table/i.test(html)) return txt("Not sent: no trade table on this page. If the page shows a Senate agreement or 'Access Denied', tap through to the filing and try again.", 400);
  // the filing page says "The Honorable Jane Doe (Doe, Jane)"; its Print view says "Mr. David H McCormick (McCormick, David H.)"
  const head = deTag((html.match(/<h2[^>]*>[\s\S]{0,300}?<\/h2>/i) || [""])[0]);
  const who = head.replace(/^(The Honorable|Hon\.?|Mr\.?|Mrs\.?|Ms\.?|Miss|Dr\.?)\s*/i, "").replace(/\s*\(.*$/, "").trim() || "the senator";
  const filed = (deTag(html).match(/Filed\s+(\d{1,2}\/\d{1,2}\/\d{4})/i) || [])[1] || null;
  const body = (html.match(/<tbody[\s\S]*?<\/tbody>/i) || [""])[0];
  const n = (body.match(/<tr[\s>]/gi) || []).length;
  const id = link.split("/").filter(Boolean).pop();
  await env.DATA.put(`senate_import/pending/${id}.json`, JSON.stringify({ link, html, who, filed, at: new Date().toISOString() }));
  const log = await r2json(env, "senate_import/log.json", []);
  log.unshift({ id, who, filed, rows: n, at: new Date().toISOString(), status: "received" });
  await env.DATA.put("senate_import/log.json", JSON.stringify(log.slice(0, 100)));
  let started = "";
  try { await dispatch(env, "daily.yml", { chain: "0", why: "senate import" }); started = " The full update has started; you'll get an alert when they're added (usually within an hour)."; }
  catch (e) { await wProblem(env, "senate-import", `Senate import: couldn't start the full update (${String(e && e.message || e).slice(0, 100)})`); started = " They'll be added at the next scheduled update."; }
  return txt(`Got it: ${n} row${n === 1 ? "" : "s"} from ${who}${filed ? ", filed " + filed : ""}.${started}`);
}
// reminders to look for new Senate filings (owner, Oct 27): every day at 8 AM, 1 PM and 5 PM Eastern
async function senateReminder(env, when) {
  const et = new Date(when.toLocaleString("en-US", { timeZone: "America/New_York" }));
  if (![8, 13, 17].includes(et.getHours()) || et.getMinutes() !== 0) return;
  const log = await importLog(env);
  const last = log.find(h => h.filed);
  await deliver(env, { title: "Check for new Senate filings",
    body: `Periodic Transactions, newest first.${last ? ` Last one you sent was filed ${last.filed}.` : ""} Open each new one → Share → Capitol Capital Senate.`,
    tag: "senate-reminder-" + et.getHours(), url: "https://efdsearch.senate.gov/search/" });
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
  out.day_label = dayLabel(out.market_open);
  const s = cr.series || {};
  if ((s.account || []).length) {
    out.since = { date: cr.date, acct: cr.since_start, spy: cr.spy_since_start,
      gap: cr.since_start != null && cr.spy_since_start != null ? cr.since_start - cr.spy_since_start : null };
    out.spark = { acct: s.account.slice(-60), spy: (s.spy || []).slice(-60) };
  }
  const park = new Set(PARK_CHOICES), seen = new Set();
  out.buys = (port.orders || []).filter(x => x.side === "buy" && (x.by === "tool" || x.by === "tap") && x.status === "filled" && !park.has(x.t))
    .filter(x => !seen.has(x.t) && seen.add(x.t)).slice(0, 4).map(x => ({ t: x.t, at: x.at, by: x.by }));
  return json(out);
}

async function widgetScript(env, url, renew) {
  const key = await widgetKey(env, renew);
  const js = WIDGET_JS.replace("__URL__", url.origin).replace("__KEY__", key);
  return new Response(js, { headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" } });
}

const WIDGET_JS = `// Capitol Capital widget for Scriptable (medium size). Read-only: it can't trade.
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
const w = new ListWidget(); w.backgroundColor = BG; w.url = URL_; w.setPadding(12, 14, 12, 14);
w.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
if (!d) { const e = w.addText("Capitol Capital: can't reach the app yet"); e.textColor = INK; e.font = Font.systemFont(13); }
else {
  const top = w.addStack(); top.centerAlignContent();
  const left = top.addStack(); left.layoutVertically();
  const t = left.addText("CAPITOL CAPITAL" + (d.stale ? " · offline" : "")); t.font = Font.semiboldMonospacedSystemFont(9); t.textColor = GREEN;
  left.addSpacer(2);
  const eq = left.addText(money(d.equity)); eq.font = Font.boldMonospacedSystemFont(24); eq.textColor = INK; eq.minimumScaleFactor = 0.6; eq.lineLimit = 1;
  const dy = left.addText((d.day_pl > 0 ? "+" : "") + money(d.day_pl) + " · " + pct(d.day_pct) + " " + (d.day_label || "today")); dy.font = Font.mediumMonospacedSystemFont(11); dy.textColor = col(d.day_pl); dy.lineLimit = 1; dy.minimumScaleFactor = 0.7;
  top.addSpacer(8);
  if (d.spark) { const img = top.addImage(spark(d.spark.acct, d.spark.spy, 120, 58)); img.imageSize = new Size(120, 58); }
  w.addSpacer(6);
  if (d.since) {
    const s = w.addStack(); s.centerAlignContent();
    const a = s.addText("You " + pct(d.since.acct)); a.font = Font.mediumMonospacedSystemFont(11); a.textColor = GREEN;
    s.addSpacer(8);
    const b = s.addText("S&P " + pct(d.since.spy)); b.font = Font.mediumMonospacedSystemFont(11); b.textColor = DIM;
    s.addSpacer();
    const g = s.addText((d.since.gap >= 0 ? "ahead " : "behind ") + Math.abs(d.since.gap ?? 0).toFixed(2)); g.font = Font.mediumMonospacedSystemFont(11); g.textColor = col(d.since.gap);
  }
  w.addSpacer();
  const nb = w.addStack(); nb.centerAlignContent();
  const lab = nb.addText("NEW BUYS  "); lab.font = Font.semiboldMonospacedSystemFont(9); lab.textColor = DIM;
  const l = nb.addText((d.buys || []).map(b => b.t).join("  ") || "none yet"); l.font = Font.boldMonospacedSystemFont(13); l.textColor = INK; l.lineLimit = 1; l.minimumScaleFactor = 0.7;
  nb.addSpacer();
  const mk = nb.addText(d.market_open ? "open" : "closed"); mk.font = Font.systemFont(9); mk.textColor = DIM;
}
if (config.runsInWidget) Script.setWidget(w); else await w.presentMedium();
Script.complete();
`;

// Alpaca's "today" = the account now vs the last trading day's 4 PM close. On weekends, holidays and before the
// open that's only after-hours price moves, so the widget says so (same rule as the app's Portfolio tab).
function dayLabel(isOpen) {
  if (isOpen) return "today";
  const f = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short", hour: "numeric", minute: "numeric", hour12: false });
  const parts = Object.fromEntries(f.formatToParts(new Date()).map(x => [x.type, x.value]));
  const hm = (+parts.hour % 24) * 60 + +parts.minute;
  return (["Sat", "Sun"].includes(parts.weekday) || hm < 960) ? "after hours" : "today";
}
