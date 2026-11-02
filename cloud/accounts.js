// Family accounts (owner, Nov 2 2026): people the owner adds get their own login (an emailed sign-in link), their own
// real-money Alpaca account that the tool trades like the owner's, and a read-only app: Today, Members and their own
// Account. The owner sees every account on the Accounts page. Viewers can't reach anything else: the allowlist
// below is checked on the server for every request, whatever the app page shows.
//
// R2 layout (all private):
//   accounts/index.json            every account: id, label, email, status, auto-buying on/off, session generation
//   accounts/<id>/keys.json        their Alpaca keys, AES-GCM encrypted; never sent back to any page
//   accounts/<id>/portfolio.json   the daily run's snapshot of their account (same shape as the owner's)
//   accounts/<id>/alerts.json      their alert list; cleared.json, subs.json (their phones), fills_state.json
//   auth/links/<hash>.json         one-time sign-in links (only a hash of the link is kept), 20 minutes
//   mail/outbox/*.json             emails waiting for the GitHub mail job (it sends through the owner's Gmail)

const enc = new TextEncoder();
const IDX = "accounts/index.json";
const LINK_MIN = 20, SESSION_DAYS = 180;
const LIVE = "https://api.alpaca.markets", DATA = "https://data.alpaca.markets";
const PREFIX = "cs-", PARK_PREFIX = "cs-park-", MORE_PREFIX = "cs-more-", PARK_CHOICES = ["SPY", "VOO", "IVV", "SGOV", "BIL", "SHV"];
const ID_RX = /^[a-z0-9]{12}$/;
const EMAIL_RX = /^[^\s@<>"]{1,64}@[^\s@<>"]{1,180}\.[a-z]{2,24}$/i;

const b64u = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64u = s => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), c => c.charCodeAt(0));
const rand = n => b64u(crypto.getRandomValues(new Uint8Array(n)));
const hex = buf => [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, "0")).join("");
const sha = async s => crypto.subtle.digest("SHA-256", enc.encode(s));
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
function json(obj, status = 200, headers = {}) {
  return new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers } });
}
async function r2json(env, key, dflt) { try { const o = await env.DATA.get(key); if (o) return await o.json(); } catch (e) {} return dflt; }
const put = (env, key, v) => env.DATA.put(key, JSON.stringify(v), { httpMetadata: { contentType: "application/json" } });
async function body(req) { try { return await req.json(); } catch (e) { return {}; } }

// ---------- the account list ----------
export async function listAccounts(env) {
  const a = await r2json(env, IDX, []);
  return Array.isArray(a) ? a : [];
}
// Every change to the list is read-modify-write with an etag check, retried on a clash, so a sign-in landing at the
// same moment as Sign out everywhere can't undo it.
async function editAccounts(env, fn) {
  for (let i = 0; i < 6; i++) {
    const o = await env.DATA.get(IDX);
    const list = o ? await o.json() : [];
    const out = await fn(Array.isArray(list) ? list : []);
    if (out === undefined) return undefined;
    const ok = await env.DATA.put(IDX, JSON.stringify(list), { httpMetadata: { contentType: "application/json" },
      onlyIf: o ? { etagMatches: o.etag } : { uploadedBefore: new Date(0) } });
    if (ok) return out;
    await new Promise(r => setTimeout(r, 40 + Math.random() * 120));
  }
  throw new Error("the account list was busy; try again");
}
export async function getAccount(env, id) {
  if (!ID_RX.test(id || "")) return null;
  return (await listAccounts(env)).find(a => a.id === id) || null;
}
async function patchAccount(env, id, fn) {
  const r = await editAccounts(env, list => { const a = list.find(x => x.id === id); if (!a) return undefined; fn(a); return { ...a }; });
  return r || null;
}

// ---------- sessions: "<id>.<generation>.<expiry>.<signature>" (the owner's passcode cookie is "<expiry>.<signature>") ----------
// The signature uses the same key as the owner's sessions (made from the passcode). Raising an account's generation
// (Sign out everywhere, Disable, Remove) ends all of its sessions at once.
export async function viewerCookie(sign, a) {
  const exp = Math.floor(Date.now() / 1000 + SESSION_DAYS * 86400);
  const msg = `${a.id}.${a.gen || 1}.${exp}`;
  return { value: `${msg}.${await sign("v." + msg)}`, maxAge: SESSION_DAYS * 86400 };
}
export async function readViewer(env, sign, cookie, safeEq) {
  const p = String(cookie || "").split(".");
  if (p.length !== 4 || !ID_RX.test(p[0]) || !/^\d{1,6}$/.test(p[1]) || !(+p[2] > Date.now() / 1000)) return null;
  if (!safeEq(p[3], await sign(`v.${p[0]}.${p[1]}.${p[2]}`))) return null;
  const a = await getAccount(env, p[0]);
  if (!a || a.status === "disabled" || String(a.gen || 1) !== p[1]) return null;
  return a;
}

