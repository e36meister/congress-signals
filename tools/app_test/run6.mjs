// Part 6: re-checks of the fixes: an account with no money, Pause keeps their own orders, Resume link scroll,
// reload on a More page, messages cleared between pages, signing the terms when already connected starts first buys.
import { chromium, devices, BASE, ctl, r2get, r2put, step, note, problem, watch, shot, settle, save, inventory, sleep } from "./lib.mjs";
import { viewerPortfolio } from "./seed.mjs";
import fs from "node:fs";
const acctId = fs.readFileSync("out/acct_id.txt", "utf8").trim();
const browser = await chromium.launch();
const fam = await browser.newContext({ ...devices["iPhone 13"], baseURL: BASE, storageState: "out/family_state.json" });
const p = await fam.newPage(); watch(p, "family");
const msgs = async () => (await p.$$eval(".tapmsg", e => e.map(x => x.innerText.trim()))).join(" | ");

step("re-check: account with no money in it");
const empty = JSON.parse(JSON.stringify(viewerPortfolio));
Object.assign(empty.portfolio, { equity: 0, cash: 0, day_pl: null, positions: [], orders: [] }); empty.portfolio.limits.per_pick = 0;
await r2put(`accounts/${acctId}/portfolio.json`, empty);
const keepA = (await ctl("/alpaca")).account; await ctl("/alpaca", { account: { ...keepA, equity: "0", last_equity: "0", cash: "0", buying_power: "0" }, positions: [] });
await p.goto("/?a=1#port"); await settle(p, 1500); await shot(p, "unfunded");
note("unfunded note: " + ((await p.$eval(".note.warn", e => e.innerText).catch(() => "none")) || "").replace(/\s+/g, " "));
note("positions panel: " + (await p.$eval("section.panel .empty", e => e.innerText).catch(() => "none")));

await ctl("/alpaca", { account: keepA });
step("re-check: Pause keeps their own Buy-more and Sell orders");
await ctl(`/seed/viewer?id=${acctId}`);
await ctl("/alpaca", { orders: [{ id: "oX1", client_order_id: "cs-NVDA-20261104-h60", symbol: "NVDA", side: "buy", status: "accepted" },
  { id: "oX2", client_order_id: "cs-more-GS-1-h60", symbol: "GS", side: "buy", status: "accepted" },
  { id: "oX3", client_order_id: "tap-sell-AAPL-1", symbol: "AAPL", side: "sell", status: "accepted" }] });
await p.goto("/?a=2#port"); await settle(p, 1500);
p.__dialog = d => d.accept(); await ctl("/outbound?clear=1");
await p.click("[data-vpause]"); await settle(p, 1000);
note(`pause: "${await msgs()}" · cancelled: ${JSON.stringify((await ctl("/outbound")).filter(x => x.method === "DELETE").map(x => x.url.split("/").pop()))}`);

step("re-check: banner Resume link scrolls to the switch");
await p.click("#tab-today"); await settle(p, 600);
await p.click(".pausebar [data-tab-go]"); await settle(p, 900);
note("Pause switch on screen: " + await p.$eval("[data-vpause]", e => { const r = e.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }));
await shot(p, "resume-link");
await p.click("[data-vpause]"); await settle(p, 900);

step("re-check: messages cleared when leaving the page");
note("on Account: " + await msgs());
await p.click("#tab-more"); await settle(p, 300); await p.click("#tab-port"); await settle(p, 600);
note("back on Account: '" + await msgs() + "'");

step("re-check: reload on More → Notifications");
await p.click("#tab-more"); await settle(p, 300); await p.click('[data-more="vnotif"]'); await settle(p, 800);
note("hash: " + new URL(p.url()).hash);
await p.reload(); await settle(p, 1500);
note("after reload: " + new URL(p.url()).hash + " · " + (await p.textContent("main")).replace(/\s+/g, " ").slice(0, 60));

step("re-check: signing the terms when already connected starts the first buys");
const idx = await r2get("accounts/index.json"); const me = idx.find(a => a.id === acctId);
delete me.terms_at; delete me.terms_v; me.started_at = "2026-11-03T23:07:26+00:00"; me.started_how = "0 first buys";
await r2put("accounts/index.json", idx); await ctl("/outbound?clear=1");
await p.goto("/?a=3#port"); await settle(p, 1500);
const n = await p.$$eval("[data-tini]", b => b.length);
for (let i = 1; i <= n; i++) await p.click(`[data-tini="${i}"]`);
await p.fill("#tname", "Test Q Mom"); await sleep(200); await p.click("[data-tsign]"); await settle(p, 1500);
note("calls when signing: " + JSON.stringify((await ctl("/outbound")).map(x => x.method + " " + x.url.replace(/\?.*/, "") + (x.url.includes("github") ? " " + x.body : ""))));
note("page after signing: " + (await p.textContent("main")).replace(/\s+/g, " ").slice(0, 160));
save();
await browser.close();
