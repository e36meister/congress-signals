// Local test copy of the Capitol Capital worker (cloud/worker.js) with a fake Alpaca, GitHub, Resend and
// news/company sources. Every outbound call is logged to out/outbound.jsonl; R2 is kept in ./r2 (a local file store).
import { Miniflare } from "miniflare";
import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { seed, viewerPortfolio, viewerAlerts } from "./seed.mjs";

const REPO = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const HERE = path.dirname(new URL(import.meta.url).pathname);
const OUT = path.join(HERE, "out");
fs.mkdirSync(OUT, { recursive: true });
const OUTLOG = path.join(OUT, "outbound.jsonl");

// ---------- fake Alpaca account (live) ----------
const now = new Date();
const iso = d => new Date(d).toISOString();
const daysAgo = n => iso(Date.now() - n * 864e5);
const A = {
  account: { status: "ACTIVE", equity: "25000", last_equity: "24800", cash: "1200", buying_power: "1200", currency: "USD" },
  clock: { is_open: false, next_open: iso(Date.now() + 15 * 3600e3), next_close: iso(Date.now() + 22 * 3600e3) },
  positions: [
    { symbol: "GS", cost_basis: "1560", qty: "2", qty_available: "2", avg_entry_price: "780", current_price: "805", market_value: "1610", unrealized_pl: "50", unrealized_plpc: "0.032", change_today: "0.004" },
    { symbol: "PLUG", cost_basis: "630", qty: "300", qty_available: "300", avg_entry_price: "2.1", current_price: "2.0", market_value: "600", unrealized_pl: "-30", unrealized_plpc: "-0.047", change_today: "-0.01" },
    { symbol: "SGOV", cost_basis: "20080", qty: "200", qty_available: "200", avg_entry_price: "100.4", current_price: "100.5", market_value: "20100", unrealized_pl: "20", unrealized_plpc: "0.001", change_today: "0" },
    { symbol: "AAPL", cost_basis: "1050", qty: "5", qty_available: "5", avg_entry_price: "210", current_price: "230", market_value: "1150", unrealized_pl: "100", unrealized_plpc: "0.095", change_today: "0.002" },
  ],
  orders: [
    { id: "o1", client_order_id: "cs-GS-20261101-h60", symbol: "GS", side: "buy", status: "filled", qty: "2", filled_qty: "2", filled_avg_price: "780", filled_at: daysAgo(3), submitted_at: daysAgo(3) },
    { id: "o2", client_order_id: "cs-sm-PLUG-20261101-h375", symbol: "PLUG", side: "buy", status: "filled", qty: "300", filled_qty: "300", filled_avg_price: "2.1", filled_at: daysAgo(3), submitted_at: daysAgo(3) },
    { id: "o3", client_order_id: "cs-park-20261101", symbol: "SGOV", side: "buy", status: "filled", qty: "200", filled_qty: "200", filled_avg_price: "100.4", filled_at: daysAgo(3), submitted_at: daysAgo(3) },
    { id: "o4", client_order_id: "web-aapl", symbol: "AAPL", side: "buy", status: "filled", qty: "5", filled_qty: "5", filled_avg_price: "210", filled_at: daysAgo(20), submitted_at: daysAgo(20) },
  ],
  assets: { GS: [true, true], PLUG: [true, true], SGOV: [true, true], AAPL: [true, true], NVDA: [true, true], BRK_B: [true, false], XYZ: [false, false] },
};
let nextId = 100;
const jr = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { "Content-Type": "application/json" } });

