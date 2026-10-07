// Web Push for the Capitol Capital phone app: VAPID keys (made once, kept in R2), subscriptions,
// message encryption (RFC 8291, aes128gcm) and sending. Used by worker.js.

const enc = new TextEncoder();
export const b64u = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
export const unb64u = s => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4)), c => c.charCodeAt(0));
const concat = (...a) => { const out = new Uint8Array(a.reduce((n, x) => n + x.length, 0)); let o = 0; for (const x of a) { out.set(x, o); o += x.length; } return out; };

export async function vapid(env) {
  const o = await env.DATA.get("push/vapid.json");
  if (o) return o.json();
  const kp = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
  const v = { priv: await crypto.subtle.exportKey("jwk", kp.privateKey), pub: b64u(await crypto.subtle.exportKey("raw", kp.publicKey)) };
  await env.DATA.put("push/vapid.json", JSON.stringify(v));
  return v;
}

export async function getSubs(env) {
  const o = await env.DATA.get("push/subs.json");
  return o ? o.json() : [];
}
export async function saveSub(env, sub, origin) {
  if (!sub || !/^https:\/\//.test(sub.endpoint || "") || !sub.keys?.p256dh || !sub.keys?.auth) throw new Error("bad subscription");
  const subs = (await getSubs(env)).filter(s => s.endpoint !== sub.endpoint);
  subs.push({ endpoint: sub.endpoint, keys: { p256dh: sub.keys.p256dh, auth: sub.keys.auth }, added: new Date().toISOString() });
  await env.DATA.put("push/subs.json", JSON.stringify(subs.slice(-10)));
  await env.DATA.put("push/origin.txt", origin);
}

async function hkdf(salt, ikm, info, bits) {
  const k = await crypto.subtle.importKey("raw", ikm, "HKDF", false, ["deriveBits"]);
  return new Uint8Array(await crypto.subtle.deriveBits({ name: "HKDF", hash: "SHA-256", salt, info }, k, bits));
}

async function encrypt(sub, payload) {
  const uaPub = unb64u(sub.keys.p256dh), auth = unb64u(sub.keys.auth);
  const as = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
  const asPub = new Uint8Array(await crypto.subtle.exportKey("raw", as.publicKey));
  const uaKey = await crypto.subtle.importKey("raw", uaPub, { name: "ECDH", namedCurve: "P-256" }, false, []);
  const shared = new Uint8Array(await crypto.subtle.deriveBits({ name: "ECDH", public: uaKey }, as.privateKey, 256));
  const ikm = await hkdf(auth, shared, concat(enc.encode("WebPush: info\0"), uaPub, asPub), 256);
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const cek = await hkdf(salt, ikm, enc.encode("Content-Encoding: aes128gcm\0"), 128);
  const nonce = await hkdf(salt, ikm, enc.encode("Content-Encoding: nonce\0"), 96);
  const key = await crypto.subtle.importKey("raw", cek, "AES-GCM", false, ["encrypt"]);
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, key, concat(enc.encode(payload), new Uint8Array([2]))));
  const rs = new Uint8Array([0, 0, 16, 0]);                  // record size 4096
  return concat(salt, rs, new Uint8Array([asPub.length]), asPub, ct);
}

async function vapidHeader(env, endpoint) {
  const v = await vapid(env);
  const origin = (await (await env.DATA.get("push/origin.txt"))?.text()) || "https://capitol-capital.workers.dev";
  const head = b64u(enc.encode(JSON.stringify({ typ: "JWT", alg: "ES256" })));
  const body = b64u(enc.encode(JSON.stringify({ aud: new URL(endpoint).origin, exp: Math.floor(Date.now() / 1000) + 12 * 3600, sub: origin })));
  const key = await crypto.subtle.importKey("jwk", v.priv, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
  const sig = b64u(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, enc.encode(head + "." + body)));
  return `vapid t=${head}.${body}.${sig}, k=${v.pub}`;
}

// msg: {title, body, tag, url}. Sends to every saved phone; drops phones that unsubscribed.
export async function sendAll(env, msg) {
  const subs = await getSubs(env);
  if (!subs.length) return { sent: 0, subs: 0 };
  const payload = JSON.stringify({ title: msg.title, body: msg.body || "", tag: msg.tag || "", url: msg.url || "/" }).slice(0, 3000);
  let sent = 0, gone = [];
  for (const s of subs) {
    try {
      const r = await fetch(s.endpoint, { method: "POST", body: await encrypt(s, payload), headers: {
        Authorization: await vapidHeader(env, s.endpoint), TTL: "86400", Urgency: "high",
        "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream" } });
      if (r.status === 404 || r.status === 410) gone.push(s.endpoint);
      else if (r.ok) sent++;
    } catch (e) {}
  }
  if (gone.length) await env.DATA.put("push/subs.json", JSON.stringify(subs.filter(s => !gone.includes(s.endpoint))));
  return { sent, subs: subs.length };
}
