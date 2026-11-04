// Part 7 (Nov 4): the owner's yea/nay buttons on Worth a look post links: save, undo, context label, family can't use them.
import { chromium, devices, BASE, ctl, r2get, r2put, step, note, problem, watch, shot, settle, save } from "./lib.mjs";
await r2put("inputs/post_reviews.json", {});     // start with no answers saved

const browser = await chromium.launch();
const own = await browser.newContext({ ...devices["iPhone 13"], baseURL: BASE });
const op = await own.newPage(); watch(op, "owner");
await op.goto("/");
await op.evaluate(async () => fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ passcode: "test-passcode-1234" }) }));
await op.goto("/?o=7#worth/OWNERWORTH"); await settle(op, 1800); await shot(op, "owner-worth-review");

step("owner: buttons and context label");
const n = await op.locator("#today-worth [data-prv]").count();
note(`yea/nay buttons on Worth a look: ${n}`);
if (n !== 4) problem(`expected 4 buttons (2 posts × yea/nay), got ${n}`);
const txt = (await op.textContent("#today-worth")).replace(/\s+/g, " ");
if (!/Context · /.test(txt)) problem("industry-only post isn't labeled Context");

step("owner: tap Nay on the first post, then tap it again to undo");
await op.locator('#today-worth [data-prv="nay"]').first().click(); await settle(op, 800);
let rev = (await r2get("inputs/post_reviews.json")) || {};
const k = "OWNERWORTH|T000001|https://example.test/post-1";
note("saved: " + JSON.stringify(rev[k] || null));
if (rev[k]?.verdict !== "nay") problem("Nay wasn't saved");
if (!(await op.locator('#today-worth [data-prv="nay"].on').count())) problem("Nay doesn't show as chosen");
await op.locator('#today-worth [data-prv="nay"]').first().click(); await settle(op, 800);
rev = (await r2get("inputs/post_reviews.json")) || {};
if (rev[k]) problem("tapping Nay again didn't undo it");
await op.locator('#today-worth [data-prv="yea"]').nth(1).click(); await settle(op, 800);
rev = (await r2get("inputs/post_reviews.json")) || {};
const k2 = "OWNERWORTH|T000001|https://example.test/post-2";
note("second post: " + JSON.stringify(rev[k2] || null));
if (rev[k2]?.verdict !== "yea" || rev[k2]?.ctx !== true) problem("Yea on the context post wasn't saved with ctx");

step("server: bad input is refused");
const bad = await op.evaluate(async () => {
  const post = b => fetch("/api/post-review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }).then(r => r.status);
  return [await post({ t: "x;drop", u: "https://a.test/1", verdict: "yea" }), await post({ t: "AAPL", u: "javascript:alert(1)", verdict: "yea" }),
          await post({ t: "AAPL", u: "https://a.test/1", verdict: "maybe" })];
});
note("bad inputs: " + bad.join(", "));
if (bad.some(s => s !== 400)) problem("a bad post-review input wasn't refused");

step("family: post reviews are out of reach");
const fam = await browser.newContext({ baseURL: BASE, storageState: "out/family_state.json" }).catch(() => null);
if (fam) {
  const fp = await fam.newPage();
  await fp.goto("/");
  const s = await fp.evaluate(async () => [(await fetch("/api/post-reviews")).status,
    (await fetch("/api/post-review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ t: "AAPL", u: "https://a.test/1", verdict: "yea" }) })).status]);
  note("family GET/POST: " + s.join(", "));
  if (s.some(x => x !== 403)) problem("a family account reached the post reviews");
} else note("no family session saved (run1 first) — family check skipped");
save();
await browser.close();
