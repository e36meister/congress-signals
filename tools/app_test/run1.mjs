// Part 1: owner adds a family account; the family member signs in from the emailed link and goes through every
// first-time screen, the Terms, and Connect (every kind of bad key, then a good one).
import { chromium, devices, BASE, ctl, r2get, step, note, problem, watch, shot, settle, save, inventory, sleep, LOG } from "./lib.mjs";
import fs from "node:fs";

const browser = await chromium.launch();
const iphone = devices["iPhone 13"];

// ---- owner ----
step("owner: passcode login and add a family account");
const own = await browser.newContext({ ...iphone, baseURL: BASE });
const op = await own.newPage(); watch(op, "owner");
await op.goto("/");
let r = await op.evaluate(async () => (await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ passcode: "test-passcode-1234" }) })).status);
note("owner login status " + r);
await ctl("/outbound?clear=1");
r = await op.evaluate(async () => { const x = await fetch("/api/accounts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "add", label: "Test Mom", email: "testmom@example.test" }) }); return { s: x.status, j: await x.json() }; });
note("add account: " + JSON.stringify(r));
const acctId = r.j.account.id;
fs.writeFileSync("out/acct_id.txt", acctId);
const mails = (await ctl("/outbound")).filter(x => x.url.includes("resend"));
const link = (mails.map(m => JSON.parse(m.body).html).join(" ").match(/https?:\/\/[^"']+\/signin#[A-Za-z0-9_-]+/) || [])[0];
note("sign-in link emailed: " + (link ? "yes" : "NO"), { mails: mails.length });
if (!link) { problem("no sign-in link found in the email"); save(); process.exit(1); }
const token = link.split("#")[1];

// ---- family member: not signed in ----
step("family: app before signing in");
const fam = await browser.newContext({ ...iphone, baseURL: BASE });
const p = await fam.newPage(); watch(p, "family");
await p.goto("/"); await settle(p); await shot(p, "login-page");
note("login page buttons: " + JSON.stringify(await inventory(p)));
// asking for a link with the account's email and with an unknown one: same answer
for (const em of ["testmom@example.test", "nobody@example.test", "bad-email"]) {
  await p.fill("#em", em); await p.click("#go"); await sleep(700);
  note(`email link request for ${em}: "${await p.textContent("#msg")}"`);
}
await shot(p, "login-after-email");

step("family: open the emailed link and tap Sign in");
await p.goto("/signin#" + token); await settle(p); await shot(p, "signin-page");
note("signin page url after load (token must be hidden): " + p.url());
await p.click("#go"); await settle(p, 1500);
note("after Sign in, url: " + p.url());
await shot(p, "first-screen");
// the same link a second time must fail
const p2 = await fam.newPage(); watch(p2, "family-2nd-tab");
await p2.goto("/signin#" + token); await p2.click("#go"); await sleep(800);
note(`re-using the link: "${await p2.textContent("#msg")}"`); await p2.close();

step("family: first screens (profile picture step)");
note("visible: " + JSON.stringify(await inventory(p)));
await shot(p, "photo-step");
await p.click("[data-vphotoskip]"); await settle(p, 1000);
await shot(p, "after-photo-skip");
note("visible after skip: " + JSON.stringify((await inventory(p)).slice(0, 40)));

const ck = (await fam.cookies()).find(c => c.name);
fs.writeFileSync("out/family_state.json", JSON.stringify(await fam.storageState()));
await own.storageState({ path: "out/owner_state.json" });
save();
await browser.close();
console.log("account", acctId);
