// helpers for the click-through: every /api request the page makes is logged with its body and the answer
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
export const { chromium, devices } = require("playwright");

export const BASE = "http://127.0.0.1:8787";
export const CTL = "http://127.0.0.1:8788";
export const OUT = path.join(path.dirname(new URL(import.meta.url).pathname), "out");
fs.mkdirSync(path.join(OUT, "shots"), { recursive: true });

export const ctl = async (p, body) => {
  const r = await fetch(CTL + p, body !== undefined ? { method: "POST", body: typeof body === "string" ? body : JSON.stringify(body) } : {});
  return r.json();
};
export const r2get = async k => { const j = await ctl("/r2/get?k=" + encodeURIComponent(k)); return j.missing ? null : JSON.parse(j.text); };
export const r2put = (k, v) => ctl("/r2/put?k=" + encodeURIComponent(k), JSON.stringify(v));

export const LOG = [];          // {step, kind, ...}
let STEP = "start";
export function step(name) { STEP = name; LOG.push({ step: name, kind: "step" }); console.log("== " + name); }
export function note(text, extra = {}) { LOG.push({ step: STEP, kind: "note", text, ...extra }); console.log("   " + text); }
export function problem(text, extra = {}) { LOG.push({ step: STEP, kind: "PROBLEM", text, ...extra }); console.log("   !! " + text); }

export function watch(page, who) {
  page.on("console", m => { if (["error", "warning"].includes(m.type())) LOG.push({ step: STEP, kind: "console", who, type: m.type(), text: m.text().slice(0, 500) }); });
  page.on("pageerror", e => { LOG.push({ step: STEP, kind: "pageerror", who, text: String(e && e.stack || e).slice(0, 800) }); console.log("   !! pageerror " + String(e).slice(0, 200)); });
  page.on("dialog", async d => { LOG.push({ step: STEP, kind: "dialog", who, type: d.type(), text: d.message().slice(0, 300) }); await (page.__dialog || (x => x.accept()))(d); });
  page.on("response", async res => {
    const u = new URL(res.url());
    if (!u.pathname.startsWith("/api/") && !(u.origin === BASE && res.request().resourceType() === "document")) return;
    const req = res.request();
    let body = "";
    try { body = (await res.text()).slice(0, 1500); } catch (e) { body = "(no body)"; }
    LOG.push({ step: STEP, kind: "api", who, method: req.method(), path: u.pathname + u.search, sent: (req.postData() || "").slice(0, 800), status: res.status(), got: body });
  });
}
export async function shot(page, name) {
  const f = path.join(OUT, "shots", `${path.basename(process.argv[1], ".mjs")}-${String(LOG.filter(x => x.kind === "shot").length + 1).padStart(3, "0")}-${name}.png`);
  await page.screenshot({ path: f, fullPage: true });
  LOG.push({ step: STEP, kind: "shot", file: f });
  return f;
}
export const sleep = ms => new Promise(r => setTimeout(r, ms));
export async function settle(page, ms = 600) { try { await page.waitForLoadState("networkidle", { timeout: 4000 }); } catch (e) {} await sleep(ms); }
export function save() { fs.writeFileSync(path.join(OUT, "log-" + path.basename(process.argv[1], ".mjs") + ".json"), JSON.stringify(LOG, null, 1)); }
// every visible button / link / input on the page right now (for the inventory of what a family member can tap)
export async function inventory(page) {
  return page.evaluate(() => {
    const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
    return [...document.querySelectorAll("button, a[href], input, select, textarea, summary, label.buybtn")].filter(vis).map(el => {
      const ds = Object.entries(el.dataset || {}).map(([k, v]) => `${k}=${v}`).join(" ");
      return `${el.tagName.toLowerCase()}${el.id ? "#" + el.id : ""}${el.type && el.tagName === "INPUT" ? "[" + el.type + "]" : ""} "${(el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("placeholder") || "").trim().slice(0, 50)}" ${ds}${el.disabled ? " (disabled)" : ""}${el.getAttribute("href") ? " href=" + el.getAttribute("href") : ""}`;
    });
  });
}

// ---- sign-in (Nov 4): email + password, then the 6-digit code from the (fake) email ----
export const TEST_PW = "test-password-42!";
// the newest code emailed to `to` since `since` (ms), read from the outbound log
export async function lastCode(to, since = 0) {
  for (let i = 0; i < 20; i++) {
    const mails = (await ctl("/outbound")).filter(x => x.url.includes("resend")).map(x => JSON.parse(x.body))
      .filter(m => (m.to || []).includes(to) && /is your Capitol Capital code/.test(m.subject || ""));
    const m = mails[mails.length - 1];
    const code = m && (String(m.subject).match(/^(\d{6})/) || [])[1];
    if (code && (!since || mails.length > since)) return { code, n: mails.length };
    await sleep(250);
  }
  return { code: null, n: 0 };
}
export const codeCount = async to => (await ctl("/outbound")).filter(x => x.url.includes("resend")).map(x => JSON.parse(x.body))
  .filter(m => (m.to || []).includes(to) && /is your Capitol Capital code/.test(m.subject || "")).length;
// the sign-in screen's "First time? Set up your account": email → code + new password → signed in
export async function uiSetup(page, email, pw = TEST_PW, purpose = "setup") {
  await page.goto("/?s=" + Date.now()); await settle(page, 800);
  const before = await codeCount(email);
  await page.click(`[data-go="${purpose}"]`); await page.fill("#em", email); await page.click("#go"); await settle(page, 800);
  const { code } = await lastCode(email, before);
  if (!code) return { ok: false, why: "no code emailed", msg: await page.textContent("#msg") };
  await page.fill("#cd", code); await page.fill("#np", pw); await page.fill("#np2", pw); await page.click("#go"); await settle(page, 1500);
  return { ok: !(await page.$("#f")), code, msg: (await page.$("#msg")) ? await page.textContent("#msg") : "" };
}
// "Sign in": email + password → code → signed in
export async function uiSignIn(page, email, pw = TEST_PW) {
  await page.goto("/?s=" + Date.now()); await settle(page, 800);
  if (!(await page.$("#f"))) return { ok: true, already: true };
  const before = await codeCount(email);
  await page.fill("#em", email); await page.fill("#pw", pw); await page.click("#go"); await settle(page, 800);
  const m1 = await page.textContent("#msg");
  const { code } = await lastCode(email, before);
  if (!code) return { ok: false, why: "no code emailed", msg: m1 };
  await page.fill("#cd", code); await page.click("#go"); await settle(page, 1500);
  return { ok: !(await page.$("#f")), code, msg: (await page.$("#msg")) ? await page.textContent("#msg") : "" };
}