// ---------- Alpaca keys, encrypted at rest ----------
// The key comes from ACCOUNTS_KEY (a long random secret, set once as a GitHub secret; the deploy gives it to this
// server and the daily run reads it too). It is never the passcode, so the passcode never has to sit in the trading jobs.
async function aesKey(env) {
  const src = env.ACCOUNTS_KEY;
  if (!src || src.length < 32) throw new Error("ACCOUNTS_KEY isn't set up");
  return crypto.subtle.importKey("raw", await sha("cc-accounts-v1|" + src), "AES-GCM", false, ["encrypt", "decrypt"]);
}
async function saveKeys(env, id, keyId, secret) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: enc.encode("acct:" + id) }, await aesKey(env),
    enc.encode(JSON.stringify({ key: keyId, secret })));
  await put(env, `accounts/${id}/keys.json`, { v: 1, iv: b64u(iv), ct: b64u(ct) });
}
async function loadKeys(env, id) {
  const k = await r2json(env, `accounts/${id}/keys.json`, null);
  if (!k || !k.iv || !k.ct) return null;
  try {
    const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64u(k.iv), additionalData: enc.encode("acct:" + id) }, await aesKey(env), unb64u(k.ct));
    return JSON.parse(new TextDecoder().decode(pt));
  } catch (e) { return null; }
}
function client(keys) {
  const h = { "APCA-API-KEY-ID": keys.key, "APCA-API-SECRET-KEY": keys.secret };
  const call = async (method, path, b) => {
    const r = await fetch((path.startsWith("http") ? "" : LIVE) + path, { method, headers: { ...h, ...(b ? { "Content-Type": "application/json" } : {}) }, body: b ? JSON.stringify(b) : undefined });
    let data = null;
    try { data = await r.json(); } catch (e) {}
    return { status: r.status, ok: r.ok, data };
  };
  return { mode: "live", ready: true, get: p => call("GET", p), post: (p, b) => call("POST", p, b) };
}
export async function alpacaFor(env, a) {
  const keys = a && a.keys_set_at ? await loadKeys(env, a.id) : null;
  return keys ? client(keys) : null;
}

// what the owner's page may see about an account (never the keys, never a session)
function pub(a) {
  return { id: a.id, label: a.label, email: a.email, status: a.status, auto: a.auto !== false, created: a.created,
           last_login: a.last_login || null, keys: !!a.keys_set_at, keys_set_at: a.keys_set_at || null, key_hint: a.key_hint || null,
           link_sent: a.link_sent || null };
}

// ---------- sign-in links ----------
async function queueLink(env, a, origin, dispatch) {
  const t = rand(32);
  await put(env, `auth/links/${hex(await sha("link|" + t))}.json`, { id: a.id, gen: a.gen || 1, exp: Date.now() + LINK_MIN * 60e3 });
  const link = `${origin}/signin#${t}`;
  const html = `<div style="font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:#152033;max-width:480px">
<h2 style="font-weight:600;margin:0 0 12px">Sign in to Capitol Capital</h2>
<p>Tap the button on the phone you want to use. It works once and expires in ${LINK_MIN} minutes.</p>
<p style="margin:24px 0"><a href="${esc(link)}" style="background:#1D4F91;color:#fff;padding:12px 22px;border-radius:10px;text-decoration:none;font-weight:600">Sign in</a></p>
<p style="color:#46536A;font-size:14px">After you sign in, use Share → Add to Home Screen to keep the app on your phone. It stays signed in for 6 months.<br>
Didn't ask for this? You can ignore it.</p></div>`;
  await put(env, `mail/outbox/${Date.now()}-${rand(6)}.json`, { to: a.email, subject: "Your Capitol Capital sign-in link", html });
  await patchAccount(env, a.id, x => { x.link_sent = new Date().toISOString(); });
  await dispatch(env, "mail.yml", {});
}

