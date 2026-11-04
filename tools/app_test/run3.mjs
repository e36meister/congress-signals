// Part 3: connected account with a portfolio: Account page, Buy / Buy more, Sell, buy size, Pause; Today's Buy buttons.
import { chromium, devices, BASE, ctl, r2get, r2put, step, note, problem, watch, shot, settle, save, inventory, sleep, LOG } from "./lib.mjs";
import fs from "node:fs";
const acctId = fs.readFileSync("out/acct_id.txt", "utf8").trim();
// family trading starts on hold (view-only, Nov 3); these checks need the Buy, Sell and Pause buttons, so turn it off here
await r2put("accounts/hold.json", { on: false });
await ctl(`/seed/viewer?id=${acctId}`);
const browser = await chromium.launch();
const fam = await browser.newContext({ ...devices["iPhone 13"], baseURL: BASE, storageState: "out/family_state.json" });
const p = await fam.newPage(); watch(p, "family");
const vis = async sel => (await p.$$(sel)).length;
const txt = async sel => (await p.$(sel)) ? (await p.textContent(sel)).trim().replace(/\s+/g, " ").slice(0, 300) : null;
const msgs = async () => (await p.$$eval(".tapmsg", e => e.map(x => x.innerText.trim()))).join(" | ");
const orders = async () => (await ctl("/alpaca")).orders.filter(o => o.status === "accepted").map(o => `${o.side} ${o.symbol} ${o.notional ? "$" + o.notional : o.qty + " sh"} ${o.client_order_id}`);
const newOut = async () => (await ctl("/outbound?clear=1")).filter(x => !x.url.includes("/v2/clock")).map(x => `${x.method} ${x.url.replace(/https:\/\/(api|data)\.alpaca\.markets/, "alpaca").replace(/\?.*/, "")} ${x.status}${x.method !== "GET" && x.body ? " " + x.body.slice(0, 200) : ""}`);

step("family: Account page with a portfolio");
await p.goto("/#port"); await settle(p, 1500); await ctl("/outbound?clear=1"); await shot(p, "account");
note("Account buttons: " + JSON.stringify((await inventory(p)).filter(x => !/tab-|#bell|#refresh|#alerts/.test(x))));
const acc = await p.textContent("main");
note("Account text (start): " + acc.replace(/\s+/g, " ").slice(0, 700));
note("rows: " + JSON.stringify(await p.$$eval("tr[data-hl]", r => r.map(x => x.innerText.replace(/\s+/g, " ").slice(0, 160)))));

step("family: buy size on the Account page");
for (const k of ["low", "standard", "high", "standard"]) {
  await p.click(`[data-vsize="${k}"]`); await settle(p, 500);
  note(`size ${k}: message "${await msgs()}", saved size ${(await r2get("accounts/index.json")).find(a => a.id === acctId).size}, pressed ${await p.$eval(`[data-vsize="${k}"]`, b => b.getAttribute("aria-pressed"))}`);
}
note("per-pick estimates shown: " + (await p.textContent("main")).match(/about \$[\d,]+ a pick/g));

step("family: Buy more on a held pick (GS)");
await p.click('[data-vbuy="GS"]'); await settle(p, 400); await shot(p, "buymore-open");
note("Buy more box: " + (await txt(".sellbox")).slice(0, 300));
note("prefilled amount: " + await p.$eval("#vbuy-GS", e => e.value));
for (const amt of ["0", "100000", "50000", "100"]) {
  await ctl("/outbound?clear=1");
  if (!(await vis("#vbuy-GS"))) { await p.click('[data-vbuy="GS"]'); await settle(p, 300); }
  await p.fill("#vbuy-GS", amt); await p.click('[data-vbuysend="GS"]'); await settle(p, 900);
  note(`Buy more GS $${amt}: "${await msgs()}" · calls ${JSON.stringify(await newOut())}`);
}
note("orders now: " + JSON.stringify(await orders()));
await shot(p, "buymore-sent");

step("family: Buy on Today (candidates not held: NVDA fractionable, BRK-B whole shares, XYZ not tradable)");
await p.click("#tab-today"); await settle(p, 800); await shot(p, "today-connected");
note("Today Buy buttons: " + JSON.stringify(await p.$$eval("[data-vbuy]", b => b.map(x => x.dataset.vbuy + ":" + x.innerText))));
for (const [t, amt] of [["NVDA", "250"], ["BRK-B", "50"], ["BRK-B", "1000"], ["XYZ", "100"]]) {
  await ctl("/outbound?clear=1");
  if (!(await vis(`#vbuy-${t}`))) await p.click(`[data-vbuy="${t}"]`); await settle(p, 300);
  await p.fill(`#vbuy-${t}`, amt); await p.click(`[data-vbuysend="${t}"]`); await settle(p, 900);
  note(`Buy ${t} $${amt}: "${await msgs()}" · calls ${JSON.stringify(await newOut())}`);
}
// a stock not on the Buy list, straight to the server
let r = await p.evaluate(async () => { const x = await fetch("/api/buy-more", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t: "TSLA", dollars: 100 }) }); return [x.status, await x.text()]; });
note("server: buy TSLA (not on the list): " + JSON.stringify(r));
note("orders now: " + JSON.stringify(await orders()));

