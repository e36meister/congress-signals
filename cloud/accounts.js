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
//   mail/outbox/*.json             emails waiting for the GitHub mail job (only when sending straight through Resend failed)

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
// ---------- family trading hold (owner, Nov 3 2026) ----------
// One switch for every family account, kept at accounts/hold.json. While it's on (the default until the owner turns
// it off on the Accounts page), family accounts are view-only: they can sign the terms and connect Alpaca, but the
// tool places no orders for them (congress_signals/accounts.py) and their Buy and Sell buttons are off.
export async function familyHold(env) { const h = await r2json(env, "accounts/hold.json", null); return !h || h.on !== false; }
const HOLD_MSG = "Trading for family accounts hasn't started yet; the app is view-only for now";
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
  return { mode: "live", ready: true, get: p => call("GET", p), post: (p, b) => call("POST", p, b), del: p => call("DELETE", p) };
}
export async function alpacaFor(env, a) {
  const keys = a && a.keys_set_at ? await loadKeys(env, a.id) : null;
  return keys ? client(keys) : null;
}

// what the owner's page may see about an account (never the keys, never a session)
function pub(a) {
  return { id: a.id, label: a.label, email: a.email, status: a.status, auto: a.auto !== false, created: a.created,
           last_login: a.last_login || null, keys: !!a.keys_set_at, keys_set_at: a.keys_set_at || null, key_hint: a.key_hint || null,
           link_sent: a.link_sent || null, size: sizeOf(a), started_at: a.started_at || null,
           build: a.build || null, terms_at: termsOk(a) ? a.terms_at : null, paused: !!a.paused, paused_at: a.paused_at || null, park: parkOf(a) };
}

// ---------- sign-in links ----------
// Emails go out straight away through Resend, from admin@capitolcapital.cc. If that fails (or the RESEND_API_KEY
// secret isn't set), the email waits in R2 for the GitHub mail job, which tries again (Resend, then Gmail).
// Returns true when it was sent here; false means it was queued and the mail job needs starting.
const MAIL_FROM = "Capitol Capital <admin@capitolcapital.cc>";
async function sendMail(env, msg) {
  const to = msg.to === "owner" ? env.OWNER_EMAIL : msg.to;
  if (env.RESEND_API_KEY && to) {
    try {
      const r = await fetch("https://api.resend.com/emails", { method: "POST",
        headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
        body: JSON.stringify({ from: MAIL_FROM, to: [to], subject: String(msg.subject || "").slice(0, 140), html: msg.html, ...(msg.reply_to ? { reply_to: msg.reply_to } : {}) }) });
      if (r.ok) return true;
    } catch (e) {}
  }
  await put(env, `mail/outbox/${Date.now()}-${rand(6)}.json`, msg);
  return false;
}
async function queueLink(env, a, origin, dispatch) {
  const t = rand(32);
  await put(env, `auth/links/${hex(await sha("link|" + t))}.json`, { id: a.id, gen: a.gen || 1, exp: Date.now() + LINK_MIN * 60e3, ...(a.owner ? { owner: true } : {}) });
  const link = `${env.APP_HOST ? "https://" + env.APP_HOST : origin}/signin#${t}`;
  const html = `<div style="font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:#152033;max-width:480px">
<h2 style="font-weight:600;margin:0 0 12px">Sign in to Capitol Capital</h2>
<p>Tap the button on the device you want to use. It works once and expires in ${LINK_MIN} minutes.</p>
<p style="margin:24px 0"><a href="${esc(link)}" style="background:#1D4F91;color:#fff;padding:12px 22px;border-radius:10px;text-decoration:none;font-weight:600">Sign in</a></p>
<p style="color:#46536A;font-size:14px">After you sign in, use Share → Add to Home Screen to keep the app on your phone or computer. It stays signed in for 6 months.<br>
Didn't ask for this? You can ignore it.</p></div>`;
  const sent = await sendMail(env, { to: a.email, subject: "Your Capitol Capital sign-in link", html });
  if (!a.owner) await patchAccount(env, a.id, x => { x.link_sent = new Date().toISOString(); });
  if (!sent) await dispatch(env, "mail.yml", {});
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
    // the owner can sign in with an email link too (backup for the passcode)
    if (env.OWNER_EMAIL && email === String(env.OWNER_EMAIL).trim().toLowerCase()) {
      if (await bump(env, `auth/rate/${hour}/acct-owner.json`) > 4) return;
      return queueLink(env, { id: "owner", gen: 1, email, owner: true }, url.origin, dispatch);
    }
    const a = (await listAccounts(env)).find(x => x.email === email && x.status !== "disabled");
    if (!a || await bump(env, `auth/rate/${hour}/acct-${a.id}.json`) > 4) return;
    await queueLink(env, a, url.origin, dispatch);
  })().catch(() => {});
  if (ctx && ctx.waitUntil) ctx.waitUntil(work); else await work;
  return json({ ok: true, message: "If that email has an account, a sign-in link is on its way. It should arrive within a minute." });
}
async function bump(env, key) {
  const n = ((await r2json(env, key, { n: 0 })).n || 0) + 1;
  await put(env, key, { n });
  return n;
}