// POST /api/login/email {email}: always the same answer, so the page doesn't reveal who has an account
// The work happens after the answer (ctx.waitUntil), so a real account and a made-up one answer equally fast.
// Limits: 10 requests an hour per network address, and 4 links an hour per real account (junk addresses can't use
// up anyone's allowance). Counts are per hour bucket, one small file each.
export async function requestLink(req, env, url, dispatch, ctx) {
  const b = await body(req);
  const email = String(b.email || "").trim().toLowerCase();
  if (!EMAIL_RX.test(email)) return json({ error: "bad-email", message: "That doesn't look like an email address." }, 400);
  const ip = req.headers.get("CF-Connecting-IP") || "local", hour = Math.floor(Date.now() / 3600e3);
  const work = (async () => {
    if (await bump(env, `auth/rate/${hour}/ip-${hex(await sha("ip|" + ip)).slice(0, 24)}.json`) > 10) return;
    const a = (await listAccounts(env)).find(x => x.email === email && x.status !== "disabled");
    if (!a || await bump(env, `auth/rate/${hour}/acct-${a.id}.json`) > 4) return;
    await queueLink(env, a, url.origin, dispatch);
  })().catch(() => {});
  if (ctx && ctx.waitUntil) ctx.waitUntil(work); else await work;
  return json({ ok: true, message: "If that email has an account, a sign-in link is on its way. It can take a minute or two." });
}
async function bump(env, key) {
  const n = ((await r2json(env, key, { n: 0 })).n || 0) + 1;
  await put(env, key, { n });
  return n;
}

// POST /api/login/link {t}: trade a one-time link for a session cookie
export async function redeemLink(req, env, sign, cookieName) {
  const b = await body(req);
  const t = String(b.t || "");
  const bad = json({ error: "link", message: "That sign-in link has expired or was already used. Ask for a new one." }, 401);
  if (!/^[A-Za-z0-9_-]{40,50}$/.test(t)) return bad;
  const key = `auth/links/${hex(await sha("link|" + t))}.json`;
  const o = await env.DATA.get(key);
  if (!o) return bad;
  const L = await o.json();
  if (L.used) return bad;
  // mark it used only if nobody else did in between (two taps at once: only one wins)
  const won = await env.DATA.put(key, JSON.stringify({ used: true }), { onlyIf: { etagMatches: o.etag } });
  if (!won || !(L.exp > Date.now())) return bad;
  const a = await patchAccount(env, L.id, x => { if (String(x.gen || 1) !== String(L.gen || 1) || x.status === "disabled") return;
    if (x.status === "invited") x.status = "active"; x.last_login = new Date().toISOString(); });
  if (!a || a.status === "disabled" || String(a.gen || 1) !== String(L.gen || 1)) return bad;
  const c = await viewerCookie(sign, a);
  return json({ ok: true }, 200, { "Set-Cookie": `${cookieName}=${c.value}; Path=/; Max-Age=${c.maxAge}; HttpOnly; Secure; SameSite=Strict` });
}

