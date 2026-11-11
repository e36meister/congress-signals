// Part 8 (Nov 11 2026): greyed-out Buy/Sell with the info button, "Trading paused" link, and the Blog.
import { chromium, devices, BASE, r2get, r2put, step, note, problem, watch, shot, settle, save, sleep } from "./lib.mjs";
import fs from "node:fs";
const acctId = fs.readFileSync("out/acct_id.txt", "utf8").trim();
const browser = await chromium.launch();
const fam = await browser.newContext({ ...devices["iPhone 13"], baseURL: BASE, storageState: "out/family_state.json" });
const p = await fam.newPage(); watch(p, "family");
const vis = async sel => (await p.$$(sel)).length;
const setAcct = async patch => { const ix = await r2get("accounts/index.json"); Object.assign(ix.find(a => a.id === acctId), patch); await r2put("accounts/index.json", ix); };

step("hold on (trading not started): Today");
await r2put("accounts/hold.json", { on: true }); await setAcct({ paused: false });
await p.goto("/#today"); await settle(p, 1500); await p.reload(); await settle(p, 2500); await shot(p, "hold-today");
note(`disabled Buy ${await vis(".buybtn.vb[disabled]")} · active Buy ${await vis("[data-vbuy]")} · info ${await vis(".infobtn")} · Trading paused label ${await vis(".vpausedl")}`);
await p.evaluate(() => scrollTo(0, 400)); const y0 = await p.evaluate(() => scrollY);
await p.click(".infobtn"); await settle(p, 900); await shot(p, "hold-blog");
const t1 = await p.textContent("main"); note("post: " + t1.replace(/\s+/g, " ").slice(0, 260)); if (!t1.includes("hasn't started")) problem("hold post line missing");
note("hash " + await p.evaluate(() => location.hash));
await p.click("[data-blogback]"); await settle(p, 900);
note(`back → tab ${await p.evaluate(() => location.hash + " " + (document.querySelector("main h2")?.textContent||""))} y ${await p.evaluate(() => scrollY)} (was ${y0})`);
step("hold on: Account");
await p.goto("/#port"); await settle(p, 1500); await shot(p, "hold-port");
note(`disabled Buy more ${await vis(".buybtn.vb[disabled]")} · disabled Sell ${await vis(".vsell[disabled]")} · active Sell ${await vis("[data-vsell]")} · info ${await vis(".infobtn")}`);

step("their own pause on: Today and Account");
await r2put("accounts/hold.json", { on: false }); await setAcct({ paused: true, paused_at: new Date().toISOString() });
await p.goto("/#today"); await settle(p, 1500);
note("server viewer: " + JSON.stringify(await p.evaluate(async () => (await (await fetch("/api/data")).json()).viewer)));
await p.reload(); await settle(p, 2500); await shot(p, "paused-today");
note(`disabled Buy ${await vis(".buybtn.vb[disabled]")} · Trading paused label ${await vis(".vpausedl")} · info ${await vis(".infobtn")}`);
await p.click(".vpausedl"); await settle(p, 1200); await shot(p, "paused-link");
note(`after Trading paused tap: tab ${await p.evaluate(() => location.hash + " " + (document.querySelector("main h2")?.textContent||""))} · pause panel in view ${await p.evaluate(() => { const r = document.querySelector("#vpause")?.getBoundingClientRect(); return r ? r.top < innerHeight && r.bottom > 0 : null; })}`);
note(`Account while paused: disabled Buy more ${await vis(".buybtn.vb[disabled]")} · active Sell ${await vis("[data-vsell]")} · info ${await vis(".infobtn")}`);
await p.click(".infobtn"); await settle(p, 900);
note("post: " + (await p.textContent("main")).replace(/\s+/g, " ").slice(0, 200));
await p.click('[data-blog="sizing"]'); await settle(p, 900); await shot(p, "sizing");
note("sizing: " + (await p.textContent("main")).replace(/\s+/g, " ").slice(0, 600));
await p.click("[data-blogback]"); await settle(p, 800); note("back from sizing → " + await p.evaluate(() => location.hash));

step("normal trading: no info buttons; Blog from More");
await setAcct({ paused: false });
await p.goto("/#today"); await settle(p, 1500); await p.reload(); await settle(p, 2500);
note(`active Buy ${await vis("[data-vbuy]")} · info ${await vis(".infobtn")}`);
await p.goto("/#more"); await settle(p, 900); await p.click('[data-more="blog"]'); await settle(p, 800); await shot(p, "blog-list");
note("blog list: " + (await p.textContent("main")).replace(/\s+/g, " ").slice(0, 200));
await p.click('[data-blog="buttons"]'); await settle(p, 800); note("normal post: " + (await p.textContent("main")).replace(/\s+/g, " ").slice(0, 120));
await p.click("[data-blogback]"); await settle(p, 600); note("back → " + await p.evaluate(() => location.hash + " " + document.querySelector("main h2")?.textContent));
await p.click("[data-blogback]"); await settle(p, 600); note("back → " + await p.evaluate(() => location.hash + " " + (document.querySelector("main h2")?.textContent||"")));
await browser.close(); save();
