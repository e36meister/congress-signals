// Part 1: owner adds a family account; the family member gets the invite, sets up a password with an emailed code
// (Nov 4: email + password + code replaced the emailed sign-in link), signs in, and goes through the first screens.
import { chromium, devices, BASE, ctl, r2get, step, note, problem, watch, shot, settle, save, inventory, sleep, LOG,
         TEST_PW, lastCode, codeCount, uiSetup, uiSignIn } from "./lib.mjs";
import fs from "node:fs";

const browser = await chromium.launch();
const iphone = devices["iPhone 13"];
// a fresh hour of sign-in tries (the rate limits are kept in R2 between runs)
for (const k of await ctl("/r2/list?p=" + encodeURIComponent("auth/rate/"))) await ctl("/r2/del?k=" + encodeURIComponent(k));

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
const mails = (await ctl("/outbound")).filter(x => x.url.includes("resend")).map(x => JSON.parse(x.body));
const invite = mails.find(m => /invited/i.test(m.subject || ""));
note("invite emailed: " + (invite ? `yes ("${invite.subject}")` : "NO"), { mails: mails.length });
if (!invite) { problem("no invite email"); save(); process.exit(1); }
if (/signin#/.test(invite.html)) problem("the invite still carries a sign-in link");

// ---- family member: not signed in ----
const EM = "testmom@example.test";
step("family: sign-in screen before setting up");
const fam = await browser.newContext({ ...iphone, baseURL: BASE });
const p = await fam.newPage(); watch(p, "family");
await p.goto("/"); await settle(p); await shot(p, "login-page");
note("login page buttons: " + JSON.stringify(await inventory(p)));
await p.fill("#em", EM); await p.fill("#pw", TEST_PW); await p.click("#go"); await sleep(700);
note(`sign in before a password is set: "${await p.textContent("#msg")}"`);
if (await codeCount(EM)) problem("a code was emailed before the account had a password");

step("family: Set up your account — unknown email gets the same answer");
await p.click("#back").catch(() => {});
await p.click('[data-go="setup"]'); await p.fill("#em", "nobody@example.test"); await p.click("#go"); await settle(p, 700);
const unknownHint = await p.textContent("#hint");
note(`setup with an unknown email: "${unknownHint.slice(0, 90)}…"`);
await p.click("#back");

step("family: Set up — weak password refused, then a good one");
await p.click('[data-go="setup"]'); await p.fill("#em", EM); await p.click("#go"); await settle(p, 800);
const knownHint = await p.textContent("#hint");
if (knownHint.replace(/\S+@\S+/, "") !== unknownHint.replace(/\S+@\S+/, "")) problem("setup answers differently for a real and an unknown email");
const { code } = await lastCode(EM);
note("setup code emailed: " + (code ? "yes" : "NO"));
if (!code) { problem("no setup code"); save(); process.exit(1); }
await p.fill("#cd", code); await p.fill("#np", "short"); await p.fill("#np2", "short"); await p.click("#go"); await settle(p, 700);
note(`weak password: "${await p.textContent("#msg")}"`);
await p.fill("#np", TEST_PW); await p.fill("#np2", TEST_PW + "x"); await p.click("#go"); await settle(p, 500);
note(`passwords don't match: "${await p.textContent("#msg")}"`);
await p.fill("#np2", TEST_PW); await p.click("#go"); await settle(p, 1500);
note("after setting the password, url: " + p.url() + (await p.$("#f") ? " (still on the sign-in screen!)" : ""));
if (await p.$("#f")) problem("setting the password didn't sign in: " + await p.textContent("#msg"));
await shot(p, "first-screen");
// the same code a second time must fail
const reuse = await p.evaluate(async c => { const r = await fetch("/api/auth/verify", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cid: "x".repeat(24), code: c }) }); return r.status; }, code);
note("a code with a made-up id: " + reuse);

step("family: sign out and back in with password + code");
const fam2 = await browser.newContext({ ...iphone, baseURL: BASE });
const p2 = await fam2.newPage(); watch(p2, "family-2nd-device");
await p2.goto("/"); await p2.fill("#em", EM); await p2.fill("#pw", "wrong-password-1"); await p2.click("#go"); await sleep(700);
note(`wrong password: "${await p2.textContent("#msg")}"`);
const s2 = await uiSignIn(p2, EM);
note("second device signs in: " + JSON.stringify({ ok: s2.ok, msg: s2.msg }));
if (!s2.ok) problem("password + code sign-in failed");
await p2.close(); await fam2.close();

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
