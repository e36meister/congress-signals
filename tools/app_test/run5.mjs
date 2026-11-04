// Part 5: the owner's Accounts page for this family account (every button), and what the family member sees after each.
import { chromium, devices, BASE, ctl, r2get, r2put, step, note, problem, watch, shot, settle, save, inventory, sleep, LOG } from "./lib.mjs";
import fs from "node:fs";
const acctId = fs.readFileSync("out/acct_id.txt", "utf8").trim();
const browser = await chromium.launch();
const own = await browser.newContext({ ...devices["iPhone 13"], baseURL: BASE, storageState: "out/owner_state.json" });
const op = await own.newPage(); watch(op, "owner");
const fam = await browser.newContext({ ...devices["iPhone 13"], baseURL: BASE, storageState: "out/family_state.json" });
const p = await fam.newPage(); watch(p, "family");
const msgs = async pg => (await pg.$$eval(".tapmsg", e => e.map(x => x.innerText.trim()))).join(" | ");
const famSees = async () => { await p.goto("/?r=" + Date.now() + "#port"); await settle(p, 1200); return (await p.textContent("body")).replace(/\s+/g, " ").slice(0, 160); };
const acct = async () => (await r2get("accounts/index.json")).find(a => a.id === acctId);
const tap = async (act, dialog = "accept") => {
  op.__dialog = d => dialog === "accept" ? d.accept(dialog === "accept" && d.type() === "prompt" ? "testmom@example.test" : undefined) : d.dismiss();
  await op.click(`[data-acc="${act}"][data-id="${acctId}"]`); await settle(op, 900);
};

step("owner: Accounts page");
await op.goto("/#more"); await settle(op, 1000);
await op.evaluate(() => localStorage.setItem("ctd-tab", "accounts")); await op.goto("/?o=2"); await settle(op, 1800); await shot(op, "owner-accounts");
note("owner Accounts buttons: " + JSON.stringify((await inventory(op)).filter(x => /data-acc|acc|accadd|accl|acce|Add/.test(x))));
note("card: " + (await op.$eval("article.acct", e => e.innerText.replace(/\s+/g, " ").slice(0, 300)).catch(() => "no card")));

step("owner: View portfolio and Signed terms");
await op.click(`[data-accview="${acctId}"]`); await settle(op, 1500); await shot(op, "owner-view");
note("owner view text: " + (await op.textContent("main")).replace(/\s+/g, " ").slice(0, 300));
note("owner view buttons: " + JSON.stringify((await inventory(op)).filter(x => !/tab-|#bell|#refresh|#alerts|#mebtn/.test(x)).slice(0, 30)));
await op.click("[data-accback]"); await settle(op, 800);
await op.click(`[data-accterms="${acctId}"]`); await settle(op, 1200); await shot(op, "owner-terms");
note("owner signed-terms view: " + (await op.textContent("main")).replace(/\s+/g, " ").slice(0, 200));
await op.click("[data-accback]"); await settle(op, 800);

step("owner: Pause auto-buying → family sees");
await tap("auto"); note(`owner: "${await msgs(op)}" · auto=${(await acct()).auto}`);
note("family sees: " + await famSees());
await shot(p, "family-auto-off");
await tap("auto"); note(`owner turns it back on: "${await msgs(op)}" · auto=${(await acct()).auto}`);

step("owner: Send sign-in link, Sign out everywhere");
await tap("link"); note(`owner: "${await msgs(op)}"`);
await tap("signout"); note(`owner: "${await msgs(op)}"`);
note("family after Sign out everywhere: " + await famSees());
await shot(p, "family-signed-out-everywhere");

// family signs in again for the next checks
const signInAgain = async () => {
  await ctl("/outbound?clear=1");
  await op.evaluate(async id => fetch("/api/accounts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "link", id }) }), acctId);
  const html = (await ctl("/outbound")).filter(x => x.url.includes("resend")).map(x => JSON.parse(x.body).html).join("");
  const t = (html.match(/signin#([A-Za-z0-9_-]+)/) || [])[1];
  await p.goto("/signin#" + t); await p.click("#go"); await settle(p, 1200);
};
await signInAgain();
note("family signed in again: " + await famSees());

step("owner: Reset keys → family sees");
await tap("reset-keys", "dismiss"); note(`owner Cancel on the question: keys still set ${!!(await acct()).keys_set_at}`);
await tap("reset-keys"); note(`owner: "${await msgs(op)}" · keys file left: ${!!(await r2get(`accounts/${acctId}/keys.json`))}`);
note("family sees: " + await famSees());
await shot(p, "family-after-reset");
const a1 = await acct();
note("account after reset: started_at=" + a1.started_at + " size=" + a1.size + " terms=" + !!a1.terms_at);

step("owner: Turn off → family; Turn back on");
await tap("disable"); note(`owner: "${await msgs(op)}"`);
note("family sees: " + await famSees());
const r = await p.evaluate(async () => (await fetch("/api/login/email", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: "testmom@example.test" }) })).status);
note("family asks for a link while turned off: " + r + " · emails sent: " + (await ctl("/outbound")).filter(x => x.url.includes("resend")).length);
await tap("enable"); note(`owner: "${await msgs(op)}" · status ${(await acct()).status}`);

step("owner: Remove (wrong email typed, then right)");
op.__dialog = d => d.accept("wrong@example.test");
await op.click(`[data-acc="remove"][data-id="${acctId}"]`); await settle(op, 900); note(`wrong email: "${await msgs(op)}"`);
op.__dialog = d => d.accept("TestMom@Example.test ");
await op.click(`[data-acc="remove"][data-id="${acctId}"]`); await settle(op, 900); note(`right email (different case, space): "${await msgs(op)}"`);
note("files left for the account: " + JSON.stringify(await ctl("/r2/list?p=" + encodeURIComponent(`accounts/${acctId}/`))));
note("family sees: " + await famSees());
save();
await browser.close();
