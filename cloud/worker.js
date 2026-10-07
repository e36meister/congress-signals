// Capitol Capital on Cloudflare: serves the app, checks the passcode login, hands out the dashboard data
// from private R2 storage, reads live numbers from Alpaca and places Buy-button orders.
// Nothing under /api answers without a valid login cookie (except the login itself).

const COOKIE = "cc_s";
const SESSION_DAYS = 180;
const MAX_FAILS = 10;            // wrong passcodes allowed per hour before locking
const PREFIX = "cs-", PARK_PREFIX = "cs-park-", PARK_CHOICES = ["SPY", "VOO", "IVV"];
const enc = new TextEncoder();

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
  if (!(await validSession(req, env))) return json({ error: "login" }, 401);

  if (p === "/api/session") return json({ ok: true, mode: (env.TRADING_MODE || "paper") });
  if (p === "/api/data" && req.method === "GET") return fromR2(req, env, "dashboard_data.json");
  if (p === "/api/charts" && req.method === "GET") return fromR2(req, env, "ticker_charts.json");
  if (p === "/api/live" && req.method === "GET") return live(env);
  if (p === "/api/buy" && req.method === "POST") return buy(req, env);
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
