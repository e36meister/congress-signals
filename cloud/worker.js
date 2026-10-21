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
    if (!url.pathname.startsWith("/api/")) return env.ASSETS.fetch(req);
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
    await step("quick", "Starting the filings check", () => startQuickCheck(env, min));
    await step("daily", "Starting the daily update", () => startDailyOnTime(env, new Date(ev.scheduledTime)));
    if (min === 5) await step("substack", "Reading members' Substack feeds", () => mirrorSubstack(env));
    if (min === 7) await step("posts", "Starting the hourly posts job", () => dispatch(env, "posts.yml", {}));
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
  if (p === "/api/members" && req.method === "GET") return fromR2(req, env, "members/index.json");
  if (p === "/api/posts" && req.method === "GET") return fromR2(req, env, "posts/feed.json");
  if (p === "/api/member" && req.method === "GET") {
    const k = url.searchParams.get("k") || "";
    if (!/^([A-Z]\d{6}|n-[a-z0-9-]{1,40})$/.test(k)) return json({ error: "bad-member" }, 400);
    return fromR2(req, env, `members/${k}.json`);
  }
  if (p === "/api/notifications" && req.method === "GET") return json({ items: await alertLog(env) });
  if (p === "/api/tie-reviews" && req.method === "GET") return json(await tieReviews(env));
  if (p === "/api/context" && req.method === "GET") return tradeContext(env, url);
  if (p === "/api/health" && req.method === "GET") return health(env);
  if (p === "/api/people-review" && req.method === "GET") return peopleReviewList(env);
  if (p === "/api/people-review" && req.method === "POST") return peopleReview(req, env);
  if (p === "/api/tie-review" && req.method === "POST") return tieReview(req, env);
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
      await env.DATA.put(key, JSON.stringify({ title: ("Data problem: " + text).slice(0, 120), body: text.slice(0, 600), tag: "health", url: "/#health" }));
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
      const r = await fetch(`https://${s}.substack.com/feed`, { headers: { "User-Agent": "Mozilla/5.0 (compatible; CapitolCapital/1.0)", Accept: "application/rss+xml, application/xml" } });
      const t = r.ok ? await r.text() : "";
      if (!r.ok || !t.includes("<rss")) { bad.push(`${s} (HTTP ${r.status})`); continue; }
      await env.DATA.put(`feeds/substack/${s}.json`, JSON.stringify({ at: new Date().toISOString(), xml: t }), { httpMetadata: { contentType: "application/json" } });
    } catch (e) { bad.push(`${s} (${String(e && e.message || e).slice(0, 40)})`); }
  }
  if (bad.length) throw new Error(`${bad.length} of ${subs.length} feeds didn't load: ${bad.slice(0, 6).join(", ")}`);
}

async function r2json(env, key, dflt) { try { const o = await env.DATA.get(key); if (o) return await o.json(); } catch (e) {} return dflt; }
async function peopleReviewList(env) {
  return json({ items: await r2json(env, PEOPLE_QUEUE, []), verdicts: await r2json(env, PEOPLE_FILE, {}) });
}
async function peopleReview(req, env) {
  let b = {};
  try { b = await req.json(); } catch (e) {}
  const key = String(b.key || "").toLowerCase(), verdict = String(b.verdict || "");
  if (!/^[a-z][a-z'\-]{1,30}\|[a-z0-9.\-]{1,12}$/.test(key)) return json({ ok: false, why: "bad-key" }, 400);
  if (!["yes", "no", "clear"].includes(verdict)) return json({ ok: false, why: "bad-verdict" }, 400);
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
  if (!["yes", "no", "clear"].includes(verdict)) return json({ ok: false, why: "bad-verdict" }, 400);
  const all = await tieReviews(env);
  if (verdict === "clear") delete all[key];
  else all[key] = { verdict, note: String(b.note || "").slice(0, 500), at: new Date().toISOString() };
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
async function deliver(env, msg) {
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
        if (msg && msg.title) await deliver(env, msg);
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
    if (o.side === "buy" && cid.startsWith("tap-")) msgs.push({ t, s: `bought ${t}`, title: `Bought ${t}`, body: `Your Buy-button order filled: ${sh}.` });
    else if (o.side === "buy" && cid.startsWith("cs-")) msgs.push({ t, s: `bought ${t}`, title: `Tool bought ${t}`, body: sh + (w ? ` · ${w}` : "") + "." });
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
