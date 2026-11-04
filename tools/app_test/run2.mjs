// Part 2: alerts step, Today, a chart, Members, Alerts, More pages before connecting; Terms; Connect.
import { chromium, devices, BASE, ctl, r2get, r2put, step, note, problem, watch, shot, settle, save, inventory, sleep, LOG } from "./lib.mjs";
import fs from "node:fs";
const acctId = fs.readFileSync("out/acct_id.txt", "utf8").trim();
const browser = await chromium.launch();
const fam = await browser.newContext({ ...devices["iPhone 13"], baseURL: BASE, storageState: "out/family_state.json" });
const p = await fam.newPage(); watch(p, "family");
const vis = async sel => (await p.$$(sel)).length;
const txt = async sel => (await p.$(sel)) ? (await p.textContent(sel)).trim().replace(/\s+/g, " ").slice(0, 300) : null;

step("family: app opens (photo step was skipped, alert step next)");
await p.goto("/"); await settle(p, 1200); await shot(p, "open");
if (await vis("[data-valertskip]")) { note("alert step shown: tapping Not now"); await p.click("[data-valertskip]"); await settle(p); }
else note("alert step not shown (this browser can't do push alerts)");
await shot(p, "today");
note("Today buttons: " + JSON.stringify(await inventory(p)));
const todayText = await p.textContent("main");
for (const w of ["OWNERSELL", "AVOIDME", "OWNERWORTH", "Worth a look", "Needs review", "Suggested"]) if (todayText.includes(w)) problem(`Today shows "${w}" to a family account`);
note("Buy candidates shown: " + JSON.stringify(await p.$$eval("[data-chart]", b => [...new Set(b.map(x => x.dataset.chart))])));

step("family: tap a ticker on Today (chart popup)");
await p.click('[data-chart="GS"]'); await settle(p, 1500); await shot(p, "chart-GS");
note("chart popup buttons: " + JSON.stringify(await inventory(p)));
const modalText = await p.evaluate(() => (document.querySelector(".mbg:not([hidden]) .modal") || {}).innerText || "");
note("chart popup text starts: " + modalText.slice(0, 400).replace(/\n+/g, " | "));
const labels = await p.$$eval(".mbg:not([hidden]) .modal button", bs => bs.map(b => b.innerText.trim()).filter(l => l && !/^(×|Close)$/.test(l)));
for (const label of labels) {
  const loc = p.locator(".mbg:not([hidden]) .modal button", { hasText: label.split("\n")[0] }).first();
  try { await loc.click({ timeout: 2000 }); await settle(p, 700); note(`tapped "${label.split("\n")[0]}" in the chart popup → popup still open: ${await vis(".mbg:not([hidden]) .modal")}`); }
  catch (e) { note(`couldn't tap "${label.split("\n")[0]}": ${String(e).split("\n")[0].slice(0, 90)}`); }
  if (!(await vis(".mbg:not([hidden]) .modal"))) { await p.click('[data-chart="GS"]'); await settle(p, 900); }
}
await shot(p, "chart-GS-after-taps");
await p.click(".mbg:not([hidden]) .mx").catch(() => p.keyboard.press("Escape")); await settle(p);

