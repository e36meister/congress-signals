"""Builds cloud/app/index.html from the claude.ai dashboard page (dashboard.html next to this file):
swaps the Google Drive data layer for the Cloudflare one and adds the home-screen app settings and login.
Run: python cloud/build_app.py"""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(HERE, "dashboard.html"), encoding="utf-8").read()


def sub(old, new, s, count=1):
    if old not in s:
        sys.exit(f"build_app: couldn't find: {old[:70]!r}")
    return s.replace(old, new, count)


# ---- head: home-screen app settings ----
head = """<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="Capitol Capital">
<meta name="theme-color" content="#F3F5F8" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0E131A" media="(prefers-color-scheme: dark)">
<meta name="format-detection" content="telephone=no">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon-180.png">
<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">
"""
src = sub("</head>", head + "</head>", src)

# ---- login screen styles + markup ----
css = """
.login{position:fixed;inset:0;z-index:100;background:var(--ground);display:grid;place-items:center;padding:24px 16px}
.login form{width:100%;max-width:340px;display:grid;gap:14px;text-align:center}
.login img{width:84px;height:84px;border-radius:20px;justify-self:center;box-shadow:0 6px 20px rgba(0,0,0,.15)}
.login h1{font:600 26px var(--head)}
.login p{margin:0;color:var(--ink2);font-size:14px}
.login input{font:500 18px var(--mono);padding:12px 14px;border:1px solid var(--rule);border-radius:10px;background:var(--surface);color:var(--ink);text-align:center;width:100%}
.login button{font:600 16px var(--body);padding:12px;border-radius:10px;border:0;background:var(--accent);color:#fff;cursor:pointer}
:root[data-theme="dark"] .login button{color:#0E131A}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]) .login button{color:#0E131A}}
.login button:disabled{opacity:.6}
.login .msg{min-height:20px;font-size:13.5px;color:var(--bad)}
@media (display-mode: standalone){ header.top{padding-top:max(16px,env(safe-area-inset-top,0px))} }
</style>"""
src = sub("</style>\n\n<div class=\"wrap\">", css + "\n\n<div class=\"wrap\">", src)
login = """<div class="login" id="login" hidden><form id="loginForm" autocomplete="on">
  <img src="/icon-180.png" alt="">
  <h1>Capitol Capital</h1>
  <p>Enter your passcode. This phone stays logged in for 6 months.</p>
  <input type="text" name="username" value="Capitol Capital" autocomplete="username" hidden>
  <input type="password" id="pc" name="password" autocomplete="current-password" placeholder="Passcode" required aria-label="Passcode">
  <button type="submit" id="pcGo">Log in</button>
  <div class="msg" id="pcMsg" role="alert"></div>
</form></div>
"""
src = sub("<div class=\"wrap\">", login + "<div class=\"wrap\">", src)
src = sub("Connecting to your Google Drive…", "Loading…", src)
src = sub('<button class="link" id="refresh" type="button" hidden>Refresh</button>',
          '<button class="link" id="refresh" type="button" hidden>Refresh</button><button class="link" id="alerts" type="button" hidden>Turn on alerts</button>', src)

# ---- Buy button: orders go straight to Alpaca ----
src = sub("state.requests.find(f => f.title.startsWith(\"order_request_\") && tickerOf(f.title) === t)",
          "(state.taps || []).find(o => o.t === t && OPEN.includes(o.status))", src)
src = sub("const tickerOf = name =>", "const OPEN = [\"new\", \"accepted\", \"pending_new\", \"partially_filled\", \"accepted_for_bidding\", \"held\", \"calculated\"];\nconst tickerOf = name =>", src)
src = sub("<span class=\"chip new\">Order pending</span>", "<span class=\"chip new\">Order placed · waiting to fill</span>", src)
src = sub("<span class=\"foot\">Placed within about 15 minutes, or at the next market open.</span></div>`;",
          "<span class=\"foot\">Sent to Alpaca right away; fills now if the market is open, otherwise at the next open.</span>"
          "${state.tapMsg[r.t] ? `<div class=\"tapmsg neg\">${esc(state.tapMsg[r.t].text)}</div>` : \"\"}</div>`;", src)

# "From the Buy button" section: real orders from Alpaca instead of request files
start = src.index("function reqSection() {")
end = src.index("function addTradingDays(")
src = src[:start] + r"""function reqSection() {
  const rs = state.taps || [];
  if (!rs.length) return "";
  const st = o => o.status === "filled" ? `<span class="pos">Bought ${Number(o.filled_qty).toLocaleString(undefined, { maximumFractionDigits: 4 })} at ${money(o.filled_px, 2)}</span>`
    : OPEN.includes(o.status) ? "Waiting to fill" + (state.data?.portfolio?.market_open ? "" : " at the market open")
    : `<span class="neg">${esc(String(o.status).replace(/_/g, " "))}</span>`;
  return `<section class="panel"><div class="ph"><h2>From the Buy button</h2><span class="sub">Last 7 days · live from Alpaca</span></div>
  <div class="tbl"><table><thead><tr><th>When</th><th>Ticker</th><th class="n">Amount</th><th>Status</th></tr></thead><tbody>
  ${rs.map(o => `<tr><td class="nw">${esc(fmtET(o.at))}</td><td class="mono">${esc(o.t)}</td><td class="n">${o.notional ? money(o.notional) : (o.qty ? Number(o.qty).toLocaleString() + " sh" : "–")}</td><td>${st(o)}</td></tr>`).join("")}
  </tbody></table></div></section>`;
}
""" + src[end:]

# ---- ticker charts from Cloudflare ----
start = src.index("async function loadCharts() {")
end = src.index("function openChart(t) {")
src = src[:start] + """async function loadCharts() {
  if (pop.charts) return pop.charts;
  if (window.__CTD_CHARTS) return (pop.charts = window.__CTD_CHARTS);
  if (!pop.loading) pop.loading = api("/api/charts").then(r => (pop.charts = r.js)).finally(() => { pop.loading = null; });
  return pop.loading;
}
""" + src[end:]

# ---- data layer: replace everything from the Drive section to the end of the script ----
start = src.index("// ---------- data from Google Drive ----------")
end = src.index("})();\n</script>")
src = src[:start] + open(os.path.join(HERE, "data_layer.js"), encoding="utf-8").read() + src[end:]

for gone in ("mcp.callTool", "window.claude", "order_request_${"):
    if gone in src:
        sys.exit(f"build_app: leftover Drive code: {gone}")
os.makedirs(os.path.join(HERE, "app"), exist_ok=True)
open(os.path.join(HERE, "app", "index.html"), "w", encoding="utf-8").write(src)
print("built cloud/app/index.html", len(src), "bytes")