function alpaca(req, u, bodyText) {
  const p = u.pathname, m = req.method;
  const key = req.headers.get("APCA-API-KEY-ID") || "";
  if (key.startsWith("AKBAD")) return jr({ message: "forbidden" }, 403);
  if (key.startsWith("AKPENDING") && p === "/v2/account") return jr({ ...A.account, status: "SUBMITTED" });
  if (p === "/v2/account") return jr(A.account);
  if (p === "/v2/clock") return jr(A.clock);
  if (p === "/v2/positions" && m === "GET") return jr(A.positions);
  if (p.startsWith("/v2/positions/")) {
    const s = decodeURIComponent(p.split("/")[3]);
    const x = A.positions.find(q => q.symbol === s);
    return x ? jr(x) : jr({ message: "position does not exist" }, 404);
  }
  if (p.startsWith("/v2/assets/")) {
    const s = decodeURIComponent(p.split("/")[3]), a = A.assets[s.replace(".", "_")];
    return a ? jr({ symbol: s, tradable: a[0], fractionable: a[1] }) : jr({ message: "not found" }, 404);
  }
  if (p === "/v2/orders" && m === "GET") {
    const st = u.searchParams.get("status") || "open";
    const open = o => ["new", "accepted", "pending_new", "partially_filled"].includes(o.status);
    let l = A.orders.filter(o => st === "all" ? true : st === "open" ? open(o) : !open(o));
    return jr(l);
  }
  if (p === "/v2/orders" && m === "POST") {
    const b = JSON.parse(bodyText || "{}");
    const o = { id: "o" + nextId++, ...b, status: "accepted", filled_qty: "0", submitted_at: iso(Date.now()) };
    A.orders.unshift(o);
    return jr(o);
  }
  if (p.startsWith("/v2/orders/") && m === "DELETE") {
    const id = p.split("/")[3], o = A.orders.find(x => x.id === id);
    if (o) o.status = "canceled";
    return new Response(null, { status: 204 });
  }
  if (p.startsWith("/v2/account/activities")) return jr([]);
  if (p.startsWith("/v2/calendar")) return jr([]);
  return jr({ message: "fake alpaca: unhandled " + m + " " + p }, 404);
}
function alpacaData(req, u) {
  const p = u.pathname;
  if (p.endsWith("/snapshot")) return jr({ latestTrade: { p: 590 }, prevDailyBar: { c: 585 }, dailyBar: { c: 588, t: iso(Date.now()) } });
  if (p.includes("/trades/latest")) {
    const syms = (u.searchParams.get("symbols") || p.split("/")[3] || "").split(",");
    if (p.startsWith("/v2/stocks/") && !p.startsWith("/v2/stocks/trades")) return jr({ trade: { p: 100 } });
    return jr({ trades: Object.fromEntries(syms.map(s => [s, { p: 100 }])) });
  }
  if (p.includes("/bars")) {
    const syms = (u.searchParams.get("symbols") || "").split(",").filter(Boolean);
    const one = p.split("/")[3];
    const bars = [];
    for (let i = 400; i >= 0; i -= 1) { const d = new Date(Date.now() - i * 864e5); if (d.getUTCDay() % 6 === 0) continue; bars.push({ t: d.toISOString(), c: 100 + Math.sin(i / 15) * 8 + (400 - i) * 0.03, o: 100, h: 102, l: 98, v: 1e6 }); }
    if (syms.length) return jr({ bars: Object.fromEntries(syms.map(s => [s, bars])), next_page_token: null });
    return jr({ bars, symbol: one, next_page_token: null });
  }
  if (p.includes("/news")) return jr({ news: [{ headline: "Fake headline about the company", url: "https://example.com/n1", created_at: iso(Date.now() - 3600e3), source: "benzinga", summary: "A fake summary." }], next_page_token: null });
  return jr({ message: "fake alpaca data: unhandled " + p }, 404);
}

async function outbound(req) {
  const u = new URL(req.url);
  let bodyText = "";
  try { bodyText = await req.clone().text(); } catch (e) {}
  let res;
  const h = u.hostname;
  if (h === "api.alpaca.markets" || h === "paper-api.alpaca.markets") res = alpaca(req, u, bodyText);
  else if (h === "data.alpaca.markets") res = alpacaData(req, u);
  else if (h === "api.github.com") res = new Response(null, { status: 204 });
  else if (h === "api.resend.com") res = jr({ id: "fake-mail-" + nextId++ });
  else if (h.endsWith("push.apple.com") || h.endsWith("googleapis.com") || h.includes("push")) res = new Response(null, { status: 201 });
  else if (h === "query.wikidata.org") res = jr({ results: { bindings: [] } });
  else if (h.endsWith("wikipedia.org")) res = jr({ extract: "A fake company that does fake things." });
  else if (h.endsWith("yahoo.com")) res = new Response("fakecrumb", { status: 200, headers: { "set-cookie": "A3=fake; path=/" } });
  else if (h.endsWith("sec.gov")) res = jr({ name: "FAKE CO", sicDescription: "Services", filings: { recent: { form: [], filingDate: [], accessionNumber: [], primaryDocument: [], items: [] } } });
  else if (h === "news.google.com") res = new Response(`<?xml version="1.0"?><rss><channel><item><title>Fake Google News item - Source</title><link>https://example.com/g1</link><pubDate>${new Date().toUTCString()}</pubDate><source>Source</source></item></channel></rss>`, { headers: { "Content-Type": "application/rss+xml" } });
  else res = new Response("fake: no route", { status: 404 });
  const rec = { at: new Date().toISOString(), method: req.method, url: u.origin + u.pathname + (u.search ? u.search.slice(0, 200) : ""), status: res.status,
    body: bodyText ? bodyText.slice(0, 60000) : undefined };
  fs.appendFileSync(OUTLOG, JSON.stringify(rec) + "\n");
  return res;
}