// POST /api/login/link {t}: trade a one-time link for a session cookie
export async function redeemLink(req, env, sign, cookieName, ownerCookie) {
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
  if (L.owner) return json({ ok: true }, 200, { "Set-Cookie": await ownerCookie() });
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
<p>Tap Sign in to use the app on this device.</p><button type="button" id="go">Sign in</button><div class="msg" id="msg" role="alert"></div></div></main>
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
const VIEWER_GET = new Set(["/api/session", "/api/data", "/api/members", "/api/member", "/api/chart", "/api/charts", "/api/search",
  "/api/movers", "/api/news", "/api/about", "/api/context", "/api/notifications", "/api/live", "/api/push/key", "/api/me/photo",
  "/api/me/prefs", "/api/me/widget", "/api/me/terms", "/api/tax", "/api/tax/file"]);
const VIEWER_POST = new Set(["/api/logout", "/api/notifications/clear", "/api/push/subscribe", "/api/buy-more", "/api/sell",
  "/api/me/keys", "/api/me/photo", "/api/bug", "/api/me/prefs", "/api/me/widget", "/api/me/email-ask", "/api/me/size", "/api/me/terms", "/api/me/pause", "/api/me/park"]);
export function viewerAllowed(method, path) {
  return method === "GET" ? VIEWER_GET.has(path) : method === "POST" ? VIEWER_POST.has(path) : false;
}
// Routes whose answer is the same for everyone (market data, members' public trades) are handled by the worker as
// usual; these are answered here with the viewer's own data.
export async function viewerApi(req, env, url, a, h) {
  const p = url.pathname;
  if (p === "/api/session") { try { await noteSeen(env, a); } catch (e) {} }
  if (p === "/api/session") {
    // the first time they open the app is remembered, so a few days in they're asked once whether they want emails too
    if (!a.first_seen) { a.first_seen = new Date().toISOString(); await patchAccount(env, a.id, x => { if (!x.first_seen) x.first_seen = a.first_seen; }); }
    // which app version they last opened (shown on the owner's Accounts page)
    const bv = String(url.searchParams.get("b") || "").slice(0, 40);
    if (/^0\.\d+\.[\w-]+$/.test(bv) && bv !== a.build) await patchAccount(env, a.id, x => { x.build = bv; x.build_at = new Date().toISOString(); });
    return json({ ok: true, role: "viewer", connected: !!a.keys_set_at, auto: a.auto !== false, mode: "live", size: sizeOf(a),
      terms: termsOk(a), paused: !!a.paused, hold: await familyHold(env), build: env.APP_BUILD || null, initials: nameInitials(a.name || a.label),
      photo: a.photo_at || null, photo_prompted: !!(a.photo_at || a.photo_skipped),
      ask_email: !a.email_asked && Date.now() - Date.parse(a.first_seen) >= EMAIL_ASK_DAYS * 864e5 });
  }
  if (p === "/api/me/email-ask") {
    const b = await body(req), yes = b.choice === "yes";
    if (yes) { const cur = await getPrefs(env, a.id); cur.email.close = true; cur.email.fills = true; await put(env, `accounts/${a.id}/prefs.json`, cur); }
    await patchAccount(env, a.id, x => { x.email_asked = yes ? "yes" : "no"; });
    return json({ ok: true });
  }
  if (p === "/api/data" || p === "/api/live") { try { await noteSeen(env, a); } catch (e) {} }
  if (p === "/api/data") return viewerData(env, a);
  if (p === "/api/tax") { const idx = await r2json(env, `accounts/${a.id}/tax/index.json`, null); return idx ? json(idx) : json({ error: "no-file" }, 404); }
  if (p === "/api/tax/file") return viewerTaxFile(env, a, url);
  if (p === "/api/movers") return viewerMovers(env, a);
  if (p === "/api/me/photo" && req.method === "GET") return getPhoto(env, a);
  if (p === "/api/me/photo") return setPhoto(req, env, a);
  if (p === "/api/bug") return bugReport(req, env, a, h.dispatch);
  if (p === "/api/me/prefs" && req.method === "GET") return json(await getPrefs(env, a.id));
  if (p === "/api/me/prefs") return setPrefs(req, env, a);
  if (p === "/api/me/widget") return h.widgetScript(url, await widgetKey(env, a.id, req.method === "POST"));
  if (p === "/api/sell") {
    if (await familyHold(env)) return json({ ok: false, why: "hold", message: HOLD_MSG }, 400);
    const c = await alpacaFor(env, a);
    if (!c) return json({ ok: false, why: "no-keys" }, 503);
    const b = await req.clone().json().catch(() => ({}));
    if (["SGOV", "BIL", "SHV"].includes(String(b.t || "").toUpperCase()))
      return json({ ok: false, why: "parked", message: "That's the cash the tool keeps for new picks; it sells it itself when it needs to." }, 400);
    return h.sell(req, c);
  }
  if (p === "/api/live") return viewerLive(env, a, h.live);
  if (p === "/api/notifications") return json({ items: await visibleAlerts(env, a.id) });
  if (p === "/api/notifications/clear") return clearAlerts(req, env, a.id);
  if (p === "/api/buy-more") return buyMore(req, env, a, h.maxTap);
  if (p === "/api/me/keys") return setKeys(req, env, a, h);
  if (p === "/api/me/size") return setSize(req, env, a);
  if (p === "/api/me/terms" && req.method === "GET") return json({ terms: TERMS, signed: await r2json(env, `accounts/${a.id}/terms.json`, null) });
  if (p === "/api/me/terms") return signTerms(req, env, a, h.dispatch);
  if (p === "/api/me/pause") return setPause(req, env, a);
  if (p === "/api/me/park") return setPark(req, env, a);
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
// Today for them is the Buy candidates only (owner, Nov 2: no Worth a look, posts, avoid or also-bought).
const VIEWER_DATA_KEYS = ["version", "updated", "photos", "member_tags", "data_warning"];
async function buyList(env) {
  const o = await env.DATA.get("dashboard_data.json");
  const d = o ? await o.json() : {};
  return { d, buys: ((d.watchlist || {}).buys || []).filter(r => r.action === "BUY") };
}
async function viewerData(env, a) {
  const [{ d, buys }, port] = await Promise.all([buyList(env), r2json(env, `accounts/${a.id}/portfolio.json`, null)]);
  if (!d.updated) return json({ error: "no-file", message: "The daily update hasn't run yet" }, 404);
  const out = {};
  for (const k of VIEWER_DATA_KEYS) if (k in d) out[k] = d[k];
  const w = d.watchlist || {};
  out.watchlist = { lookback: w.lookback, updated: w.updated, buys, sells: [], new: (w.new || []).filter(n => n.action === "BUY") };
  const pol = (d.adaptive && d.adaptive.policy) || {};
  out.adaptive = { policy: { hold_other: pol.hold_other, hold_small: pol.hold_small, sizing: pol.sizing } };
  out.viewer = { hold: await familyHold(env), connected: !!a.keys_set_at, auto: a.auto !== false, size: sizeOf(a), terms: termsOk(a), paused: !!a.paused, park: parkOf(a) };
  if (port) {
    out.portfolio = port.portfolio || null;
    out.member_sold = port.member_sold || {};
    out.close_report = port.close_report || null;
    out.close_history = dayRows(port.close_history);
  }
  return json(out, 200, { "Cache-Control": "private, no-cache" });
}
// Why it moved (shown as "What's going on" in a stock's chart): only the stocks on the Buy list and the ones they hold,
// so the owner's own holdings never show up
async function viewerMovers(env, a) {
  const [o, { buys }, port] = await Promise.all([env.DATA.get("movers/latest.json"), buyList(env), r2json(env, `accounts/${a.id}/portfolio.json`, null)]);
  if (!o) return json({ error: "no-file", message: "Not built yet" }, 404);
  const m = await o.json();
  const keep = new Set([...buys.map(r => r.t), ...(((port || {}).portfolio || {}).positions || []).map(x => x.t)]);
  const items = {};
  const mine = new Set((((port || {}).portfolio || {}).positions || []).map(x => x.t)), cand = new Set(buys.map(r => r.t));
  // "held" in the owner's file means the owner holds it: replaced with theirs
  for (const [t, v] of Object.entries(m.items || {})) if (keep.has(t)) items[t] = { ...v, held: mine.has(t), cand: cand.has(t) };
  return json({ at: m.at, updated: m.updated, items }, 200, { "Cache-Control": "private, no-cache" });
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
export async function deliverTo(env, id, msg, sendAll, push = true) {
  const aid = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  const entry = { id: aid, at: new Date().toISOString(), title: String(msg.title || "").slice(0, 120), body: String(msg.body || "").slice(0, 600),
                  kind: msg.tag || "", url: msg.url || "/#port", items: (msg.items || []).slice(0, 20) };
  const key = `accounts/${id}/alerts.json`, log = await r2json(env, key, []);
  await put(env, key, [entry, ...(Array.isArray(log) ? log : [])].slice(0, 300));
  if (!push) return { sent: 0 };                // kept in their Alerts list only (they turned this kind of push off)
  const u = entry.url.includes("#") ? entry.url.replace("#", `?n=${aid}#`) : entry.url + `?n=${aid}`;
  return sendAll(env, { title: entry.title, body: entry.body, tag: msg.tag || "", url: u }, subsKey(id));
}

// ---------- first-time setup: they paste their own live Alpaca keys once ----------
// Buy size (owner, Nov 3 2026): each pick's amount for their balance, times 0.5 (low), 1 (standard) or 1.5 (high);
// the trading side reads it from the account list (broker.SIZE_MULT). Same overall limits whatever the size.
export const SIZES = { low: 0.5, standard: 1, high: 1.5 };
export const sizeOf = a => (a && SIZES[a.size] ? a.size : "standard");
async function setSize(req, env, a) {
  const b = await body(req), size = String(b.size || "");
  if (!SIZES[size]) return json({ ok: false, why: "bad-size" }, 400);
  await patchAccount(env, a.id, x => { x.size = size; });
  return json({ ok: true, size, message: size === "standard" ? "Buy size: standard." : `Buy size: ${size}. New buys use it from now on.` });
}

async function setKeys(req, env, a, h = {}) {
  if (a.keys_set_at) return json({ ok: false, why: "already-set", message: "Your account is already connected. Email the Operator if it needs to change." }, 409);
  if (!termsOk(a)) return json({ ok: false, why: "terms", message: "Please review and agree to the Terms of Use first." }, 400);
  const b = await body(req);
  if (!PARKS.includes(b.park)) return json({ ok: false, why: "park", message: "Choose what happens to your spare cash before connecting." }, 400);
  // Nov 3: a family member got "Those don't look like Alpaca keys" for keys copied from Alpaca. The check only trimmed
  // the ends, so anything a phone paste carries along (a line break or space in the middle, an invisible character,
  // the "API Key ID" / "Secret Key" label, quotes) failed it, and so did keys pasted into the wrong boxes. The pasted
  // text is now cleaned, swapped boxes are put right, and the format check is only a sanity check: Alpaca itself
  // decides whether the keys work (next step). The message says which box looks wrong, never what was typed.
  const clean = v => String(v || "").normalize("NFKC").replace(/[\u200B-\u200D\u2060\uFEFF]/g, "")
    .replace(/^\s*(api\s*)?(key\s*id|secret(\s*key)?|key)(\s*[:=]\s*|\s+)/i, "").replace(/["'`\s]/g, "");
  let key = clean(b.key).toUpperCase(), secret = clean(b.secret);
  const keyLike = v => /^[A-Z0-9]{16,40}$/i.test(v) && /^[AP]K/i.test(v);
  if (!keyLike(key) && keyLike(secret) && secret.length < key.length) [key, secret] = [secret.toUpperCase(), String(b.key ? clean(b.key) : "")];
  const kOk = /^[A-Z0-9]{12,64}$/.test(key), sOk = /^[A-Za-z0-9+/=_-]{20,128}$/.test(secret);
  if (!kOk || !sOk)
    return json({ ok: false, why: "format", message: !kOk && !sOk ? "Neither box looks like an Alpaca key. In Alpaca (Live account) open API Keys, generate a key, and copy the Key (starts with AK) into the first box and the Secret into the second."
      : !kOk ? `The first box should be the Key: about 20 letters and numbers, starting with AK. What's there is ${key.length} characters${key ? ` starting with "${key.slice(0, 2)}"` : ""}.`
      : `The second box should be the Secret: about 40 letters and numbers, shown only once when the key is made. What's there is ${secret.length} characters. If you no longer have it, make a new key in Alpaca.` }, 400);
  if (/^PK/.test(key)) return json({ ok: false, why: "paper", message: "Those are paper-trading keys. Make the keys from your real-money (Live) account." }, 400);
  const r = await client({ key, secret }).get("/v2/account");
  if (r.status === 401 || r.status === 403) return json({ ok: false, why: "rejected", message: "Alpaca didn't accept those keys. Check you copied the whole secret, from the Live account." }, 400);
  if (!r.ok || !r.data) return json({ ok: false, why: "alpaca", message: "Couldn't reach Alpaca to check the keys. Try again in a minute." }, 502);
  if (r.data.status && r.data.status !== "ACTIVE") return json({ ok: false, why: "inactive", message: `Your Alpaca account isn't open for trading yet (it says "${String(r.data.status).toLowerCase()}"). Try again once it's approved.` }, 400);
  const size = SIZES[b.size] ? b.size : sizeOf(a);
  await saveKeys(env, a.id, key, secret);
  await patchAccount(env, a.id, x => { x.keys_set_at = new Date().toISOString(); x.key_hint = "…" + key.slice(-4); x.size = size; x.park = b.park; });
  await env.DATA.put(`accounts/${a.id}/fills_state.json`, JSON.stringify({ seen: [], fresh: true }));
  // owner, Nov 3: buy the current picks right away (family_start.yml), not at the next update. If GitHub won't start
  // the job, the daily update does the same first buys on its next run (accounts.run), and the owner sees why.
  let started = false;
  const held = await familyHold(env);
  if (a.auto !== false && !a.paused && !held && h.dispatch) {
    try { await h.dispatch(env, "family_start.yml", { account: a.id }); started = true; }
    catch (e) { if (h.problem) await h.problem(env, "acct-start", `Family account first buys couldn't start right away (${String(e && e.message || e).slice(0, 100)}); the next update does them`); }
  }
  if (started && h.ok) await h.ok(env, "acct-start");
  if (held) return json({ ok: true, message: "Connected. Trading for family accounts hasn't started yet, so nothing is bought for now. You'll be able to see everything here in the meantime." });
  return json({ ok: true, message: a.auto === false ? "Connected. Automatic buying is off for this account, so nothing is bought until it's turned on."
    : started ? "Connected. Buying the current picks now. You'll get an alert as each order fills (orders placed while the market is closed fill when it opens)."
    : "Connected. Your first buys go in with the next update." });
}

// ---------- Buy more (viewers, on a stock the tool already holds for them) ----------
// The order is marked as the tool's (cs-more-…), so the extra shares are sold with the rest of the position when
// the tool sells it. Short on cash: some of the parked T-bill fund is sold first.
async function buyMore(req, env, a, maxTap) {
  if (await familyHold(env)) return json({ ok: false, why: "hold", message: HOLD_MSG }, 400);
  if (a.paused) return json({ ok: false, why: "paused", message: "trading is paused for your account. Resume it at the bottom of your Account page to buy" }, 400);
  if (!termsOk(a)) return json({ ok: false, why: "terms", message: "please agree to the Terms of Use first" }, 400);
  const c = await alpacaFor(env, a);
  if (!c) return json({ ok: false, why: "no-keys" }, 503);
  const b = await body(req);
  const t = String(b.t || "").toUpperCase(), dollars = Math.round(+b.dollars * 100) / 100;
  if (!/^[A-Z][A-Z0-9-]{0,6}$/.test(t)) return json({ ok: false, why: "bad-request" }, 400);
  if (!(dollars >= 1 && dollars <= maxTap)) return json({ ok: false, why: "amount-over-limit", cap: maxTap }, 400);
  const sym = t.replace(/-/g, ".");
  const since = new Date(Date.now() - 500 * 864e5).toISOString();
  // owner, Nov 2: only stocks on the current Buy list. The order is marked as the tool's, so it follows the same hold
  // and sell rules as the tool's own buy of that pick.
  const { d, buys } = await buyList(env);
  if (!buys.some(r => r.t === t)) return json({ ok: false, why: "not-a-pick", message: `${t} isn't on the Buy list right now` }, 400);
  const pol = (d.adaptive && d.adaptive.policy) || {}, row = buys.find(r => r.t === t);
  const hold = Math.max(5, Math.min(500, +((row && row.sm) ? pol.hold_small : pol.hold_other) || 60));
  const [asset, acct] = await Promise.all([c.get(`/v2/assets/${encodeURIComponent(sym)}`), c.get("/v2/account")]);
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
  const order = { symbol: sym, side: "buy", type: "market", time_in_force: "day", client_order_id: `${MORE_PREFIX}${t}-${reqId}`.slice(0, 40) + `-h${hold}` };
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
  const list = await listAccounts(env), hold = await familyHold(env);
  const rows = await Promise.all(list.map(async a => {
    const port = await r2json(env, `accounts/${a.id}/portfolio.json`, null), p = port && port.portfolio;
    const seen = await r2json(env, `accounts/${a.id}/seen.json`, null);
    return { ...pub(a), seen_at: seen && seen.at || null, summary: p ? { equity: p.equity, cash: p.cash, day_pl: p.day_pl, positions: (p.positions || []).length,
      invested: (p.positions || []).filter(x => x.sleeve !== "park").reduce((s, x) => s + (x.mv || 0), 0), updated: p.updated } : null,
      problem: port && port.problem || null };
  }));
  return json({ accounts: rows, hold });
}
export async function ownerAccount(env, url) {
  const a = await getAccount(env, url.searchParams.get("id"));
  if (!a) return json({ error: "not-found" }, 404);
  const port = await r2json(env, `accounts/${a.id}/portfolio.json`, null);
  return json({ account: pub(a), portfolio: port && port.portfolio || null, member_sold: port && port.member_sold || {},
                close_report: port && port.close_report || null, close_history: dayRows(port && port.close_history), problem: port && port.problem || null });
}
// the daily closes for their Pick days cards: value, that day's change and gain since start (no S&P)
function dayRows(h) {
  return (Array.isArray(h) ? h : []).slice(-400).map(x => ({ date: x.date, equity: x.equity, day_pl: x.day_pl ?? null, since: x.since ?? (x.idx != null ? x.idx - 1 : null) }));
}
export async function ownerAccountTerms(env, url) {
  const a = await getAccount(env, url.searchParams.get("id"));
  if (!a) return json({ error: "not-found" }, 404);
  return json({ terms: TERMS, signed: await r2json(env, `accounts/${a.id}/terms.json`, null) });
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
  if (act === "hold") {
    const on = b.on === true;
    await put(env, "accounts/hold.json", { on, at: new Date().toISOString() });
    return json({ ok: true, message: on ? "Family trading is on hold: every family account is view-only." : "Family trading is on: the tool trades family accounts at the next update, starting with each connected account's first buys." });
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
    // new keys may be a different Alpaca account: its first buys happen again when they reconnect
    await patchAccount(env, a.id, x => { delete x.keys_set_at; delete x.key_hint; delete x.started_at; delete x.started_how; });
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

// ---------- every 2 minutes in market hours: each person's own alerts ----------
// Trades that filled, big moves in what they hold, and their own account-value alerts, by push and/or email as
// they chose in Settings. They never get the owner's alerts (posts, Senate filings, Data health, Worth a look).
export async function checkFillsAll(env, sendAll, reasons, dispatch) {
  const list = (await listAccounts(env)).filter(a => a.status !== "disabled" && a.keys_set_at);
  if (!list.length) return;
  let why = null, movers, mailed = false;
  const fails = [];
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  for (const a of list) {
    try {
      const c = await alpacaFor(env, a);
      if (!c) { fails.push(a.label + ": keys unreadable"); continue; }
      const P = await getPrefs(env, a.id);
      const out = [];          // {kind, title, body, url, items}
      // 1. fills
      const key = `accounts/${a.id}/fills_state.json`, st = await r2json(env, key, null);
      const after = new Date(Date.now() - 4 * 864e5).toISOString();
      const [r, acct] = await Promise.all([c.get(`/v2/orders?status=closed&direction=desc&limit=200&after=${encodeURIComponent(after)}`),
        P.balance.length ? c.get("/v2/account") : Promise.resolve(null)]);
      if (!r.ok || !Array.isArray(r.data)) { fails.push(`${a.label}: Alpaca HTTP ${r.status}`); continue; }
      const filled = r.data.filter(o => o.status === "filled");
      if (!st || st.fresh) await put(env, key, { seen: filled.map(o => o.id) });
      else {
        const seen = new Set(st.seen || []), fresh = filled.filter(o => !seen.has(o.id)).reverse();
        if (fresh.length) {
          why = why || await reasons(env);
          const msgs = [];
          for (const o of fresh) {
            seen.add(o.id);
            const cid = o.client_order_id || "";
            if (cid.startsWith(PARK_PREFIX)) continue;
            const t = o.symbol.replace(/\./g, "-"), q = +o.filled_qty, px = +o.filled_avg_price;
            const sh = `${q % 1 ? q.toFixed(4).replace(/0+$/, "") : q} share${q === 1 ? "" : "s"} at $${px.toFixed(2)} ($${Math.round(q * px).toLocaleString("en-US")})`;
            const w = why[`${t}|${o.side}`];
            if (o.side === "buy" && cid.startsWith(MORE_PREFIX)) msgs.push({ t, s: `bought ${t}`, title: `Bought ${t}`, body: `Your Buy order filled: ${sh}.` });
            else if (o.side === "buy" && cid.startsWith(PREFIX)) msgs.push({ t, s: `bought ${t}`, title: `Bought ${t}`, body: sh + (w ? ` · ${w}` : "") + "." });
            else if (o.side === "sell" && cid.startsWith("tap-sell-")) msgs.push({ t, s: `sold ${t}`, title: `Sold ${t}`, body: `Your Sell order filled: ${sh}.` });
            else if (o.side === "sell" && cid.startsWith(PREFIX)) msgs.push({ t, s: `sold ${t}`, title: `Sold ${t}`, body: sh + (w ? ` · ${w}` : "") + "." });
            else msgs.push({ t, s: `${o.side === "sell" ? "sold" : "bought"} ${t}`, title: `${o.side === "sell" ? "Sold" : "Bought"} ${t}`, body: sh + "." });
          }
          await put(env, key, { seen: [...seen].slice(-1500) });
          if (msgs.length > 3) out.push({ kind: "fills", title: `${msgs.length} trades filled`, body: msgs.map(m => m.s).join(", ").replace(/^./, x => x.toUpperCase()) + ".",
            url: "/#port", items: msgs.map(m => ({ l: `${m.title}: ${m.body}`, u: `/#port/${m.t}` })) });
          else for (const m of msgs) out.push({ kind: "fills", title: m.title, body: m.body, url: `/#port/${m.t}` });
        }
      }
      // 1b. first buys still waiting for money (Nov 4: the first family account was connected before it was funded):
      // once there's cash, start them (family_start.yml), at most once an hour per account
      if (!startedOk(a) && termsOk(a) && !a.paused && a.auto !== false && dispatch) {
        const acc2 = acct && acct.ok ? acct : await c.get("/v2/account");
        if (acc2.ok && acc2.data && +acc2.data.cash >= 20) {
          const sk = `accounts/${a.id}/start_kick.json`, last = (await r2json(env, sk, {})).at || 0;
          if (Date.now() - last > 3600e3) {
            await put(env, sk, { at: Date.now() });
            try { await dispatch(env, "family_start.yml", { account: a.id }); } catch (e) { fails.push(`${a.label}: first buys couldn't start (${String(e.message || e).slice(0, 60)})`); }
          }
        }
      }
      // 2. their account-value alerts, each at most once a day
      if (acct && acct.ok && acct.data) {
        const eq = +acct.data.equity, last = +acct.data.last_equity, dayPct = last ? (eq / last - 1) * 100 : null;
        const bkey = `accounts/${a.id}/balance_state.json`, bst = await r2json(env, bkey, {});
        let changed = false;
        for (const rule of P.balance) {
          const k = `${rule.id}|${today}`;
          if (bst[k]) continue;
          const v = +rule.value;
          const hit = rule.kind === "above" ? eq >= v : rule.kind === "below" ? eq <= v
            : rule.kind === "day_up" ? dayPct != null && dayPct >= v : rule.kind === "day_down" ? dayPct != null && dayPct <= -v : false;
          if (!hit) continue;
          bst[k] = true; changed = true;
          const money = x => "$" + Math.round(x).toLocaleString("en-US");
          out.push({ kind: "balance", title: rule.kind === "above" ? `Account is above ${money(v)}` : rule.kind === "below" ? `Account is below ${money(v)}`
            : rule.kind === "day_up" ? `Account up ${dayPct.toFixed(1)}% today` : `Account down ${Math.abs(dayPct).toFixed(1)}% today`,
            body: `Your account is at ${money(eq)}${dayPct != null ? ` (${dayPct >= 0 ? "+" : ""}${dayPct.toFixed(2)}% today)` : ""}.`, url: "/#port" });
        }
        if (changed) await put(env, bkey, Object.fromEntries(Object.entries(bst).filter(([k]) => k.endsWith(today))));
      }
      // 3. big moves in what they hold (from Why it moved)
      if (P.push.moves || P.email.moves) {
        if (movers === undefined) movers = await r2json(env, "movers/latest.json", null);
        const port = await r2json(env, `accounts/${a.id}/portfolio.json`, null);
        const held = new Set(((port && port.portfolio && port.portfolio.positions) || []).filter(x => x.sleeve !== "park").map(x => x.t));
        const mkey = `accounts/${a.id}/moves_state.json`, mst = await r2json(env, mkey, { seen: [] }), seen = new Set(mst.seen || []);
        const fresh = Object.values((movers && movers.items) || {}).filter(m => held.has(m.t) && !seen.has(`${m.t}|${m.date}|${m.kind}`));
        for (const m of fresh) {
          seen.add(`${m.t}|${m.date}|${m.kind}`);
          const mv = m.move == null ? "" : ` ${m.move > 0 ? "up" : "down"} ${Math.abs(m.move).toFixed(1)}%${m.kind === "day" ? " today" : " in 5 days"}`;
          out.push({ kind: "moves", title: `${m.t}${mv}`, body: String(((m.lines || [])[1] || (m.lines || [])[0] || "")).slice(0, 300), url: `/#t/${m.t}` });
        }
        if (fresh.length) await put(env, mkey, { seen: [...seen].slice(-300) });
      }
      // deliver as they chose: push and/or email; everything is kept in their Alerts list
      for (const m of out) await deliverTo(env, a.id, { title: m.title, body: m.body, tag: m.kind === "fills" ? "fill-" + m.title : m.kind, url: m.url, items: m.items }, sendAll, !!P.push[m.kind]);
      const mail = out.filter(m => P.email[m.kind]);
      if (mail.length) {
        const html = `<div style="font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:#152033;max-width:520px">${mail.map(m =>
          `<p><b>${esc(m.title)}</b><br>${esc(m.body)}</p>`).join("")}<p style="color:#46536A;font-size:13px">Change which emails you get in the app: More → Settings.</p></div>`;
        if (!await sendMail(env, { to: a.email, subject: mail.length === 1 ? `Capitol Capital: ${mail[0].title}` : `Capitol Capital: ${mail.length} updates`, html })) mailed = true;
      }
    } catch (e) { fails.push(`${a.label}: ${String(e && e.message || e).slice(0, 80)}`); }
  }
  if (mailed && dispatch) { try { await dispatch(env, "mail.yml", {}); } catch (e) { fails.push("couldn't start the email job"); } }
  if (fails.length) throw new Error("family accounts' alerts: " + fails.join("; "));
}

// ---------- their notification settings (More → Settings) ----------
// kinds: fills (the tool's or their trades filled), close (daily market-close summary), moves (a big move in a stock
// they hold), balance (their own account-value alerts). Each can go by push, email, both or neither.
const KINDS = ["fills", "close", "moves", "balance"];
// new accounts: push on, email off; after EMAIL_ASK_DAYS of using the app they're asked once whether they want emails too
const DEF_PREFS = { push: { fills: true, close: true, moves: false, balance: true }, email: { fills: false, close: false, moves: false, balance: false }, balance: [] };
const EMAIL_ASK_DAYS = 3;
export async function getPrefs(env, id) {
  const p = await r2json(env, `accounts/${id}/prefs.json`, {});
  const out = { push: { ...DEF_PREFS.push }, email: { ...DEF_PREFS.email }, balance: [] };
  for (const k of KINDS) {
    if (typeof (p.push || {})[k] === "boolean") out.push[k] = p.push[k];
    if (typeof (p.email || {})[k] === "boolean") out.email[k] = p.email[k];
  }
  out.balance = Array.isArray(p.balance) ? p.balance : [];
  return out;
}
async function setPrefs(req, env, a) {
  const b = await body(req), cur = await getPrefs(env, a.id);
  for (const k of KINDS) {
    if (typeof (b.push || {})[k] === "boolean") cur.push[k] = b.push[k];
    if (typeof (b.email || {})[k] === "boolean") cur.email[k] = b.email[k];
  }
  if (Array.isArray(b.balance)) {
    const rules = [];
    for (const r of b.balance.slice(0, 8)) {
      const kind = String(r.kind || ""), v = +r.value;
      if (!["above", "below", "day_up", "day_down"].includes(kind) || !(v > 0) || v > 1e9) continue;
      if (kind.startsWith("day") && v > 50) continue;
      rules.push({ id: /^[a-z0-9]{6,12}$/.test(r.id || "") ? r.id : rand(6).toLowerCase().replace(/[^a-z0-9]/g, "x"), kind, value: v });
    }
    cur.balance = rules;
  }
  await put(env, `accounts/${a.id}/prefs.json`, cur);
  return json({ ok: true, prefs: cur });
}

// ---------- their own home-screen widget (same Scriptable widget as the owner's, showing their account) ----------
// The widget's key is "v.<account id>.<secret>"; only that account's summary can be read with it.
async function widgetKey(env, id, renew) {
  const k = `accounts/${id}/widget_key.txt`, o = renew ? null : await env.DATA.get(k);
  if (o) return (await o.text()).trim();
  const key = `v.${id}.${rand(24)}`;
  await env.DATA.put(k, key);
  return key;
}
export async function viewerWidget(env, given, safeEq) {
  const m = String(given || "").match(/^v\.([a-z0-9]{12})\.[A-Za-z0-9_-]{30,40}$/);
  if (!m) return null;
  const a = await getAccount(env, m[1]);
  if (!a || a.status === "disabled") return json({ error: "key" }, 401);
  const o = await env.DATA.get(`accounts/${a.id}/widget_key.txt`), key = o ? (await o.text()).trim() : "";
  if (!key || !safeEq(given, key)) return json({ error: "key" }, 401);
  const doc = await r2json(env, `accounts/${a.id}/portfolio.json`, {}), p = doc.portfolio || {}, cr = doc.close_report || {};
  const out = { at: new Date().toISOString(), equity: p.equity ?? null, day_pl: p.day_pl ?? null, market_open: !!p.market_open, live: false };
  const c = await alpacaFor(env, a);
  if (c) {
    try {
      const [acct, clock] = await Promise.all([c.get("/v2/account"), c.get("/v2/clock")]);
      if (acct.ok) { out.equity = +acct.data.equity; if (+acct.data.last_equity) out.day_pl = out.equity - +acct.data.last_equity; out.live = true; }
      if (clock.ok) out.market_open = !!clock.data.is_open;
    } catch (e) {}
  }
  if (out.equity != null && out.day_pl != null && out.equity - out.day_pl) out.day_pct = out.day_pl / (out.equity - out.day_pl) * 100;
  out.day_label = out.market_open ? "Today" : "Since the close";
  // their own gain since they started (deposits and withdrawals don't count as gains); no S&P line for them
  const hist = (doc.close_history || []).filter(h => h.equity && h.idx);
  if (hist.length && cr.since_start != null) {
    out.since = { date: cr.date, acct: cr.since_start * 100, spy: null, gain: cr.gain ?? null };
    out.spark = { acct: hist.slice(-60).map(h => (h.idx - 1) * 100), spy: [] };
  }
  const park = new Set(PARK_CHOICES), seen = new Set();
  out.buys = (p.orders || []).filter(x => x.side === "buy" && x.status === "filled" && !park.has(x.t))
    .filter(x => !seen.has(x.t) && seen.add(x.t)).slice(0, 4).map(x => ({ t: x.t, at: x.at, by: x.by === "tool" ? "tool" : "tap" }));
  return json(out);
}

// ---------- their profile picture (shown on stock charts where they bought or sold, like the owner's) ----------
// The app shrinks it to a 256px JPEG before sending, so it's small.
async function getPhoto(env, a) {
  const o = await env.DATA.get(`accounts/${a.id}/photo.jpg`);
  if (!o) return new Response("No photo", { status: 404, headers: { "Cache-Control": "no-store" } });
  return new Response(o.body, { headers: { "Content-Type": "image/jpeg", "Cache-Control": "private, max-age=86400" } });
}
async function setPhoto(req, env, a) {
  const b = await body(req);
  if (b.skip === true) { await patchAccount(env, a.id, x => { x.photo_skipped = true; }); return json({ ok: true }); }
  if (b.remove === true) {
    await env.DATA.delete(`accounts/${a.id}/photo.jpg`);
    await patchAccount(env, a.id, x => { delete x.photo_at; x.photo_skipped = true; });
    return json({ ok: true });
  }
  const m = String(b.data || "").match(/^data:image\/jpeg;base64,([A-Za-z0-9+/=]+)$/);
  if (!m || m[1].length > 400000) return json({ ok: false, message: "That picture couldn't be used. Try another one." }, 400);
  const bytes = Uint8Array.from(atob(m[1]), ch => ch.charCodeAt(0));
  if (bytes[0] !== 0xFF || bytes[1] !== 0xD8) return json({ ok: false, message: "That picture couldn't be used. Try another one." }, 400);
  await env.DATA.put(`accounts/${a.id}/photo.jpg`, bytes, { httpMetadata: { contentType: "image/jpeg" } });
  const at = new Date().toISOString();
  await patchAccount(env, a.id, x => { x.photo_at = at; });
  return json({ ok: true, photo: at });
}

// ---------- bug reports and feature ideas: emailed to the Operator (the owner) (5 a day per account, both kinds together) ----------
async function bugReport(req, env, a, dispatch) {
  const b = await body(req);
  const text = String(b.text || "").trim().slice(0, 4000), page = String(b.page || "").slice(0, 60), ua = String(req.headers.get("User-Agent") || "").slice(0, 200);
  const cf = req.cf || {}, ip = String(req.headers.get("CF-Connecting-IP") || "unknown").slice(0, 60);
  const conn = [ip, [cf.city, cf.region, cf.country].filter(Boolean).join(", "), cf.asOrganization].filter(Boolean).map(String).join(" · ").slice(0, 200);
  const idea = b.kind === "idea", what = idea ? "Feature idea" : "Bug report";
  if (text.length < 5) return json({ ok: false, message: idea ? "Tell us a little about your idea." : "Tell us a little about what happened." }, 400);
  const day = new Date().toISOString().slice(0, 10);
  if (await bump(env, `auth/rate/bug-${day}-${a.id}.json`) > 5) return json({ ok: false, message: "That's the most for today. Thanks! Try again tomorrow." }, 429);
  const html = `<div style="font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;color:#152033;max-width:560px">
<h2 style="font-weight:600;margin:0 0 8px">${what} from ${esc(a.label)}</h2>
<p style="white-space:pre-wrap;border-left:3px solid #DCE1E8;padding-left:12px">${esc(text)}</p>
<p style="color:#46536A;font-size:13px">Page: ${esc(page || "unknown")}<br>Account: ${esc(a.email)}<br>Device/browser: ${esc(ua)}<br>Connection: ${esc(conn)}<br>Sent ${esc(new Date().toISOString())}</p></div>`;
  if (!await sendMail(env, { to: "owner", subject: `Capitol Capital ${what.toLowerCase()} from ${a.label}`.slice(0, 120), html, reply_to: a.email })) {
    try { await dispatch(env, "mail.yml", {}); } catch (e) {}
  }
  return json({ ok: true, message: idea ? "Sent. Thanks for the idea." : "Sent. Thanks for letting us know." });
}


// ---------- Terms of Use (owner, Nov 3 2026) ----------
// Each person initials every section and signs with their typed full name before they can connect Alpaca, and the
// tool doesn't trade an account whose signature is missing or older than the current terms (congress_signals/
// accounts.py TERMS_V must match TERMS.v). The signed copy is kept at accounts/<id>/terms.json with the exact text
// signed, and emailed to them and to the Operator.
export const TERMS = { v: "2026-11-03", title: "Capitol Capital Terms of Use",
  intro: 'These Terms of Use (the "Terms") govern your use of Capitol Capital (the "Service"), a private software tool provided by its operator (the "Operator"). By initialing each section and signing below, you ("you") agree to these Terms.',
  sections: [
    ["The Service", "The Service selects securities based on publicly disclosed transactions by members of the U.S. Congress and, once you connect your brokerage account, places orders in that account according to its rules."],
    ["No compensation; not investment advice", "The Service is offered privately, by invitation only, and free of charge. The Operator is not a registered investment adviser or broker-dealer, receives no compensation of any kind, and nothing in the Service constitutes individualized investment, legal or tax advice."],
    ["Authorization to trade", "You authorize the Service to place, change and cancel orders in your Alpaca account through the API key you provide, including automatic purchases sized from your account balance and the buy size you select, and sales under the Service's rules, without confirming each order with you, except while trading is paused under Section 6."],
    ["Investment risk", "Investing in securities involves risk, including the loss of principal. Backtested results are hypothetical, past performance does not guarantee future results, and congressional disclosures are reported with a delay and may not anticipate price movements."],
    ["Technical risk", "The Service relies on third-party data, software and brokerage systems. Errors, delays, outages or incorrect data may cause orders to be placed, missed or timed differently than intended."],
    ["Your control", "Your account and the assets in it remain yours at all times. You may sell any position at any time. You may pause trading in the app at any time; while trading is paused, the Service will not place any buy or sell orders in your account and will hold your existing positions until you resume trading or sell them yourself. You may also revoke your API key in Alpaca, which immediately ends the Service's ability to trade."],
    ["Account visibility", "The Operator can view your account value, positions, orders and trade history as reported by Alpaca. How your information is stored and protected is described in the Privacy Policy."],
    ["Confidentiality", 'The Service\'s selections, scores, signals, alerts, analysis and other content (the "Confidential Information") are provided for your personal use only. You agree not to share, publish, sell or otherwise disclose Confidential Information to anyone who does not have an account, including by forwarding notifications or emails or sharing screenshots, and not to use it to provide advice or services to others. This does not apply to information that is publicly available, such as the congressional disclosures themselves, or to sharing your own account information with your tax, legal or financial professionals or as required by law.'],
    ["Taxes", "You are solely responsible for all taxes on activity in your account. Alpaca issues your tax forms."],
    ["Limitation of liability", 'THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE," WITHOUT WARRANTIES OF ANY KIND. TO THE FULLEST EXTENT PERMITTED BY LAW, YOU RELEASE THE OPERATOR FROM, AND AGREE THAT THE OPERATOR SHALL NOT BE LIABLE FOR, ANY AND ALL CLAIMS, LOSSES OR DAMAGES OF ANY KIND ARISING FROM OR RELATED TO YOUR USE OF THE SERVICE, INCLUDING TRADING LOSSES AND CLAIMS BASED ON THE OPERATOR\'S OWN NEGLIGENCE OR GROSS NEGLIGENCE, EXCEPT LIABILITY FOR THE OPERATOR\'S WILLFUL MISCONDUCT OR FRAUD OR LIABILITY THAT CANNOT BE WAIVED UNDER APPLICABLE LAW.'],
    ["Changes and termination", "Either party may end your use of the Service at any time. The Operator may update these Terms; you will be asked to agree to any updated Terms before the Service trades in your account again."],
    ["Entire agreement; severability", "These Terms and the Privacy Policy are the entire agreement between you and the Operator about the Service. If any part of these Terms is found unenforceable, the rest remains in effect."],
    ["Governing law", "These Terms are governed by the laws of the State of Florida."],
  ],
  attest: "By signing, you confirm that you are at least 18 years old and have read and agree to these Terms and the Privacy Policy." };
// first buys done ("0 first buys" was written for an account that had no money in it yet: not done)
export const startedOk = a => !!(a && a.started_at && a.started_how !== "0 first buys");
export function termsOk(a) { return !!(a && a.terms_at && a.terms_v === TERMS.v); }
// for their picture button when they have no photo: first and last name (the last of several last names)
function nameInitials(n) {
  const w = String(n || "").normalize("NFKC").replace(/[^\p{L}\s'-]/gu, " ").split(/\s+/).filter(Boolean);
  return w.length ? (w[0][0] + (w.length > 1 ? w[w.length - 1][0] : "")).toUpperCase() : "";
}
const initialsOf = name => name.split(/\s+/).filter(Boolean).map(w => w[0].toUpperCase()).join("").slice(0, 4);

async function signTerms(req, env, a, dispatch) {
  const b = await body(req);
  const name = String(b.name || "").normalize("NFKC").replace(/\s+/g, " ").trim().slice(0, 80);
  if (!/^\S+(\s+\S+)+$/.test(name) || name.length < 4) return json({ ok: false, message: "Type your full legal name (first and last) to sign." }, 400);
  const got = Array.isArray(b.initialed) ? b.initialed.map(Number) : [];
  if (TERMS.sections.some((_, i) => !got.includes(i + 1))) return json({ ok: false, message: "Initial every section first." }, 400);
  if (b.v !== TERMS.v) return json({ ok: false, message: "These terms were just updated. Reload the page and review them again." }, 409);
  const cf = req.cf || {};
  const rec = { v: TERMS.v, name, initials: initialsOf(name), at: new Date().toISOString(), email: a.email,
    device: String(req.headers.get("User-Agent") || "").slice(0, 200), ip: String(req.headers.get("CF-Connecting-IP") || "").slice(0, 60),
    place: [cf.city, cf.region, cf.country].filter(Boolean).join(", "), text: TERMS };
  await put(env, `accounts/${a.id}/terms.json`, rec);
  await patchAccount(env, a.id, x => { x.terms_at = rec.at; x.terms_v = TERMS.v; x.name = name; });
  // already connected (they connected before the Terms existed, or re-signed new terms): trading was on hold until
  // now, so the first buys start right away if they haven't happened yet (Nov 4)
  if (a.keys_set_at && !startedOk(a) && a.auto !== false && !a.paused && dispatch) {
    try { await dispatch(env, "family_start.yml", { account: a.id }); } catch (e) {}
  }
  const html = termsHtml(rec);
  const fmt = new Date(rec.at).toLocaleString("en-US", { timeZone: "America/New_York", dateStyle: "long", timeStyle: "short" });
  let queued = false;
  for (const to of [a.email, "owner"])
    if (!await sendMail(env, { to, subject: to === "owner" ? `Capitol Capital: ${a.label} signed the Terms of Use` : "Your signed Capitol Capital Terms of Use", html })) queued = true;
  if (queued && dispatch) { try { await dispatch(env, "mail.yml", {}); } catch (e) {} }
  return json({ ok: true, signed: rec, message: `Signed ${fmt} ET. A copy was emailed to you.` });
}

// the signed copy, as an email (also what the app shows)
export function termsHtml(rec) {
  const T = rec.text || TERMS;
  const when = new Date(rec.at).toLocaleString("en-US", { timeZone: "America/New_York", dateStyle: "long", timeStyle: "short" }) + " ET";
  return `<div style="font:15px/1.6 Georgia,'Times New Roman',serif;color:#1b1f27;max-width:620px">
<h2 style="text-align:center;font-weight:600;margin:0 0 14px">${esc(T.title)}</h2><p>${esc(T.intro)}</p>
${T.sections.map(([h, t], i) => `<p style="border-top:1px solid #e3e6eb;padding-top:8px"><b>${i + 1}. ${esc(h)}.</b> ${esc(t)} <span style="display:inline-block;border:1px solid #9aa3b2;border-radius:3px;padding:0 6px;font:600 13px system-ui,sans-serif;color:#1D4F91">${esc(rec.initials)}</span></p>`).join("")}
<p style="border-top:2px solid #1b1f27;padding-top:10px">${esc(T.attest)}</p>
<p><b>Signed:</b> <span style="font:italic 20px 'Snell Roundhand','Brush Script MT',cursive;color:#1D4F91">${esc(rec.name)}</span><br>
<b>Date:</b> ${esc(when)}<br><b>Email:</b> ${esc(rec.email || "")}</p></div>`;
}

// ---------- Pause trading (owner, Nov 3 2026) ----------
// While paused the tool places no orders at all in the account (no buys, no sells; congress_signals/accounts.py skips
// it) and its open orders are cancelled; their own Sell still works. Buy more is off until they resume.
async function setPause(req, env, a) {
  const b = await body(req), on = b.paused === true;
  await patchAccount(env, a.id, x => { if (on) { x.paused = true; x.paused_at = new Date().toISOString(); } else { delete x.paused; delete x.paused_at; } });
  let cancelled = 0;
  if (on) {
    const c = await alpacaFor(env, a);
    if (c) {
      const r = await c.get("/v2/orders?status=open&limit=200");
      // the tool's orders only: not their own Buy / Buy more orders (cs-more-, Nov 4 click-through: Pause cancelled
      // those too) nor their Sell orders (tap-sell-)
      for (const o of (r.data || []).filter(o => /^cs-/.test(o.client_order_id || "") && !(o.client_order_id || "").startsWith(MORE_PREFIX))) {
        try { const x = await c.del(`/v2/orders/${o.id}`); if (x.ok || x.status === 204) cancelled++; } catch (e) {}
      }
    }
  }
  return json({ ok: true, paused: on, cancelled,
    message: on ? `Trading paused. The tool won't buy or sell anything in your account until you resume.${cancelled ? ` ${cancelled} waiting order${cancelled === 1 ? " was" : "s were"} cancelled.` : ""}` : "Trading resumed. The tool goes back to buying and selling on its usual schedule." });
}


// ---------- spare cash: "sgov" (parked in the T-bill fund) or "cash" (left as cash); chosen when connecting ----------
// congress_signals/accounts.py passes it to broker.settings: with "cash" the tool parks nothing and sells what it had
// parked. Switching to cash here sells the parked fund right away (unless trading is paused).
const PARKS = ["sgov", "cash"];
export function parkOf(a) { return PARKS.includes(a && a.park) ? a.park : "sgov"; }
async function setPark(req, env, a) {
  const b = await body(req);
  if (!PARKS.includes(b.park)) return json({ ok: false, message: "Choose SGOV or cash." }, 400);
  await patchAccount(env, a.id, x => { x.park = b.park; });
  let sold = 0;
  if (b.park === "cash" && !a.paused && !(await familyHold(env))) {
    const c = await alpacaFor(env, a);
    if (c) {
      const since = new Date(Date.now() - 800 * 864e5).toISOString();
      const ph = await c.get(`/v2/orders?status=closed&limit=500&direction=desc&symbols=${PARK_CHOICES.join(",")}&after=${encodeURIComponent(since)}`);
      const syms = [...new Set((ph.data || []).filter(o => o.side === "buy" && o.filled_at && (o.client_order_id || "").startsWith(PARK_PREFIX)).map(o => o.symbol))];
      for (const sym of syms) {
        const pp = await c.get(`/v2/positions/${sym}`);
        if (!pp.ok || !pp.data || !(+pp.data.qty > 0)) continue;
        const r = await c.post("/v2/orders", { symbol: sym, side: "sell", qty: String(pp.data.qty), type: "market", time_in_force: "day",
          client_order_id: `${PARK_PREFIX}tocash-${Date.now()}-${sym}`.slice(0, 48) });
        if (r.ok) sold++;
      }
    }
  }
  return json({ ok: true, park: b.park, message: b.park === "cash"
    ? (a.paused ? "Spare cash will be kept as cash. Trading is paused, so the SGOV the tool holds is sold when you resume."
      : sold ? "Spare cash is now kept as cash. The SGOV the tool held is being sold (it fills now, or at the next open)." : "Spare cash is now kept as cash.")
    : "Spare cash will be parked in SGOV, starting at the next update." });
}


// ---------- their Taxes page: the same files the owner gets, from their own account (tax_report.py) ----------
const VTAX = { "statement.pdf": "application/pdf", "form8949.csv": "text/csv", "sales.json": "application/json" };
async function viewerTaxFile(env, a, url) {
  const y = String(url.searchParams.get("y") || ""), f = String(url.searchParams.get("f") || "");
  if (!/^20\d\d$/.test(y) || !VTAX[f]) return json({ error: "bad-request" }, 400);
  const o = await env.DATA.get(`accounts/${a.id}/tax/${y}/${f}`);
  if (!o) return json({ error: "no-file" }, 404);
  return new Response(o.body, { headers: { "Content-Type": VTAX[f], "Cache-Control": "private, no-store",
    ...(f === "sales.json" ? {} : { "Content-Disposition": `attachment; filename="capitol-capital-${y}-${f.replace(".", "-").replace("-pdf", ".pdf").replace("-csv", ".csv")}"` }) } });
}


// ---------- last active (owner's Accounts page: "Online now" within 5 minutes) ----------
async function noteSeen(env, a) {
  const k = `accounts/${a.id}/seen.json`, cur = await r2json(env, k, null);
  if (cur && Date.now() - Date.parse(cur.at) < 60e3) return;
  await put(env, k, { at: new Date().toISOString() });
}
