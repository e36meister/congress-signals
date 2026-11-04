// Part 4: Alerts, Notifications, Settings (picture, tips, widget, sign out), Report a bug, Company search, the
// "want emails too?" card, and everything a family account must NOT reach (pages, API routes, owner data).
import { chromium, devices, BASE, ctl, r2get, r2put, step, note, problem, watch, shot, settle, save, inventory, sleep, LOG } from "./lib.mjs";
import fs from "node:fs";
const acctId = fs.readFileSync("out/acct_id.txt", "utf8").trim();
const browser = await chromium.launch();
const fam = await browser.newContext({ ...devices["iPhone 13"], baseURL: BASE, storageState: "out/family_state.json" });
const p = await fam.newPage(); watch(p, "family");
const vis = async sel => (await p.$$(sel)).length;
const txt = async sel => (await p.$(sel)) ? (await p.textContent(sel)).trim().replace(/\s+/g, " ").slice(0, 400) : null;
const msgs = async () => (await p.$$eval(".tapmsg", e => e.map(x => x.innerText.trim()))).join(" | ");
const call = (path, method = "GET", body) => p.evaluate(async ([path, method, body]) => {
  const x = await fetch(path, method === "GET" ? {} : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  return [x.status, (await x.text()).slice(0, 400)]; }, [path, method, body]);
const more = async k => { await p.click("#tab-more"); await settle(p, 300); await p.click(`[data-more="${k}"]`); await settle(p, 900); };

step("family: Alerts list (2 alerts on file)");
await p.goto("/"); await settle(p, 1200);
note("bell badge: " + await p.$eval("#bell", b => b.innerText.trim() + " " + (b.dataset.n || b.getAttribute("aria-label") || "")));
await p.click("#bell"); await settle(p, 800); await shot(p, "alerts");
note("alerts shown: " + JSON.stringify(await p.$$eval("[data-alert]", a => a.map(x => x.innerText.replace(/\s+/g, " ").slice(0, 100)))));
await p.click('[data-alert="al1aaaa"]'); await settle(p, 800);
note("tap the 'Bought GS' alert → " + new URL(p.url()).hash + " · highlighted: " + await vis(".hl, [data-hl].flash, .flash"));
await shot(p, "alert-tapped");
await p.click("#bell"); await settle(p, 600);
await p.click("[data-aclear]"); await settle(p, 800);
note("Clear all → " + (await txt("main")).slice(0, 120) + " · saved: " + JSON.stringify(await r2get(`accounts/${acctId}/cleared.json`)));

step("family: Notifications settings");
await more("vnotif"); await shot(p, "notif");
await p.click('[data-vpref="email|fills"]'); await settle(p, 700);
note(`email for trades on: "${await msgs()}" · saved ${JSON.stringify((await r2get(`accounts/${acctId}/prefs.json`)).email)}`);
await p.selectOption("#vrk", "day_down"); await p.fill("#vrv", "60"); await p.click('#vrule button[type="submit"]'); await settle(p, 500);
note(`day alert 60%: "${await msgs()}"`);
await p.fill("#vrv", "5"); await p.click('#vrule button[type="submit"]'); await settle(p, 700);
await p.selectOption("#vrk", "above"); await p.fill("#vrv", "30000"); await p.click('#vrule button[type="submit"]'); await settle(p, 700);
note(`rules shown: ${JSON.stringify(await p.$$eval(".vrules li span", s => s.map(x => x.innerText)))} · saved ${JSON.stringify((await r2get(`accounts/${acctId}/prefs.json`)).balance)}`);
await p.click("[data-vruledel]"); await settle(p, 700);
note(`after Remove: ${JSON.stringify(await p.$$eval(".vrules li span", s => s.map(x => x.innerText)))}`);
note("hash on this page: " + new URL(p.url()).hash);
await p.reload(); await settle(p, 1200);
note("after reload on Notifications, the app shows: " + new URL(p.url()).hash + " / " + (await txt("main")).slice(0, 80));

step("family: Settings");
await more("vset"); await shot(p, "settings");
// a picture: make a small JPEG in the page and upload it through the file input
const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP4z8DwHwyBNAMDA4MDAwB9cAX/IuWCkgAAAABJRU5ErkJggg==", "base64");
fs.writeFileSync("out/pic.png", png);
await p.setInputFiles("#vphoto", "out/pic.png"); await settle(p, 1500);
note(`picture upload: "${await msgs()}" · saved photo: ${!!(await ctl("/r2/get?k=" + encodeURIComponent(`accounts/${acctId}/photo.jpg`))).text}`);
const ph = await call("/api/me/photo"); note("GET /api/me/photo: " + ph[0]);
p.__dialog = d => d.accept();
if (await vis("[data-vphotodel]")) { await p.click("[data-vphotodel]"); await settle(p, 800); note(`Remove picture: "${await msgs()}"`); }
await p.check("#vtips"); await settle(p, 200);
await p.click("#tab-today"); await settle(p, 500); note("tips on → Today tip shown: " + await p.$eval(".vtip", e => getComputedStyle(e).display !== "none").catch(() => "no tip element"));
await more("vset"); await p.uncheck("#vtips");
await p.click("[data-vwidget]"); await settle(p, 1200); await shot(p, "widget");
note("widget modal: " + (await p.evaluate(() => (document.querySelector("#wbg .modal") || {}).innerText || "")).replace(/\s+/g, " ").slice(0, 300));
note("widget modal buttons: " + JSON.stringify(await p.$$eval("#wbg button", b => b.map(x => x.innerText.trim()))));
const wkObj = await ctl("/r2/get?k=" + encodeURIComponent(`accounts/${acctId}/widget_key.txt`));
if (wkObj.text) {
  const wr = await fetch(BASE + "/api/widget", { headers: { "X-Widget-Key": wkObj.text.trim() } });
  note("widget read with their key: " + wr.status + " " + (await wr.text()).slice(0, 250));
  const wr2 = await fetch(BASE + "/api/widget", { headers: { "X-Widget-Key": wkObj.text.trim().slice(0, -2) + "xx" } });
  note("widget read with a wrong key: " + wr2.status);
}
await p.click("#wbg .mx").catch(() => {}); await settle(p);

step("family: Report a bug / suggest a feature");
await more("bug"); await shot(p, "bug");
await p.fill("#vbugt", "hi"); await p.click('#vbug button[type="submit"]'); await settle(p, 600);
note(`too short: "${await msgs()}"`);
await p.fill("#vbugt", "The chart didn't load on my phone"); await p.click("[data-bugkind=\"idea\"]"); await settle(p, 300);
note("switching to idea keeps the draft: " + await p.$eval("#vbugt", e => e.value));
await ctl("/outbound?clear=1");
await p.click('#vbug button[type="submit"]'); await settle(p, 900);
const bm = (await ctl("/outbound")).filter(x => x.url.includes("resend")).map(x => JSON.parse(x.body));
note(`send: "${await msgs()}" · email: ${JSON.stringify(bm.map(m => [m.to, m.subject, m.reply_to]))}`);
note("after send, textarea: '" + await p.$eval("#vbugt", e => e.value) + "'");
for (let i = 0; i < 5; i++) { const r = await call("/api/bug", "POST", { text: "test report number " + i, kind: "bug" }); if (i === 4) note("6th report today: " + r.join(" ")); }

step("family: Company search");
await more("search"); await shot(p, "search");
await p.fill("#csq", "apple"); await settle(p, 900);
note("search results for 'apple': " + JSON.stringify(await p.$$eval("main [data-chart], main .sres button", b => b.map(x => x.innerText.replace(/\s+/g, " ").slice(0, 60))).catch(() => [])));
const first = await p.$("main [data-chart]");
if (first) { await first.click(); await settle(p, 1500); await shot(p, "search-chart"); note("chart from search: " + (await p.evaluate(() => (document.querySelector(".mbg:not([hidden]) .modal") || {}).innerText || "")).replace(/\s+/g, " ").slice(0, 300)); await p.click(".mbg:not([hidden]) .mx").catch(() => {}); }
const sr = await call("/api/search?q=tes"); note("server search 'tes': " + sr.join(" ").slice(0, 300));

step("family: the 'want emails too?' card (a few days in)");
const idx = await r2get("accounts/index.json"); const me = idx.find(a => a.id === acctId);
me.first_seen = new Date(Date.now() - 4 * 864e5).toISOString(); delete me.email_asked;
await r2put("accounts/index.json", idx);
await p.goto("/#today"); await settle(p, 1200); await shot(p, "email-ask");
note("card shown: " + await vis(".vask"));
if (await vis('[data-vemail="yes"]')) { await p.click('[data-vemail="yes"]'); await settle(p, 800);
  note("yea → prefs email: " + JSON.stringify((await r2get(`accounts/${acctId}/prefs.json`)).email) + " · asked: " + (await r2get("accounts/index.json")).find(a => a.id === acctId).email_asked); }

step("family: what must stay out of reach");
for (const h of ["#works", "#record", "#defense", "#small", "#reality", "#accounts", "#tax", "#health", "#posts", "#bonds", "#settings"]) {
  await p.goto("/" + h); await settle(p, 600);
  const t = (await txt("main")).slice(0, 60);
  note(`go to ${h} → ${new URL(p.url()).hash || "(none)"} · "${t}"`);
}
for (const [path, m] of [["/api/posts"], ["/api/notes"], ["/api/bonds"], ["/api/health"], ["/api/manual"], ["/api/accounts"], ["/api/accounts/view?id=" + acctId],
  ["/api/tie-reviews"], ["/api/people-review"], ["/api/senate-import/setup"], ["/api/widget/script"], ["/api/buy", "POST"], ["/api/manual/keep", "POST"], ["/api/accounts", "POST"]]) {
  const r = await call(path, m || "GET", m ? { action: "add", label: "x", email: "x@x.test", t: "GS", dollars: 5 } : undefined);
  note(`${m || "GET"} ${path} → ${r[0]}`);
  if (r[0] !== 403) problem(`${path} answered ${r[0]} to a family account`);
}
// their own tax page is allowed (accounts/<id>/tax/): no file yet answers 404, never the owner's
{ const r = await call("/api/tax"); note(`GET /api/tax (their own) → ${r[0]}`); if (![200, 404].includes(r[0])) problem(`/api/tax answered ${r[0]}`); }
for (const f of ["/me.jpg", "/index.html", "/sw.js", "/icon.svg", "/something-else.js"]) { const r = await fetch(BASE + f, { headers: { Cookie: (await fam.cookies()).map(c => `${c.name}=${c.value}`).join("; ") } }); note(`file ${f} → ${r.status}`); }
const data = await call("/api/data");
const leaks = ["OWNER_ONLY", "OWNERSELL", "OWNERWORTH", "987654", "AVOIDME"].filter(w => data[1].includes(w) || false);
const full = await p.evaluate(async () => await (await fetch("/api/data")).text());
note("/api/data keys: " + JSON.stringify(Object.keys(JSON.parse(full))) + " · owner markers found: " + JSON.stringify(["OWNER_ONLY", "OWNERSELL", "OWNERWORTH", "987654", "AVOIDME"].filter(w => full.includes(w))));
const mv = await p.evaluate(async () => await (await fetch("/api/movers")).text());
note("/api/movers tickers: " + JSON.stringify(Object.keys(JSON.parse(mv).items)) + " · owner-held OWNERHELD present: " + mv.includes("OWNERHELD"));
const ch = await p.evaluate(async () => await (await fetch("/api/charts")).text());
note("/api/charts: " + ch.length + " bytes; tickers " + JSON.stringify(Object.keys(JSON.parse(ch).px || {})));
const nt = await call("/api/notifications"); note("/api/notifications: " + nt.join(" ").slice(0, 200));
const lv = await call("/api/live"); note("/api/live: " + lv.join(" ").slice(0, 200));

step("family: Sign out");
p.__dialog = d => d.accept();
await more("vset"); await p.click("[data-vsignout]"); await settle(p, 1500); await shot(p, "signed-out");
note("after Sign out: " + (await p.title()) + " · login form: " + await vis("#em"));
const after = await call("/api/data"); note("API after sign out: " + after[0]);
save();
await browser.close();
