// Test data shaped like the real R2 files (shapes from tools/viewer_schema_dump.py, Nov 4). Owner-only fields carry
// OWNER_ONLY markers, so a leak to the family account is easy to spot in the captured responses.
const day = n => new Date(Date.now() - n * 864e5).toISOString().slice(0, 10);
const now = new Date().toISOString();

const row = (t, co, extra = {}) => ({ rank: 1, action: "BUY", t, co, pct: 97, m: "Nancy Pelosi (D-CA)", why: "proven member; cluster", d: day(2),
  move: 1.2, td: day(20), mt: 3.1, an: "buy", up: 8.5, earn: day(-20), news: 2, sec: "Financials", nwm: 0, def: 0, cap: 15e10, dodm: 0,
  ppl: [{ n: "Nancy Pelosi", r: "D-CA", b: "P000197", tr: 12.3, tn: 40 }], px: 100, sz: 1, ags: 0, sm: 0, lag: 18, nc: 0, s: 0.91, ...extra });

export const dashboard = {
  version: 2, updated: now, photos: {}, member_tags: { names: { "Nancy Pelosi": "D-CA" }, bio: { P000197: "D-CA" } }, data_warning: null,
  watchlist: { updated: now, lookback: 45,
    buys: [row("GS", "Goldman Sachs"), row("NVDA", "NVIDIA", { sec: "Technology" }), row("PLUG", "Plug Power", { sm: 1, cap: 2e9 }),
           row("BRK-B", "Berkshire Hathaway B"), row("XYZ", "Untradable Co"),
           { ...row("AVOIDME", "Avoid Co"), action: "AVOID" }],
    sells: [{ ...row("OWNERSELL", "Owner Sell Signal"), action: "SELL", OWNER_ONLY: "sells list" }],
    new: [{ action: "BUY", t: "GS" }, { action: "SELL", t: "OWNERSELL" }] },
  adaptive: { policy: { hold_other: 60, hold_small: 375, hold_sleeve: 375, sizing: "equal", price_exit: "none", extend: "none" }, OWNER_ONLY: "adaptive history" },
  small: { slots: 25, OWNER_ONLY: "small backtest" },
  portfolio: { equity: 987654, OWNER_ONLY: "owner portfolio" }, close_report: { OWNER_ONLY: "owner close" }, worth: [{ t: "OWNERWORTH", OWNER_ONLY: 1 }],
  source_health: { OWNER_ONLY: "health" }, backtest: { OWNER_ONLY: "backtest" },
};
export const members = { updated: now, members: [
  { k: "P000197", n: "Nancy Pelosi", tag: "D-CA", ch: "House", nt: 40, buys: 30, sells: 10, first: day(2000), last: day(20), bx3m: 4.1, bx1y: 12.3, bhit1y: 0.6, sx3m: -1, sx1y: 2, tickers: 25 },
  { k: "T000278", n: "Tommy Tuberville", tag: "R-AL", ch: "Senate", nt: 120, buys: 70, sells: 50, first: day(1500), last: day(5), bx3m: -2.0, bx1y: 3.3, bhit1y: 0.5, sx3m: 1, sx1y: -1, tickers: 80 } ] };
const tcols = ["d", "td", "t", "ty", "lo", "hi", "own", "s", "x60", "x125", "co", "sec", "why", "lag", "pct", "ret"];
export const member1 = { k: "P000197", b: "P000197", n: "Nancy Pelosi", tag: "D-CA", ch: "House", photo: "", stats: members.members[0],
  tcols, trades: [[day(2), day(20), "GS", "buy", 1001, 15000, "SP", 0.91, 3.2, null, "Goldman Sachs", "Financials", "proven member", 18, 97, 0.03]],
  ecols: ["d", "k", "t", "x", "tk", "u"], events: [[day(30), "", "hr", "Hearing on banking", "GS", ""]], posts: [[day(3), "x", "Fake post", "", "", 0, ""]], pprof: null, ties: [] };
const dates = []; for (let i = 300; i >= 0; i--) { const d = new Date(Date.now() - i * 864e5); if (d.getUTCDay() % 6) dates.push(d.toISOString().slice(0, 10)); }
const series = b => dates.map((_, i) => +(b * (1 + Math.sin(i / 20) * 0.05 + i * 0.0004)).toFixed(2));
const ctr = t => [{ n: "Nancy Pelosi", b: "P000197", ty: "buy", td: day(20), d: day(2), p: 100, lo: 1001, hi: 15000, own: "SP", dl: 1001, dh: 15000 }];
export const charts = { updated: now, dates, px: { GS: series(780), NVDA: series(120), PLUG: series(2), "BRK-B": series(450), AAPL: series(220) },
  trades: { GS: ctr("GS"), NVDA: ctr("NVDA"), PLUG: ctr("PLUG"), AAPL: ctr("AAPL") }, photos: {} };