step("family: Sell");
await p.click("#tab-port"); await settle(p, 800);
note("Sell buttons: " + JSON.stringify(await p.$$eval("[data-vsell]", b => b.map(x => x.dataset.vsell))));
await p.click('[data-vsell="PLUG"]'); await settle(p, 300); await shot(p, "sell-open");
note("sell box: " + (await txt(".sellbox")).slice(0, 400));
for (const [mode, amt] of [["shares", ""], ["shares", "10"], ["dollars", "100"], ["dollars", "100000"], ["all", null]]) {
  await ctl("/outbound?clear=1");
  if (!(await vis('[data-vsellsend="PLUG"]'))) { await p.click('[data-vsell="PLUG"]'); await settle(p, 300); }
  await p.click(`[data-vsellmode="PLUG|${mode}"]`); await settle(p, 200);
  if (amt !== null) await p.fill("#vsell-PLUG", amt);
  await p.click('[data-vsellsend="PLUG"]'); await settle(p, 900);
  note(`Sell PLUG ${mode} ${amt ?? ""}: "${await msgs()}" · calls ${JSON.stringify(await newOut())}`);
}
r = await p.evaluate(async () => { const x = await fetch("/api/sell", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t: "SGOV", mode: "all" }) }); return [x.status, await x.text()]; });
note("server: sell the parked fund SGOV: " + JSON.stringify(r));
r = await p.evaluate(async () => { const x = await fetch("/api/sell", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t: "MSFT", mode: "all" }) }); return [x.status, await x.text()]; });
note("server: sell a stock not held: " + JSON.stringify(r));
await shot(p, "after-sells");

step("family: Pause trading");
await p.evaluate(() => scrollTo(0, document.body.scrollHeight));
p.__dialog = d => d.dismiss();
await p.click("[data-vpause]"); await settle(p, 500);
note(`pause, tapped Cancel on the question: checked=${await p.$eval("[data-vpause]", e => e.checked)} saved=${!!(await r2get("accounts/index.json")).find(a => a.id === acctId).paused}`);
p.__dialog = d => d.accept();
// an open tool order to be cancelled, plus one of theirs that must stay
await ctl("/alpaca", { orders: [{ id: "oT1", client_order_id: "cs-NVDA-20261104-h60", symbol: "NVDA", side: "buy", status: "accepted", notional: "100", submitted_at: new Date().toISOString() },
  { id: "oT2", client_order_id: "tap-sell-AAPL-1", symbol: "AAPL", side: "sell", status: "accepted", qty: "1", submitted_at: new Date().toISOString() }, ...(await ctl("/alpaca")).orders] });
await ctl("/outbound?clear=1");
await p.click("[data-vpause]"); await settle(p, 900); await shot(p, "paused");
note(`pause ON: "${await msgs()}" · calls ${JSON.stringify(await newOut())}`);
note("banner: " + await txt(".pausebar"));
note("Buy more buttons while paused: " + await vis("[data-vbuy]") + ", Sell buttons: " + await vis("[data-vsell]"));
await p.click("#tab-today"); await settle(p, 600);
note("Today while paused: banner " + await vis(".pausebar") + ", Buy buttons " + await vis("[data-vbuy]"));
r = await p.evaluate(async () => { const x = await fetch("/api/buy-more", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t: "GS", dollars: 100 }) }); return [x.status, await x.text()]; });
note("server: Buy more while paused: " + JSON.stringify(r));
await p.click(".pausebar [data-tab-go]"); await settle(p, 700);
note("banner Resume link → tab " + new URL(p.url()).hash + ", scrolled to bottom: " + await p.evaluate(() => Math.round(scrollY + innerHeight) >= document.body.scrollHeight - 5));
await p.click("[data-vpause]"); await settle(p, 800);
note(`pause OFF: "${await msgs()}" · saved paused=${!!(await r2get("accounts/index.json")).find(a => a.id === acctId).paused}`);
await shot(p, "resumed");

step("family: reload keeps things (Pause state, size) and the live refresh");
await p.reload(); await settle(p, 2000);
note("after reload: " + (await txt(".filters")));
fs.writeFileSync("out/family_state.json", JSON.stringify(await fam.storageState()));
save();
await browser.close();
