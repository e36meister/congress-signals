// Part 10 (Nov 11 2026): Sell as a popup with a confirm step (owner and family); family Buy more as a popup.
import { chromium, devices, BASE, r2get, r2put, ctl, step, note, problem, watch, shot, settle, save } from "./lib.mjs";
import fs from "node:fs";
const acctId = fs.readFileSync("out/acct_id.txt", "utf8").trim();
const browser = await chromium.launch();
const sellOrders = async t => ((await ctl("/alpaca")).orders || []).filter(o => o.side === "sell" && o.symbol === t).length;
const box = async p => (await p.$(".sellmodal")) ? (await p.textContent(".sellmodal")).replace(/\s+/g, " ").trim() : null;
const wide = p => p.evaluate(() => { const m = document.querySelector(".sellmodal")?.getBoundingClientRect(); return m ? [Math.round(m.left), Math.round(m.right), innerWidth] : null; });

for (const who of ["owner", "family"]) {
  const ctx = await browser.newContext({ ...devices["iPhone 13"], baseURL: BASE, storageState: `out/${who}_state.json` });
  const p = await ctx.newPage(); watch(p, who);
  const S = who === "owner" ? { sell: "data-sell", send: "data-sellsend", go: "data-sellgo", back: "data-sellback", mode: "data-sellmode", amt: "#samt-GS" }
                            : { sell: "data-vsell", send: "data-vsellsend", go: "data-vsellgo", back: "data-vsellback", mode: "data-vsellmode", amt: "#vsell-GS" };
  step(`${who}: Sell opens a popup`);
  await p.goto("/#port"); await settle(p, 1500); await p.reload(); await settle(p, 2500);
  await p.click(`[${S.sell}="GS"]`); await settle(p, 500); await shot(p, who + "-sell-popup");
  note(`popup: ${await box(p)} · box left/right/screen ${JSON.stringify(await wide(p))} · page scrollWidth ${await p.evaluate(() => document.documentElement.scrollWidth)}`);
  await p.click(`[${S.mode}="GS|shares"]`); await settle(p, 300); await p.fill(S.amt, "1"); 
  if (who === "owner") await p.dispatchEvent(S.amt, "input");
  const before = await sellOrders("GS");
  await p.click(`[${S.send}="GS"]`); await settle(p, 400); await shot(p, who + "-confirm");
  note(`confirm step: ${await box(p)} · orders sent so far ${await sellOrders("GS") - before}`);
  await p.click(`[${S.back}]`); await settle(p, 300); note(`Back → amount kept: ${await p.inputValue(S.amt).catch(() => "?")}`);
  await p.click(`[${S.send}="GS"]`); await settle(p, 300); await p.click(`[${S.go}="GS"]`); await settle(p, 1500);
  note(`after Confirm: popup open ${!!(await p.$(".sellmodal"))} · new sell orders ${await sellOrders("GS") - before} · msg: ${(await p.textContent("main")).match(/Sell order (placed|sent)[^.]*\./)?.[0]}`);
  step(`${who}: close by tapping outside and with Escape`);
  await p.click(`[${S.sell}="GS"]`); await settle(p, 400); await p.mouse.click(5, 300); await settle(p, 300); note("tap outside → open " + !!(await p.$(".sellmodal")));
  await p.click(`[${S.sell}="GS"]`); await settle(p, 400); await p.keyboard.press("Escape"); await settle(p, 300); note("Escape → open " + !!(await p.$(".sellmodal")));
  if (who === "family") {
    step("family: Buy more popup");
    await p.click('[data-vbuy="GS"]'); await settle(p, 400); await shot(p, "family-buymore");
    note(`Buy more popup: ${await box(p)}`);
    await p.click("[data-popclose]"); await settle(p, 300); note("× → open " + !!(await p.$(".sellmodal")));
  }
  await ctx.close();
}
await browser.close(); save();