const mf = new Miniflare({
  modules: true,
  modulesRoot: path.join(REPO, "cloud"),
  scriptPath: path.join(REPO, "cloud/worker.js"),
  modulesRules: [{ type: "ESModule", include: ["**/*.js"] }],
  compatibilityDate: "2026-08-06",
  r2Buckets: ["DATA"],
  r2Persist: path.join(HERE, "r2"),
  assets: { directory: path.join(REPO, "cloud/app"), binding: "ASSETS", routerConfig: { has_user_worker: true, invoke_user_worker_ahead_of_assets: true } },
  bindings: {
    APP_PASSCODE: "test-passcode-1234", ACCOUNTS_KEY: "k".repeat(48), GH_DISPATCH_TOKEN: "fake-gh", RESEND_API_KEY: "fake-resend",
    OWNER_EMAIL: "owner@example.test", TRADING_MODE: "paper", ALPACA_KEY: "PKOWNERFAKE00000000", ALPACA_SECRET: "x".repeat(40),
    MAX_TAP_DOLLARS: "25000", SEC_USER_AGENT: "test agent test@example.test",
  },
  outboundService: outbound,
  port: 8787, host: "127.0.0.1",
});
fs.rmSync(path.join(HERE, "r2"), { recursive: true, force: true });
await mf.ready;
const bucket = await mf.getR2Bucket("DATA");
await seed(bucket);
fs.writeFileSync(OUTLOG, "");
// control port for the test script: read/write R2, the fake Alpaca state, the outbound log
http.createServer(async (rq, rs) => {
  const u = new URL(rq.url, "http://x"); let b = ""; for await (const c of rq) b += c;
  const send = (o, s = 200) => { rs.writeHead(s, { "Content-Type": "application/json" }); rs.end(JSON.stringify(o)); };
  try {
    if (u.pathname === "/r2/get") { const o = await bucket.get(u.searchParams.get("k")); return send(o ? { text: await o.text() } : { missing: true }); }
    if (u.pathname === "/r2/put") { await bucket.put(u.searchParams.get("k"), b); return send({ ok: true }); }
    if (u.pathname === "/r2/list") { const l = await bucket.list({ prefix: u.searchParams.get("p") || "" }); return send(l.objects.map(o => o.key)); }
    if (u.pathname === "/r2/del") { await bucket.delete(u.searchParams.get("k")); return send({ ok: true }); }
    if (u.pathname === "/seed/viewer") { const id = u.searchParams.get("id");
      await bucket.put(`accounts/${id}/portfolio.json`, JSON.stringify(viewerPortfolio)); await bucket.put(`accounts/${id}/alerts.json`, JSON.stringify(viewerAlerts)); return send({ ok: true }); }
    if (u.pathname === "/alpaca") { if (b) Object.assign(A, JSON.parse(b)); return send({ clock: A.clock, account: A.account, orders: A.orders.slice(0, 10) }); }
    if (u.pathname === "/outbound") { const l = fs.readFileSync(OUTLOG, "utf8").trim().split("\n").filter(Boolean).map(JSON.parse); if (u.searchParams.get("clear")) fs.writeFileSync(OUTLOG, ""); return send(l); }
    if (u.pathname === "/cron") { const r = await mf.dispatchFetch("http://127.0.0.1:8787/cdn-cgi/handler/scheduled?cron=*+*+*+*+*"); return send({ status: r.status, text: await r.text() }); }
    send({ error: "?" }, 404);
  } catch (e) { send({ error: String(e && e.stack || e) }, 500); }
}).listen(8788, "127.0.0.1");
console.log("worker up on http://127.0.0.1:8787");
globalThis.MF = mf;
process.on("SIGTERM", async () => { await mf.dispose(); process.exit(0); });
