// Part 9 (Nov 11 2026): Initial investment sell, short disclaimer links in the Buy/Sell boxes, Blog section links.
import { chromium, devices, BASE, r2get, r2put, ctl, step, note, problem, watch, shot, settle, save } from "./lib.mjs";
import fs from "node:fs";
const acctId = fs.readFileSync("out/acct_id.txt", "utf8").trim();
const browser = await chromium.launch();
const fam = await browser.newContext({ ...devices["iPhone 13"], baseURL: BASE, storageState: "out/family_state.json" });
const p = await fam.newPage(); watch(p, "family");
const vis = async sel => (await p.$$(sel)).length;
const inView = sel => p.evaluate(s => { const r = document.querySelector(s)?.getBoundingClientRect(); return r ? Math.round(r.top) : null; }, sel);
const setAcct = async patch => { const ix = await r2get("accounts/index.json"); Object.assign(ix.find(a => a.id === acctId), patch); await r2put("accounts/index.json", ix); };
await r2put("accounts/hold.json", { on: false }); await setAcct({ paused: false });

step("sell box: short disclaimer link + Initial investment");
await p.goto("/#port"); await settle(p, 1500); await p.reload(); await settle(p, 2500);
await p.click('[data-vsell="GS"]'); await settle(p, 400);
note("sell box text: " + (await p.textContent(".sellbox")).replace(/\s+/g, " ").slice(0, 220));
await p.click('[data-vsellmode="GS|initial"]'); await settle(p, 300); await shot(p, "gs-initial");
note("GS initial: " + (await p.textContent(".sellbox")).replace(/\s+/g, " ").slice(0, 300) + " · send disabled " + await vis('[data-vsellsend="GS"][disabled]'));
await p.click('[data-vsellsend="GS"]'); await settle(p, 1200);
note("after send: " + (await p.textContent("main")).match(/Sell order sent[^.]*\./)?.[0]);
const st = await ctl("/alpaca");
note("GS sell order at fake Alpaca: " + JSON.stringify((st.orders || []).find(o => /tap-sell-GS/.test(o.client_order_id || "")) || st.orders?.[0]));
await p.click('[data-vsell="PLUG"]'); await settle(p, 300); await p.click('[data-vsellmode="PLUG|initial"]'); await settle(p, 300);
note("PLUG initial (down): " + (await p.textContent(".sellbox")).replace(/\s+/g, " ").slice(100, 260) + " · send disabled " + await vis('[data-vsellsend="PLUG"][disabled]'));
const r = await p.evaluate(async () => (await fetch("/api/sell", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t: "PLUG", mode: "initial" }) })).json());
note("server, PLUG initial: " + JSON.stringify(r));
const y0 = await p.evaluate(() => { const b = document.querySelector('.sellbox [data-blog="buttons/sell-yourself"]'); b.scrollIntoView({ block: "center" }); const y = scrollY; b.click(); return y; }); await settle(p, 1200); await shot(p, "sell-disclaimer");
note(`disclaimer link → ${await p.evaluate(() => location.hash)} · section top at ${await inView("#blog-sell-yourself")}px`);
await p.click("[data-blogback]"); await settle(p, 1000);
note(`Back → ${await p.evaluate(() => location.hash)} y ${await p.evaluate(() => scrollY)} (was ${y0})`);

step("buy box: short disclaimer link");
await p.goto("/#today"); await settle(p, 1500);
await p.click('[data-vbuy]'); await settle(p, 400);
note("buy box: " + (await p.textContent(".sellbox")).replace(/\s+/g, " ").slice(0, 200));
await p.click('.sellbox [data-blog="buttons/buy-yourself"]'); await settle(p, 1000);
note(`→ ${await p.evaluate(() => location.hash)} · section top ${await inView("#blog-buy-yourself")}px`);

step("links inside posts jump to sections");
await p.click('#blog-buy-yourself [data-blog="sizing/test"]'); await settle(p, 1000); await shot(p, "sizing-test");
note(`→ ${await p.evaluate(() => location.hash)} · section top ${await inView("#blog-test")}px · back label ${await p.textContent("[data-blogback]")}`);
await p.click("[data-blogback]"); await settle(p, 900);
note(`Back → ${await p.evaluate(() => location.hash)} · buy-yourself top ${await inView("#blog-buy-yourself")}px`);
await p.evaluate(() => document.querySelector('#blog-buy-yourself [data-blog="how/sells"]').click()); await settle(p, 1200); await shot(p, "how-sells");
note(`→ ${await p.evaluate(() => location.hash)} · sells top ${await inView("#blog-sells")}px`);
note("How post: " + (await p.textContent("main")).replace(/\s+/g, " ").slice(0, 500));
const links = await p.$$eval("main [data-blog]", b => b.map(x => x.dataset.blog));
note("links on How post: " + links.length + " " + JSON.stringify([...new Set(links)]));
for (const to of [...new Set(links)]) {
  await p.goto("/#blog/" + to); await settle(p, 900);
  const [k, sec] = to.split("/");
  const ok = sec ? await inView("#blog-" + sec) : await vis("main h2");
  if (ok == null) problem("link target missing: " + to); 
}
note("every link target exists");
await browser.close(); save();