step("family: Members tab");
await p.click("#tab-members"); await settle(p, 1000); await shot(p, "members");
note("Members buttons: " + JSON.stringify((await inventory(p)).slice(0, 30)));
if (await vis("[data-mem]")) { await p.click("[data-mem]"); await settle(p, 1200); await shot(p, "member-page"); note("member page buttons: " + JSON.stringify((await inventory(p)).slice(0, 40))); }
await p.locator("[data-mback]").first().click().catch(() => {}); await settle(p);
if (await vis("[data-mem=\"P000197\"]")) { await p.click("[data-mem=\"P000197\"]"); await settle(p, 1200); await shot(p, "member-page-2"); note("member page (with data) buttons: " + JSON.stringify((await inventory(p)).filter(x => !/tab-|#bell|#refresh|#alerts/.test(x)).slice(0, 40))); await p.locator("[data-mback]").first().click().catch(() => {}); await settle(p); }

step("family: Alerts (bell)");
await p.click("#bell"); await settle(p, 800); await shot(p, "alerts-empty"); note("alerts: " + (await txt("main")));

step("family: More menu and each page");
await p.click("#tab-more"); await settle(p); await shot(p, "more");
note("More items: " + JSON.stringify(await inventory(p)));
for (const k of ["search", "vnotif", "vset", "faq", "bug", "terms", "privacy"]) {
  await p.click("#tab-more"); await settle(p, 300);
  const b = await p.$(`[data-more="${k}"]`);
  if (!b) { problem(`More has no "${k}" item`); continue; }
  await b.click(); await settle(p, 900); await shot(p, "more-" + k);
  note(`More → ${k}: url ${new URL(p.url()).hash}; buttons ${JSON.stringify((await inventory(p)).filter(x => !/tab-|#bell|#refresh|#alerts/.test(x)).slice(0, 25))}`);
}

step("family: Account tab before signing the terms");
await p.click("#tab-port"); await settle(p, 1000); await shot(p, "account-terms");
note("Agree button disabled at first: " + await p.$eval("[data-tsign]", b => b.disabled));
await p.click('[data-tini="1"]'); await p.click('[data-tini="2"]'); await settle(p, 300);
await p.fill("#tname", "Testmom"); await sleep(200);
note("2 initials + one-word name, Agree disabled: " + await p.$eval("[data-tsign]", b => b.disabled));
// try the server directly with missing initials / one name / old version (what a tampered page could send)
for (const body of [{ v: "2026-11-03", name: "Test Mom", initialed: [1, 2] }, { v: "2026-11-03", name: "Testmom", initialed: [...Array(13)].map((_, i) => i + 1) },
                    { v: "2025-01-01", name: "Test Mom", initialed: [...Array(13)].map((_, i) => i + 1) }]) {
  const r = await p.evaluate(async b => { const x = await fetch("/api/me/terms", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }); return { s: x.status, j: await x.json() }; }, body);
  note(`server check, sending ${JSON.stringify(body).slice(0, 80)}…: ${r.s} ${r.j.message}`);
}
const nSec = await p.$$eval("[data-tini]", b => b.length);
for (let i = 3; i <= nSec; i++) await p.click(`[data-tini="${i}"]`);
await p.fill("#tname", "Test Q Mom"); await sleep(300);
note("all initialed + full name, Agree disabled: " + await p.$eval("[data-tsign]", b => b.disabled));
note("initials shown on boxes: " + JSON.stringify(await p.$$eval(".tini.on", b => [...new Set(b.map(x => x.textContent))])));
await shot(p, "terms-ready");
await ctl("/outbound?clear=1");
await p.click("[data-tsign]"); await settle(p, 1500); await shot(p, "after-sign");
const mailsT = (await ctl("/outbound")).filter(x => x.url.includes("resend")).map(x => JSON.parse(x.body));
note(`terms emails sent: ${mailsT.length} → ${JSON.stringify(mailsT.map(m => [m.to, m.subject]))}`);
const signed = await r2get(`accounts/${acctId}/terms.json`);
note("saved signature: " + JSON.stringify({ v: signed && signed.v, name: signed && signed.name, initials: signed && signed.initials, ip: signed && signed.ip, device: signed && signed.device && signed.device.slice(0, 30) }));
note("page now: " + (await txt("main")).slice(0, 300));

step("family: Connect page, buy size before connecting");
note("Connect buttons: " + JSON.stringify(await inventory(p)));
await p.click('[data-vsize="high"]'); await settle(p, 300);
note("size buttons pressed: " + JSON.stringify(await p.$$eval("[data-vsize]", b => b.map(x => x.dataset.vsize + "=" + x.getAttribute("aria-pressed")))));
const tries = [
  ["empty boxes", "", ""],
  ["junk", "hello", "world"],
  ["paper keys", "PKABCDEFGHIJKLMNOPQR", "s".repeat(40)],
  ["key with label and line break, secret pasted with spaces", "API Key ID: AKBADXXXX\nXXXXXXXX11", "Secret Key: abcd efgh ijkl mnop qrst uvwx yz12 3456 7890 ABCD"],
  ["swapped boxes, rejected by Alpaca", "abcdefghijklmnopqrstuvwxyz0123456789ABCD", "AKBADAAAAAAAAAAAAAAA"],
  ["account not approved yet", "AKPENDING00000000000", "p".repeat(40)],
  ["good keys", "AKGOODKEY00000000000", "g".repeat(40)],
];
for (const [label, k, s] of tries) {
  await ctl("/outbound?clear=1");
  await p.fill("#vk", k); await p.fill("#vs", s);
  await p.click('#vkeys button[type="submit"]'); await settle(p, 1200);
  const msg = await txt(".tapmsg");
  const boxes = await p.evaluate(() => [document.querySelector("#vk")?.value || "", (document.querySelector("#vs")?.value || "").length]);
  const ob = (await ctl("/outbound")).map(x => `${x.method} ${x.url.replace(/\?.*/, "")} ${x.status}${x.url.includes("github") ? " " + x.body : ""}`);
  note(`Connect, ${label}: "${msg}" · boxes after: key="${boxes[0]}" secret ${boxes[1]} chars · server called: ${JSON.stringify(ob)}`);
  await shot(p, "connect-" + label.split(" ")[0]);
  if (!(await p.$("#vk"))) break;
}
const idx = await r2get("accounts/index.json");
const me = idx.find(a => a.id === acctId);
note("account after connect: " + JSON.stringify({ size: me.size, keys: !!me.keys_set_at, hint: me.key_hint, terms_v: me.terms_v }));
const kj = await r2get(`accounts/${acctId}/keys.json`);
note("keys file fields: " + JSON.stringify(Object.keys(kj || {})) + " (contains plain key? " + JSON.stringify(kj).includes("AKGOOD") + ")");
note("page now: " + (await txt("main")).slice(0, 400));
await shot(p, "connected-no-portfolio");
note("Connected page buttons: " + JSON.stringify(await inventory(p)));
fs.writeFileSync("out/family_state.json", JSON.stringify(await fam.storageState()));
save();
await browser.close();