// The page an emailed link opens: the token stays in the #fragment (never sent to the server or kept in logs) until
// the person taps Sign in, so email scanners that open links can't use it up.
export const SIGNIN_PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer"><link rel="manifest" href="/manifest.webmanifest"><link rel="apple-touch-icon" href="/icon-180.png">
<title>Capitol Capital</title><style>
:root{--ground:#F3F5F8;--ink:#152033;--ink2:#46536A;--accent:#1D4F91;--on:#fff}
@media (prefers-color-scheme:dark){:root{--ground:#0E131A;--ink:#E6EBF2;--ink2:#B4BECC;--accent:#7FAEEA;--on:#0E131A}}
html,body{margin:0;height:100%;background:var(--ground);color:var(--ink);font:16px system-ui,-apple-system,"Segoe UI",sans-serif}
main{min-height:100%;display:grid;place-items:center;padding:24px 16px;box-sizing:border-box}
div.c{width:100%;max-width:340px;display:grid;gap:14px;text-align:center}
img{width:84px;height:84px;border-radius:20px;justify-self:center}h1{margin:0;font-weight:600;font-size:26px}p{margin:0;color:var(--ink2);font-size:14px}
button{font:600 16px inherit;padding:12px;border-radius:10px;border:0;background:var(--accent);color:var(--on);cursor:pointer}button:disabled{opacity:.6}
.msg{min-height:1.2em;font-size:14px;color:#C0392B}
</style></head><body><main><div class="c"><img src="/icon-180.png" alt=""><h1>Capitol Capital</h1>
<p>Tap Sign in to use the app on this phone.</p><button type="button" id="go">Sign in</button><div class="msg" id="msg" role="alert"></div></div></main>
<script>
const t = location.hash.slice(1); history.replaceState(null, "", "/signin");
const go = document.getElementById("go"), msg = document.getElementById("msg");
if (!t) { go.disabled = true; msg.textContent = "This link is missing its code. Open it again from the email."; }
go.addEventListener("click", async () => {
  go.disabled = true; msg.textContent = "";
  try {
    const r = await fetch("/api/login/link", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: JSON.stringify({ t }) });
    if (r.ok) { location.replace("/"); return; }
    let j = {}; try { j = await r.json(); } catch (x) {}
    msg.textContent = j.message || "Couldn't sign in. Ask for a new link.";
  } catch (x) { msg.textContent = "No connection. Try again."; go.disabled = false; }
});
</script></body></html>`;

// ---------- what a viewer may call ----------
// Anything not listed answers 403, whatever the page asks for.
const VIEWER_GET = new Set(["/api/session", "/api/data", "/api/members", "/api/member", "/api/posts", "/api/chart", "/api/charts",
  "/api/news", "/api/about", "/api/context", "/api/notifications", "/api/live", "/api/push/key"]);
const VIEWER_POST = new Set(["/api/logout", "/api/notifications/clear", "/api/push/subscribe", "/api/buy-more", "/api/me/keys"]);
export function viewerAllowed(method, path) {
  return method === "GET" ? VIEWER_GET.has(path) : method === "POST" ? VIEWER_POST.has(path) : false;
}
// Routes whose answer is the same for everyone (market data, members' public trades) are handled by the worker as
// usual; these are answered here with the viewer's own data.
export async function viewerApi(req, env, url, a, h) {
  const p = url.pathname;
  if (p === "/api/session") return json({ ok: true, role: "viewer", connected: !!a.keys_set_at, auto: a.auto !== false, mode: "live" });
  if (p === "/api/data") return viewerData(env, a);
  if (p === "/api/live") return viewerLive(env, a, h.live);
  if (p === "/api/notifications") return json({ items: await visibleAlerts(env, a.id) });
  if (p === "/api/notifications/clear") return clearAlerts(req, env, a.id);
  if (p === "/api/buy-more") return buyMore(req, env, a, h.maxTap);
  if (p === "/api/me/keys") return setKeys(req, env, a);
  if (p === "/api/push/key") return json({ key: (await h.vapid(env)).pub, phones: (await h.getSubs(env, subsKey(a.id))).length });
  if (p === "/api/push/subscribe") {
    const b = await body(req);
    try { await h.saveSub(env, b.sub, url.origin, subsKey(a.id)); } catch (e) { return json({ error: "bad-subscription" }, 400); }
    const r = await h.sendAll(env, { title: "Alerts are on", body: "You'll get an alert when the tool buys or sells in your account.", tag: "welcome", url: "/" }, subsKey(a.id));
    return json({ ok: true, ...r });
  }
  return null;        // shared, read-only data: the worker answers it
}
export const subsKey = id => `accounts/${id}/subs.json`;

// The dashboard file, cut down to what Today, Members and Account use, with the viewer's own account in it.
// Allowlist: anything new the daily run adds stays out until it's added here on purpose.
const VIEWER_DATA_KEYS = ["version", "updated", "watchlist", "worth", "unusual", "photos", "member_tags", "data_warning", "foreign"];
async function viewerData(env, a) {
  const [o, port] = await Promise.all([env.DATA.get("dashboard_data.json"), r2json(env, `accounts/${a.id}/portfolio.json`, null)]);
  if (!o) return json({ error: "no-file", message: "The daily update hasn't run yet" }, 404);
  const d = await o.json(), out = {};
  for (const k of VIEWER_DATA_KEYS) if (k in d) out[k] = d[k];
  const pol = (d.adaptive && d.adaptive.policy) || {};
  out.adaptive = { policy: { hold_other: pol.hold_other, hold_small: pol.hold_small, sizing: pol.sizing } };
  out.viewer = { connected: !!a.keys_set_at, auto: a.auto !== false };
  if (port) {
    out.portfolio = port.portfolio || null;
    out.member_sold = port.member_sold || {};
    out.close_report = port.close_report || null;
  }
  return json(out, 200, { "Cache-Control": "private, no-cache" });
}
async function viewerLive(env, a, live) {
  const c = await alpacaFor(env, a);
  if (!c) return json({ error: "no-keys" }, 503);
  return live(env, c, true);
}

// ---------- their alerts ----------
async function visibleAlerts(env, id) {
  const [log, c] = await Promise.all([r2json(env, `accounts/${id}/alerts.json`, []), r2json(env, `accounts/${id}/cleared.json`, { ids: [], before: null })]);
  const ids = new Set(c.ids || []), before = c.before ? Date.parse(c.before) : 0;
  return (Array.isArray(log) ? log : []).filter(x => !ids.has(x.id) && !(Date.parse(x.at) <= before));
}
async function clearAlerts(req, env, id) {
  const b = await body(req), key = `accounts/${id}/cleared.json`;
  const c = await r2json(env, key, { ids: [], before: null });
  if (b.all === true) { c.before = new Date().toISOString(); c.ids = []; }
  else if (typeof b.id === "string" && /^[a-z0-9]{4,24}$/.test(b.id)) { c.ids = (c.ids || []).filter(x => x !== b.id).concat(b.id).slice(-500); }
  else return json({ ok: false, why: "bad-id" }, 400);
  await put(env, key, c);
  return json({ ok: true });
}
export async function deliverTo(env, id, msg, sendAll) {
  const aid = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  const entry = { id: aid, at: new Date().toISOString(), title: String(msg.title || "").slice(0, 120), body: String(msg.body || "").slice(0, 600),
                  kind: msg.tag || "", url: msg.url || "/#port", items: (msg.items || []).slice(0, 20) };
  const key = `accounts/${id}/alerts.json`, log = await r2json(env, key, []);
  await put(env, key, [entry, ...(Array.isArray(log) ? log : [])].slice(0, 300));
  const u = entry.url.includes("#") ? entry.url.replace("#", `?n=${aid}#`) : entry.url + `?n=${aid}`;
  return sendAll(env, { title: entry.title, body: entry.body, tag: msg.tag || "", url: u }, subsKey(id));
}

// ---------- first-time setup: they paste their own live Alpaca keys once ----------
async function setKeys(req, env, a) {
  if (a.keys_set_at) return json({ ok: false, why: "already-set", message: "Your account is already connected. Ask the owner if it needs to change." }, 409);
  const b = await body(req);
  const key = String(b.key || "").trim(), secret = String(b.secret || "").trim();
  if (!/^[A-Z0-9]{16,40}$/.test(key) || !/^[A-Za-z0-9]{30,80}$/.test(secret))
    return json({ ok: false, why: "format", message: "Those don't look like Alpaca keys. Copy both again from the Alpaca page." }, 400);
  if (/^PK/.test(key)) return json({ ok: false, why: "paper", message: "Those are paper-trading keys. Make the keys from your real-money (Live) account." }, 400);
  const r = await client({ key, secret }).get("/v2/account");
  if (r.status === 401 || r.status === 403) return json({ ok: false, why: "rejected", message: "Alpaca didn't accept those keys. Check you copied the whole secret, from the Live account." }, 400);
  if (!r.ok || !r.data) return json({ ok: false, why: "alpaca", message: "Couldn't reach Alpaca to check the keys. Try again in a minute." }, 502);
  if (r.data.status && r.data.status !== "ACTIVE") return json({ ok: false, why: "inactive", message: `Your Alpaca account isn't open for trading yet (it says "${String(r.data.status).toLowerCase()}"). Try again once it's approved.` }, 400);
  await saveKeys(env, a.id, key, secret);
  await patchAccount(env, a.id, x => { x.keys_set_at = new Date().toISOString(); x.key_hint = "…" + key.slice(-4); });
  await env.DATA.put(`accounts/${a.id}/fills_state.json`, JSON.stringify({ seen: [], fresh: true }));
  return json({ ok: true, message: "Connected. The tool starts buying the current picks at the next update." });
}

// ---------- Buy more (viewers, on a stock the tool already holds for them) ----------
// The order is marked as the tool's (cs-more-…), so the extra shares are sold with the rest of the position when
// the tool sells it. Short on cash: some of the parked T-bill fund is sold first.
async function buyMore(req, env, a, maxTap) {
  const c = await alpacaFor(env, a);
  if (!c) return json({ ok: false, why: "no-keys" }, 503);
  const b = await body(req);
  const t = String(b.t || "").toUpperCase(), dollars = Math.round(+b.dollars * 100) / 100;
  if (!/^[A-Z][A-Z0-9-]{0,6}$/.test(t)) return json({ ok: false, why: "bad-request" }, 400);
  if (!(dollars >= 1 && dollars <= maxTap)) return json({ ok: false, why: "amount-over-limit", cap: maxTap }, 400);
  const sym = t.replace(/-/g, ".");
  const since = new Date(Date.now() - 500 * 864e5).toISOString();
  const [asset, acct, pos, hist] = await Promise.all([c.get(`/v2/assets/${encodeURIComponent(sym)}`), c.get("/v2/account"),
    c.get(`/v2/positions/${encodeURIComponent(sym)}`), c.get(`/v2/orders?status=closed&limit=500&direction=desc&symbols=${encodeURIComponent(sym)}&after=${encodeURIComponent(since)}`)]);
  if (!pos.ok) return json({ ok: false, why: "not-held", message: "Buy more works on stocks the tool holds for you." }, 400);
  const toolHeld = (hist.data || []).some(o => o.side === "buy" && o.filled_at && (o.client_order_id || "").startsWith(PREFIX) && !(o.client_order_id || "").startsWith(PARK_PREFIX));
  if (!toolHeld) return json({ ok: false, why: "not-held", message: "Buy more works on stocks the tool holds for you." }, 400);
  if (!asset.ok || !asset.data?.tradable) return json({ ok: false, why: "not-tradable" }, 400);
  if (!acct.ok) return json({ ok: false, why: "not-found" }, 400);
  // own money only (never margin): cash plus the parked T-bill fund the tool can sell for it
  const today = new Date(Date.now() - 864e5).toISOString();
  const [pk, recent] = await Promise.all([c.get("/v2/positions"), c.get(`/v2/orders?status=all&limit=200&direction=desc&after=${encodeURIComponent(today)}`)]);
  const parkedVal = (pk.data || []).filter(x => ["SGOV", "BIL", "SHV"].includes(x.symbol)).reduce((s, x) => s + (+x.market_value || 0), 0);
  if (dollars > Math.max(0, +acct.data.cash) + parkedVal - 25) return json({ ok: false, why: "not-enough-cash", message: "that's more than the cash and parked money in the account" }, 400);
  const spent = (recent.data || []).filter(o => (o.client_order_id || "").startsWith(MORE_PREFIX) && !["canceled", "rejected", "expired"].includes(o.status))
    .reduce((s, o) => s + (+o.notional || (+o.qty || 0) * (+o.filled_avg_price || 0)), 0);
  if (spent + dollars > maxTap) return json({ ok: false, why: "daily-limit", message: `that's over the ${"$" + Math.round(maxTap).toLocaleString("en-US")} a day limit for Buy more` }, 400);
  const reqId = String(Date.now());
  try {
    const cash = +acct.data.cash;
    if (dollars > cash) {
      const ph = await c.get(`/v2/orders?status=closed&limit=500&direction=desc&symbols=${PARK_CHOICES.join(",")}&after=${encodeURIComponent(since)}`);
      const pk = [...new Set((ph.data || []).filter(o => o.side === "buy" && o.filled_at && (o.client_order_id || "").startsWith(PARK_PREFIX)).map(o => o.symbol))][0];
      if (pk) {
        const pp = await c.get(`/v2/positions/${pk}`);
        const amt = Math.min(+(pp.data && pp.data.market_value) || 0, dollars - cash + 50);
        if (pp.ok && amt > 1) await c.post("/v2/orders", { symbol: pk, side: "sell", notional: amt.toFixed(2), type: "market", time_in_force: "day",
          client_order_id: `${PARK_PREFIX}more-${reqId}`.slice(0, 48) });
      }
    }
  } catch (e) {}
  const order = { symbol: sym, side: "buy", type: "market", time_in_force: "day", client_order_id: `${MORE_PREFIX}${t}-${reqId}`.slice(0, 48) };
  if (asset.data.fractionable) order.notional = dollars.toFixed(2);
  else {
    const tr = await c.get(`${DATA}/v2/stocks/${encodeURIComponent(sym)}/trades/latest?feed=iex`);
    const price = tr.data?.trade && +tr.data.trade.p;
    if (!price) return json({ ok: false, why: "no-price" }, 400);
    const qty = Math.floor(dollars / price);
    if (qty < 1) return json({ ok: false, why: "amount-too-small" }, 400);
    order.qty = String(qty);
  }
  const r = await c.post("/v2/orders", order);
  if (!r.ok) return json({ ok: false, why: "rejected-" + r.status, message: r.data && r.data.message }, 400);
  const clock = await c.get("/v2/clock");
  return json({ ok: true, status: r.data.status, market_open: !!clock.data?.is_open, next_open: clock.data?.next_open, qty: order.qty ? +order.qty : null });
}

// ---------- owner: the Accounts page ----------
export async function ownerAccounts(env) {
  const list = await listAccounts(env);
  const rows = await Promise.all(list.map(async a => {
    const port = await r2json(env, `accounts/${a.id}/portfolio.json`, null), p = port && port.portfolio;
    return { ...pub(a), summary: p ? { equity: p.equity, cash: p.cash, day_pl: p.day_pl, positions: (p.positions || []).length,
      invested: (p.positions || []).filter(x => x.sleeve !== "park").reduce((s, x) => s + (x.mv || 0), 0), updated: p.updated } : null,
      problem: port && port.problem || null };
  }));
  return json({ accounts: rows });
}
export async function ownerAccount(env, url) {
  const a = await getAccount(env, url.searchParams.get("id"));
  if (!a) return json({ error: "not-found" }, 404);
  const port = await r2json(env, `accounts/${a.id}/portfolio.json`, null);
  return json({ account: pub(a), portfolio: port && port.portfolio || null, member_sold: port && port.member_sold || {},
                close_report: port && port.close_report || null, problem: port && port.problem || null });
}
export async function ownerAccountLive(env, url, live) {
  const a = await getAccount(env, url.searchParams.get("id"));
  if (!a) return json({ error: "not-found" }, 404);
  const c = await alpacaFor(env, a);
  if (!c) return json({ error: "no-keys" }, 503);
  return live(env, c, true);
}
// POST /api/accounts {action, ...}
export async function ownerAccountsPost(req, env, url, dispatch) {
  const b = await body(req), act = String(b.action || "");
  if (act === "add") {
    const label = String(b.label || "").trim().slice(0, 40), email = String(b.email || "").trim().toLowerCase();
    if (!label) return json({ ok: false, message: "Give the account a name, like Mom or Sam." }, 400);
    if (!EMAIL_RX.test(email)) return json({ ok: false, message: "That doesn't look like an email address." }, 400);
    if (!env.ACCOUNTS_KEY || env.ACCOUNTS_KEY.length < 32) return json({ ok: false, message: "Set up the ACCOUNTS_KEY secret first (it locks everyone's Alpaca keys), then redeploy." }, 400);
    const a = { id: [...crypto.getRandomValues(new Uint8Array(12))].map(x => "abcdefghijklmnopqrstuvwxyz0123456789"[x % 36]).join(""),
                label, email, status: "invited", auto: true, gen: 1, created: new Date().toISOString() };
    const why = await editAccounts(env, list => {
      if (list.some(x => x.email === email)) return "That email already has an account.";
      if (list.length >= 25) return "That's the most accounts the app handles.";
      list.push(a); return "";
    });
    if (why) return json({ ok: false, message: why }, 400);
    let sent = true;
    try { await queueLink(env, a, url.origin, dispatch); } catch (e) { sent = false; }
    return json({ ok: true, account: pub(a), message: sent ? `Added. A sign-in link is on its way to ${email}.` : "Added, but the email couldn't be queued. Tap Send sign-in link to try again." });
  }
  const a = await getAccount(env, String(b.id || ""));
  if (!a) return json({ ok: false, message: "That account doesn't exist anymore." }, 404);
  if (act === "link") {
    if (a.status === "disabled") return json({ ok: false, message: "Turn the account back on first." }, 400);
    try { await queueLink(env, a, url.origin, dispatch); } catch (e) { return json({ ok: false, message: "Couldn't queue the email: " + String(e.message || e).slice(0, 120) }, 502); }
    return json({ ok: true, message: `Sign-in link sent to ${a.email}.` });
  }
  if (act === "auto") {
    const on = b.on === true;
    await patchAccount(env, a.id, x => { x.auto = on; });
    return json({ ok: true, message: on ? "Auto-buying is on for this account." : "Auto-buying is off: the tool won't buy anything new here. It still sells what it holds on schedule." });
  }
  if (act === "disable" || act === "enable") {
    await patchAccount(env, a.id, x => { x.status = act === "disable" ? "disabled" : (x.last_login ? "active" : "invited"); x.gen = (x.gen || 1) + 1; });
    return json({ ok: true, message: act === "disable" ? "Turned off: signed out everywhere and the tool stops trading this account." : "Turned back on. Send a sign-in link so they can log in again." });
  }
  if (act === "signout") {
    await patchAccount(env, a.id, x => { x.gen = (x.gen || 1) + 1; });
    return json({ ok: true, message: "Signed out on every phone." });
  }
  if (act === "reset-keys") {
    await env.DATA.delete(`accounts/${a.id}/keys.json`);
    await patchAccount(env, a.id, x => { delete x.keys_set_at; delete x.key_hint; });
    return json({ ok: true, message: "Keys removed. They'll be asked to connect Alpaca again next time they open the app. Nothing they hold was sold." });
  }
  if (act === "remove") {
    if (b.confirm !== a.email) return json({ ok: false, message: "Type the account's email to confirm." }, 400);
    await editAccounts(env, list => { const i = list.findIndex(x => x.id === a.id); if (i >= 0) list.splice(i, 1); return true; });
    let cursor;
    do {
      const l = await env.DATA.list({ prefix: `accounts/${a.id}/`, cursor });
      for (const o of l.objects) await env.DATA.delete(o.key);
      cursor = l.truncated ? l.cursor : undefined;
    } while (cursor);
    return json({ ok: true, message: "Removed. The tool no longer trades that account; anything held in it stays in their Alpaca account." });
  }
  return json({ ok: false, message: "Unknown action." }, 400);
}

// ---------- every 2 minutes in market hours: alert each person on their own fills ----------
export async function checkFillsAll(env, sendAll, reasons) {
  const list = (await listAccounts(env)).filter(a => a.status !== "disabled" && a.keys_set_at);
  if (!list.length) return;
  let why = null;
  const fails = [];
  for (const a of list) {
    try {
      const c = await alpacaFor(env, a);
      if (!c) { fails.push(a.label + ": keys unreadable"); continue; }
      const key = `accounts/${a.id}/fills_state.json`, st = await r2json(env, key, null);
      const after = new Date(Date.now() - 4 * 864e5).toISOString();
      const r = await c.get(`/v2/orders?status=closed&direction=desc&limit=200&after=${encodeURIComponent(after)}`);
      if (!r.ok || !Array.isArray(r.data)) { fails.push(`${a.label}: Alpaca HTTP ${r.status}`); continue; }
      const filled = r.data.filter(o => o.status === "filled");
      if (!st || st.fresh) { await put(env, key, { seen: filled.map(o => o.id) }); continue; }
      const seen = new Set(st.seen || []), fresh = filled.filter(o => !seen.has(o.id)).reverse();
      if (!fresh.length) continue;
      why = why || await reasons(env);
      const msgs = [];
      for (const o of fresh) {
        seen.add(o.id);
        const cid = o.client_order_id || "";
        if (cid.startsWith(PARK_PREFIX)) continue;
        const t = o.symbol.replace(/\./g, "-"), q = +o.filled_qty, px = +o.filled_avg_price;
        const sh = `${q % 1 ? q.toFixed(4).replace(/0+$/, "") : q} share${q === 1 ? "" : "s"} at $${px.toFixed(2)} ($${Math.round(q * px).toLocaleString("en-US")})`;
        const w = why[`${t}|${o.side}`];
        if (o.side === "buy" && cid.startsWith(MORE_PREFIX)) msgs.push({ t, s: `bought more ${t}`, title: `Bought more ${t}`, body: `Your Buy more order filled: ${sh}.` });
        else if (o.side === "buy" && cid.startsWith(PREFIX)) msgs.push({ t, s: `bought ${t}`, title: `Bought ${t}`, body: sh + (w ? ` · ${w}` : "") + "." });
        else if (o.side === "sell" && cid.startsWith(PREFIX)) msgs.push({ t, s: `sold ${t}`, title: `Sold ${t}`, body: sh + (w ? ` · ${w}` : "") + "." });
        else if (o.side === "sell") msgs.push({ t, s: `sold ${t}`, title: `Sold ${t}`, body: sh + "." });
        else msgs.push({ t, s: `bought ${t}`, title: `Bought ${t}`, body: sh + "." });
      }
      await put(env, key, { seen: [...seen].slice(-1500) });
      if (msgs.length > 3) await deliverTo(env, a.id, { title: `${msgs.length} trades filled`, body: msgs.map(m => m.s).join(", ").replace(/^./, x => x.toUpperCase()) + ".",
        tag: "fills", url: "/#port", items: msgs.map(m => ({ l: `${m.title}: ${m.body}`, u: `/#port/${m.t}` })) }, sendAll);
      else for (const m of msgs) await deliverTo(env, a.id, { title: m.title, body: m.body, tag: "fill-" + m.title, url: `/#port/${m.t}` }, sendAll);
    } catch (e) { fails.push(`${a.label}: ${String(e && e.message || e).slice(0, 80)}`); }
  }
  if (fails.length) throw new Error("family accounts' fill check: " + fails.join("; "));
}