const mv = (t, co, held, cand) => ({ t, co, date: day(0), kind: "day", move: 4.2, spy: 0.3, rel: 3.9, held, cand,
  lines: [`${t} rose 4.2% today`, "Why: a fake headline", "Context line"], news: [{ h: "Fake headline", src: "Reuters", u: "https://example.com/x", at: now }],
  analysts: [], filings: [], earnings: null, peers: [[1.2, "MS"]], etf: [[0.5, "XLF"]], industry: "Banks", members: [], first: day(0), at: now });
export const movers = { updated: now, items: { GS: mv("GS", "Goldman Sachs", true, true), OWNERHELD: mv("OWNERHELD", "Owner Holding Co", true, false), TSLA: mv("TSLA", "Tesla", false, false) } };
export const searchIndex = { updated: now, list: [["GS", "Goldman Sachs Group", 40], ["NVDA", "NVIDIA Corp", 30], ["AAPL", "Apple Inc", 5]] };
export const tradesByTicker = { updated: now, cols: ["d", "td", "n", "b", "ty", "lo", "hi", "own"], trades: { AAPL: [[day(5), day(30), "Nancy Pelosi", "P000197", "buy", 1001, 15000, "SP"]] } };
export const secNames = { at: now, list: [["AAPL", "Apple Inc"], ["GS", "Goldman Sachs Group"], ["TSLA", "Tesla Inc"]] };

// what the daily run saves for a connected family account (broker.sync snapshot), matching the fake Alpaca account
export const viewerPortfolio = { portfolio: {
  mode: "live", auto: true, updated: now, market_open: false, next_open: new Date(Date.now() + 15 * 3600e3).toISOString(), equity: 25000, cash: 1200, day_pl: 200,
  limits: { dollars: null, max_positions: null, max_invested_pct: 90, hold_days: 60, hold_small: 375, per_week: 5, from_policy: true, small_pct: 30,
    hold_sleeve: 375, sleeve_coverage: false, exit_on_member_sell: false, price_exit: "none", extend: "none", sleeve_price_exit: "none", sleeve_extend: "none",
    per_pick: 262.5, sizing: "equal", size: "standard", size_k: 1 },
  positions: [
    { t: "SGOV", qty: 200, avg: 100.4, px: 100.5, mv: 20100, pl: 20, plpc: 0.001, day: 0, bot: true, bought: day(3), sleeve: "park", hold: null, held: 2, manual: false },
    { t: "GS", qty: 2, avg: 780, px: 805, mv: 1610, pl: 50, plpc: 0.032, day: 0.004, bot: true, bought: day(3), sleeve: null, hold: 60, held: 2, manual: false },
    { t: "AAPL", qty: 5, avg: 210, px: 230, mv: 1150, pl: 100, plpc: 0.095, day: 0.002, bot: false, bought: null, sleeve: null, hold: null, held: null, manual: true },
    { t: "PLUG", qty: 300, avg: 2.1, px: 2.0, mv: 600, pl: -30, plpc: -0.047, day: -0.01, bot: true, bought: day(3), sleeve: "small", hold: 375, held: 2, manual: false } ],
  orders: [{ t: "GS", side: "buy", qty: "2", filled_qty: "2", notional: null, status: "filled", filled_px: "780", at: new Date(Date.now() - 3 * 864e5).toISOString(), bot: true, by: "tool" },
           { t: "SGOV", side: "buy", qty: "200", filled_qty: "200", notional: null, status: "filled", filled_px: "100.4", at: new Date(Date.now() - 3 * 864e5).toISOString(), bot: true, by: "tool" }],
  fills: {}, this_run: [], dividends: null, manual: null },
  member_sold: {}, close_report: { since_start: 0.012, spy_since_start: 0.008, gain: 300, net_in: 0, start: day(3), date: day(1), equity: 25000, day_pl: 200, positions: 3 },
  close_history: [{ date: day(3), equity: 24700, spy: 580, idx: 1 }, { date: day(1), equity: 25000, spy: 585, idx: 1.012 }], problem: null, saved: now };

export const viewerAlerts = [
  { id: "al1aaaa", at: new Date(Date.now() - 3600e3).toISOString(), title: "Bought GS", body: "2 shares at $780.00 ($1,560) · proven member.", kind: "fills", url: "/#port/GS", items: [] },
  { id: "al2bbbb", at: new Date(Date.now() - 7200e3).toISOString(), title: "Market close", body: "Account $25,000, +$200 today.", kind: "close", url: "/#port", items: [] } ];

export async function seed(bucket) {
  const put = (k, v) => bucket.put(k, JSON.stringify(v), { httpMetadata: { contentType: "application/json" } });
  await put("dashboard_data.json", dashboard);
  await put("members/index.json", members);
  await put("members/P000197.json", member1);
  await put("ticker_charts.json", charts);
  await put("movers/latest.json", movers);
  await put("charts/search_index.json", searchIndex);
  await put("charts/trades_by_ticker.json", tradesByTicker);
  await put("cache/sec_names.json", secNames);
  await put("posts/feed.json", { OWNER_ONLY: "posts feed" });
  await put("notes/summary.json", { OWNER_ONLY: "notes" });
  await put("bonds/latest.json", { OWNER_ONLY: "bonds" });
  await bucket.put("widget/key.txt", "OWNERWIDGETKEY-owner-only-0000000000000000");
}
