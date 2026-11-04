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
@media (display-mode: standalone){ .bell,.mebtn{top:calc(max(16px,env(safe-area-inset-top,0px)) - 4px)} }
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
# The home-screen widget setup button is hidden for now. Its code (widgetModal in data_layer.js) stays in place;
# set SHOW_WIDGET_BUTTON = True and rebuild to bring the button back when the widget needs updating.
SHOW_WIDGET_BUTTON = False
src = sub('<button class="link" id="refresh" type="button" hidden>Refresh</button>',
          '<button class="link" id="refresh" type="button" hidden>Refresh</button><button class="link" id="alerts" type="button" hidden>Turn on alerts</button>'
          + ('<button class="link" id="widgetBtn" type="button">Widget</button>' if SHOW_WIDGET_BUTTON else ''), src)

# ---- phone: tab bar at the bottom (thumb reach), icons + short labels; desktop keeps the top tabs ----
from urllib.parse import quote
_ic = {  # 24px outline icons
    "today": '<rect x="3.5" y="5" width="17" height="15.5" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4"/><circle cx="12" cy="15" r="1.6" fill="#000"/>',
    "record": '<path d="M4 20V4M4 20h16"/><path d="M7 15l4-4 3 3 5-6"/>',
    "defense": '<path d="M12 3l7.5 3v5.5c0 4.6-3.2 8.2-7.5 9.5-4.3-1.3-7.5-4.9-7.5-9.5V6z"/>',
    "small": '<path d="M5 20v-5M10 20v-9M15 20v-6M20 20V8"/>',
    "works": '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.6 10.8c.7.6 1.1 1.4 1.1 2.2h5c0-.8.4-1.6 1.1-2.2A6 6 0 0 0 12 3z"/>',
    "real": '<circle cx="10.5" cy="10.5" r="6"/><path d="M15 15l5.5 5.5M8 10.5l2 2 3.5-4"/>',
    "port": '<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M3 12.5h18"/>',
    "alerts": '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2H4.5z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
    "more": '<path d="M4 7h16M4 12h16M4 17h16"/>',
    "members": '<circle cx="9" cy="8.5" r="3.2"/><path d="M3 19.5c.6-3.3 3-5.2 6-5.2s5.4 1.9 6 5.2"/><path d="M15.5 5.6a3 3 0 0 1 0 5.8M17.5 14.6c1.8.6 3 2.3 3.4 4.9"/>',
}
_short = {"today": "Today", "alerts": "Alerts", "record": "Record", "defense": "Defense", "small": "Small", "works": "Works",
          "real": "Reality", "port": "Account", "members": "Members", "more": ""}
_svg = lambda body: ("url(\"data:image/svg+xml," + quote('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
                     'stroke="#000" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + body + '</svg>') + "\")")
tabcss = "\n@media (max-width: 760px){\n" + \
"""  body{padding-bottom:calc(64px + max(0px, env(safe-area-inset-bottom,0px) - 16px))}
  :root{padding-bottom:0}
  nav.tabs{position:fixed;left:0;right:0;bottom:0;z-index:50;margin:0;gap:0;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,1fr) 64px;
    overflow:visible;background:color-mix(in srgb,var(--surface) 92%,transparent);-webkit-backdrop-filter:saturate(1.6) blur(14px);
    backdrop-filter:saturate(1.6) blur(14px);border-top:1px solid var(--rule);padding:6px 2px max(4px, calc(env(safe-area-inset-bottom,0px) - 16px))}
  nav.tabs button{font-size:0;border:0;padding:4px 0 2px;display:flex;flex-direction:column;align-items:center;gap:3px;color:var(--muted);
    min-width:0;-webkit-tap-highlight-color:transparent}
  nav.tabs button::before{content:"";width:24px;height:24px;background:currentColor;-webkit-mask:var(--ic) center/contain no-repeat;mask:var(--ic) center/contain no-repeat}
  nav.tabs button::after{font:600 10.5px/1.1 var(--body);letter-spacing:.1px;white-space:nowrap}
  nav.tabs button[aria-selected="true"]{color:var(--accent);border:0}
  nav.tabs button[data-tab="more"]{justify-content:center}
  nav.tabs button[data-tab="more"]::after{display:none}
""" + "".join(f'  nav.tabs button[data-tab="{k}"]{{--ic:{_svg(v)}}}\n  nav.tabs button[data-tab="{k}"]::after{{content:"{_short[k]}"}}\n'
              for k, v in _ic.items()) + "}\n</style>"
src = sub("env(safe-area-inset-top,0px))} }\n</style>", "env(safe-area-inset-top,0px))} }" + tabcss, src)
# ---- phone: slim header, wide tables as cards, pull-to-refresh indicator ----
phonecss = """
@media (max-width: 760px){
  header.top{padding-block:10px 0}
  .bell{top:6px;width:36px;height:36px}
  .mebtn{top:6px;width:36px;height:36px;right:44px}
  .brand{align-items:center;gap:2px 12px;padding-bottom:8px}
  .brand .seal{display:none}
  .brand h1{font-size:19px;line-height:1.2}
  .status{font-size:12px;gap:6px;line-height:1.35}
  main{padding-top:14px;gap:18px}
  .tiles{grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
  .tile{padding:10px 12px}
  .tile .v{font-size:22px}
  .tile .l,.tile .c{font-size:12px;line-height:1.35}
  .tbl.cards{background:none;border:0;border-radius:0;overflow:visible;max-height:none}
  .tbl.cards table,.tbl.cards tbody{display:block}
  .tbl.cards thead{display:none}
  .tbl.cards tr{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px 14px;background:var(--surface);border:1px solid var(--rule);border-radius:10px;padding:10px 12px;margin-bottom:8px}
  .tbl.cards td{display:block;border:0;padding:0;text-align:left;white-space:normal;min-width:0;background:none;font-size:13.5px;overflow-wrap:anywhere}
  .tbl.cards td::before{content:attr(data-l);display:block;font:600 10px/1.5 var(--body);letter-spacing:.5px;text-transform:uppercase;color:var(--muted)}
  .tbl.cards td.ttl{order:-1;grid-column:1/-1;font-weight:600;font-size:15px}
  .tbl.cards td.full,.tbl.cards td[data-l=""]{grid-column:1/-1}
  .tbl.cards td.ttl::before,.tbl.cards td[data-l=""]::before{display:none}
  .tbl.cards td.why{min-width:0}
  .tbl.cards tr.on{border-color:var(--accent)}
  .tbl.cards .who-cell{display:flex;flex-direction:column;align-items:flex-start;gap:4px}
  .tbl.cards span.nm{flex-wrap:wrap;row-gap:2px}
}
@media (max-width: 360px){ nav.tabs button::after{font-size:8.5px;letter-spacing:0} nav.tabs button::before{width:22px;height:22px} }
nav.tabs button{overflow:hidden}
#ptr{position:fixed;left:50%;top:calc(env(safe-area-inset-top,0px) + 6px);z-index:60;transform:translate(-50%,-60px);
  background:var(--surface);color:var(--ink2);border:1px solid var(--rule);border-radius:999px;padding:6px 14px;
  font:600 12.5px var(--body);box-shadow:0 4px 14px rgba(0,0,0,.12);pointer-events:none;opacity:0;white-space:nowrap}
#ptr.go{color:var(--accent)}
"""
src = sub("@media (max-width: 760px){\n  body{padding-bottom", phonecss + "@media (max-width: 760px){\n  body{padding-bottom", src)

# tapping a tab starts that tab at the top of the page (bottom bar on phones)
src = sub("""  if (typeof clearViewerMsgs === "function") clearViewerMsgs();
  render();
}));""", """  if (typeof clearViewerMsgs === "function") clearViewerMsgs();
  render();
  window.scrollTo(0, 0);
}));""", src)

# ---- Buy button: orders go straight to Alpaca ----
src = sub("state.requests.find(f => f.title.startsWith(\"order_request_\") && tickerOf(f.title) === t)",
          "(state.taps || []).find(o => o.t === t && OPEN.includes(o.status))", src)
src = sub("const tickerOf = name =>", "const OPEN = [\"new\", \"accepted\", \"pending_new\", \"partially_filled\", \"accepted_for_bidding\", \"held\", \"calculated\"];\nconst tickerOf = name =>", src)
src = sub("<span class=\"chip new\">Order pending</span>", "<span class=\"chip new\">Order placed · waiting to fill</span>", src)
src = sub("<span class=\"foot\">Placed within about 15 minutes, or at the next market open.</span></div>`;",
          "<span class=\"foot\">Sent to Alpaca right away; fills now if the market is open, otherwise tries after-hours and pre-market (within 0.5% of the last price), then the open.</span>"
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
end = src.index("function openChart(t, back) {")
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

# ---- family accounts (owner, Nov 2 2026): one app, two roles (accounts_ui.js) ----
# The family-account code goes first inside the app's script, so render() can use it from the start.
src = sub("<script>\n(() => {\n", "<script>\n(() => {\n" + open(os.path.join(HERE, "accounts_ui.js"), encoding="utf-8").read() + "\n", src)
# a family account only ever renders Today, Members, Alerts and its own Account
src = sub("function render() {\n  setHeaderHeight();", "function render() {\n  guardTab();\n  setHeaderHeight();", src)
src = sub('  if (state.tab === "port") html += renderPortfolio(d);',
          '  if (state.tab === "port") html += isViewer() ? renderViewerAccount(d) : renderPortfolio(d);\n'
          '  if (state.tab === "accounts") html += renderAccounts();', src)
src = sub('  { k: "record", l: "Track record", d: "How past picks did against the S&P 500" },\n];',
          '  { k: "record", l: "Track record", d: "How past picks did against the S&P 500" },\n'
          '  { k: "accounts", l: "Accounts", d: "Family accounts: add people and see each one\'s portfolio" },\n];', src)
# the posts list's "Needs review" (people review) is the owner's job
src = sub('["all", "All new"], ["review", `Needs review${reviewCount() ? " " + reviewCount() : ""}`]]',
          '["all", "All new"], ...(isViewer() ? [] : [["review", `Needs review${reviewCount() ? " " + reviewCount() : ""}`]])]', src)
# live refresh: the Keep list and Why it moved are the owner's
src = sub('        const m = (await api("/api/manual")).js, was = JSON.stringify(state.manual || null);',
          '        if (isViewer()) throw 0;\n        const m = (await api("/api/manual")).js, was = JSON.stringify(state.manual || null);', src)
# the Buy buttons on picks are the owner's (family accounts get Buy more on their Account page)
src = sub('  const b = ev.target.closest("[data-buy],[data-send],[data-cancel]");\n  if (!b) return;',
          '  const b = ev.target.closest("[data-buy],[data-send],[data-cancel]");\n  if (!b || isViewer()) return;', src)
# who's signed in decides the app (owner or family account)
src = sub('api("/api/session").then(() => { loadData();', 'api("/api/session").then(r => { applyRole(r.js); render(); loadData();', src)
# signed out: the server's login page has both ways in (email link and passcode)
src = sub('function showLogin(msg) {\n  $("#login").hidden = false;',
          'function showLogin(msg) {\n  if (!window.__CTD_SAMPLE) { location.replace("/"); return; }\n  $("#login").hidden = false;', src)
# their More menu and pages, and the first-time profile picture step
src = sub('  if (state.tab === "more") html += renderMore();',
          '  if (isViewer() && needsPhotoStep()) { main.innerHTML = renderPhotoStep(); bind(); return; }\n'
          '  if (isViewer() && needsAlertStep()) { main.innerHTML = renderAlertStep(); bind(); return; }\n'
          '  if (isViewer() && (state.tab === "more" || VIEWER_SUB.has(state.tab) && state.tab !== "search")) html += renderViewerPage();\n'
          '  else if (state.tab === "more") html += renderMore();', src)
# Today for a family account: Buy candidates only, with their own Buy box
src = sub("function postsSection() {", "function postsSection() {\n  if (isViewer()) return \"\";", src)
src = sub("function worthSection(d) {", "function worthSection(d) {\n  if (isViewer()) return \"\";", src)
src = sub("function unusualSection(d) {", "function unusualSection(d) {\n  if (isViewer()) return \"\";", src)
src = sub("function buyArea(r) {", "function buyArea(r) {\n  if (isViewer()) return vBuyArea(r);", src)
src = sub("function confirmBox(r) {", "function confirmBox(r) {\n  if (isViewer()) return vBuyBox(r.t, false);", src)
src = sub('  if (state.tab === "today") html += renderToday(d);',
          '  if (state.tab === "today" && isViewer()) html += tip("<b>Today.</b> These are the stocks members of Congress bought recently whose purchases score near the top of all past ones. The tool buys them for you automatically; you don\'t need to do anything. Tap a ticker for its chart and what\'s going on.");\n'
          '  if (state.tab === "today" && isViewer()) html += emailAskCard();\n'
          '  if (state.tab === "today") html += renderToday(d);', src)
# family accounts: the Today jump bar lists only the sections they see, and is hidden while that's just one
src = sub('function todayJump(d, nb, nAvoid, nAlso) {\n  const notable', 'function todayJump(d, nb, nAvoid, nAlso) {\n  if (isViewer() && VIEWER_SECTIONS.size < 2) return "";\n  const notable', src)
src = sub('    .filter(([id, , n]) => n !== null && (id === "today-buy" || id === "posts" || n > 0));\n  return `<nav class="jump"',
          '    .filter(([id, , n]) => n !== null && (id === "today-buy" || id === "posts" || n > 0))\n    .filter(([id]) => !isViewer() || VIEWER_SECTIONS.has(id));\n  return `<nav class="jump"', src)
# a paused family account sees a banner at the top of every page
src = sub('  let html = "";\n  if (d.version === 1)', '  let html = "";\n  if (isViewer()) html += pausedBanner();\n  if (d.version === 1)', src)
# your trades on a stock's chart show your own picture
n = src.count('<img src="/me.jpg" alt="" onerror="this.style.display=\'none\'"></button>`).join("")}')
assert n == 1, n
src = src.replace('<img src="/me.jpg" alt="" onerror="this.style.display=\'none\'"></button>`).join("")}', '<img src="${myPhoto()}" alt="" onerror="this.style.display=\'none\'"></button>`).join("")}')
src = sub('det.innerHTML = `<div class="tdet"><img src="/me.jpg" alt=""', 'det.innerHTML = `<div class="tdet"><img src="${myPhoto()}" alt=""', src)
# the widget: their own account's
src = sub('const r = await fetch("/api/widget/script",', 'const r = await fetch(isViewer() ? "/api/me/widget" : "/api/widget/script",', src)
acct_css = """
/* family accounts: nothing that leads outside Today, Members, Alerts and their Account */
html.viewer #mebtn,html.viewer [data-more]:not([data-vok]):not([data-more="more"]),html.viewer [data-why],
html.viewer [data-buy],html.viewer .sellbtn,html.viewer .keepbtn,html.viewer [data-tie],html.viewer [data-tiev],
html.viewer #today-avoid,html.viewer #today-also,html.viewer #today-unusual,html.viewer #today-worth,html.viewer #posts,
html.viewer [data-jump="today-avoid"],html.viewer [data-jump="today-also"],html.viewer [data-jump="posts"],
html.viewer [data-jump="today-worth"],html.viewer [data-jump="today-unusual"]{display:none!important}
.vtip{display:none}html.tips .vtip{display:block;background:var(--surface);border:1px solid var(--rule);border-left:3px solid var(--accent);border-radius:10px;padding:10px 12px;font-size:14px;margin-bottom:12px}
.vbtns{display:flex;gap:8px;margin-top:6px;flex-wrap:wrap}
/* spare cash choice */
.parkopts{display:grid;gap:10px}
.parkopt{display:grid;grid-template-columns:22px minmax(0,1fr);gap:10px;align-items:start;text-align:left;background:var(--surface);border:1.5px solid var(--rule);border-radius:12px;padding:12px;color:var(--ink);font:15px/1.45 var(--body);cursor:pointer}
.parkopt b{display:block;margin-bottom:3px}.parkopt .foot{display:block}
.parkopt .pr{width:18px;height:18px;border-radius:50%;border:2px solid var(--muted);margin-top:2px;box-sizing:border-box}
.parkopt.on{border-color:var(--accent);background:var(--accent-soft)}.parkopt.on .pr{border:6px solid var(--accent)}
.parkopt:disabled{cursor:default;opacity:.85}
/* table headers stay in view: a pinned copy below the top bar (data_layer.js), and inside boxes that scroll */
.stickyhead{position:fixed;z-index:4;overflow:hidden;pointer-events:none;background:var(--sunken);box-shadow:0 2px 6px rgba(0,0,0,.10);border-radius:0 0 6px 6px}
.stickyhead table{border-collapse:collapse;table-layout:fixed;font-size:13.5px}.stickyhead th{border-bottom:1px solid var(--rule)}
.tbl.scroll thead th{position:sticky;top:0;z-index:1}
/* Terms of Use and Privacy Policy: a document page with an initial box after each section */
.tdoc{background:#fff;color:#1b1f27;border:1px solid var(--rule);border-radius:4px;box-shadow:0 1px 4px rgba(0,0,0,.08);padding:24px 20px 26px;margin:0 0 16px;font:15px/1.6 Georgia,"Times New Roman",serif;max-width:760px}
.tdoc h1{font:600 21px/1.3 Georgia,serif;text-align:center;margin:0 0 4px;color:#1b1f27}.tdoc h3{font:700 15px Georgia,serif;margin:16px 0 4px;color:#1b1f27}
.tdoc p,.tdoc li{margin:0 0 8px}.tdoc ul{padding-left:20px;margin:0}.tdoc a{color:#1D4F91}
.tsub{text-align:center;font:13.5px var(--body);color:#4b5565;margin:0 0 16px!important}
.tlead{margin:0 0 12px;color:var(--ink2)}
.tcl{display:grid;grid-template-columns:minmax(0,1fr) 58px;gap:12px;padding:10px 0;border-top:1px solid #e3e6eb}
.tini{width:58px;height:36px;align-self:end;border:1.5px dashed #9aa3b2;border-radius:4px;background:#fff;color:#8a93a3;font:12px var(--body);cursor:pointer;display:grid;place-items:center}
.tini.on{border-style:solid;border-color:#1D4F91;color:#1D4F91;font:600 15px var(--body)}
.tsig{margin-top:18px;padding-top:12px;border-top:2px solid #1b1f27}
.tsg{display:grid;grid-template-columns:minmax(0,1fr) 110px;gap:14px;margin:12px 0 14px}
.tline{width:100%;box-sizing:border-box;border:0;border-bottom:1.5px solid #1b1f27;background:none;height:38px;padding:0 4px;font:italic 22px "Snell Roundhand","Brush Script MT",cursive;color:#1D4F91;outline:none;border-radius:0}
.tline::placeholder{font:14px var(--body);color:#9aa3b2}.tline.signed{display:flex;align-items:flex-end}
.tdate{border-bottom:1.5px solid #1b1f27;height:38px;display:flex;align-items:flex-end;padding:0 0 4px 4px;font:15px var(--body);box-sizing:border-box}
.tcap{font:12px var(--body);color:#4b5565;margin-top:4px}.tgo{width:100%}.tgo:disabled{opacity:.5}
.legal{display:flex;gap:10px;justify-content:center;align-items:center;margin:4px 0 16px;color:var(--ink2);font-size:13px}.legal .link{font-size:13px}
.pausebar{background:#FFF4E5;color:#7A4B00;border:1px solid #F0C27A;border-radius:10px;padding:10px 12px;margin:0 0 12px;font-size:14px}
.pausebar .link{color:inherit;text-decoration:underline;font-size:14px}
@media (prefers-color-scheme:dark){.pausebar{background:#3A2A10;color:#F5D59A;border-color:#7A5A20}}
.pick>.sellbox{grid-column:1/-1;margin-top:6px}.sellbox .note{max-width:760px}
.vsell{font:600 13px var(--body);padding:6px 12px;border-radius:8px;border:1px solid var(--bad);background:none;color:var(--bad);cursor:pointer}
.vsell.go{background:var(--bad);color:#fff}.vsell:disabled{opacity:.6}
.vme{width:64px;height:64px;border-radius:50%;object-fit:cover;border:1px solid var(--rule)}.vme.none{display:inline-block;background:var(--rule)}
.vrules{list-style:none;margin:0 0 10px;padding:0;display:grid;gap:6px}.vrules li{display:flex;justify-content:space-between;gap:10px;align-items:center}
.keysform select{min-width:0;width:100%;font:15px var(--body);padding:10px;border:1px solid var(--rule);border-radius:10px;background:var(--surface);color:var(--ink)}
.faq details{border-bottom:1px solid var(--rule);padding:10px 0}.faq summary{font-weight:600;cursor:pointer}.faq p{margin:8px 0 2px;color:var(--ink2)}
.vswitch{display:flex;gap:10px;align-items:center;font-size:15px}
.steps{margin:8px 0 12px;padding-left:20px;display:grid;gap:6px}
.keysform{display:grid;gap:10px;max-width:420px;margin:6px 0}
.keysform input{font:500 15px var(--mono);padding:10px 12px;border:1px solid var(--rule);border-radius:10px;background:var(--surface);color:var(--ink);width:100%;box-sizing:border-box}
.keysform input[type=email],.keysform input#accl{font-family:var(--body)}
.accts{display:grid;gap:10px}
.acct{border:1px solid var(--rule);border-radius:12px;padding:12px 14px;display:grid;gap:6px;background:var(--surface)}
.acct-h{display:flex;flex-wrap:wrap;align-items:center;gap:8px}.acct-h b{font-size:16px}
.acct-s{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:14px}
.acct-b{display:flex;flex-wrap:wrap;gap:8px;margin-top:4px}
.btn.sm{padding:6px 10px;font-size:13px}
</style>"""
src = sub("</style>", acct_css, src)
# Today for a family account: no "Suggested $" amounts (the tool sizes their buys) and no owner phone-alert notes
src = sub("function suggestLine(r) {\n  const g = suggestion(r); if (!g) return \"\";",
          "function suggestLine(r) {\n  if (isViewer()) return \"\";\n  const g = suggestion(r); if (!g) return \"\";", src)
src = sub('    <p class="foot">Sent to your phone: 3+ members', '    <p class="foot owner-only">Sent to your phone: 3+ members', src)
src = sub('html.viewer [data-jump="today-unusual"]{display:none!important}', 'html.viewer [data-jump="today-unusual"],html.viewer .owner-only{display:none!important}', src)

# ---- no autocorrect, auto-capitals or spell-check in any text box (owner, Oct 29) ----
# Boxes are drawn and redrawn by the page's code, so a watcher marks every box as it appears.
nocorrect = """<script>
(function () {
  function off(el) {
    if (el.dataset.nocorrect) return;
    el.dataset.nocorrect = "1";
    el.setAttribute("autocorrect", "off");
    el.setAttribute("autocapitalize", "off");
    el.setAttribute("spellcheck", "false");
    el.spellcheck = false;
    if (!el.hasAttribute("autocomplete")) el.setAttribute("autocomplete", "off");
  }
  function scan(root) {
    if (!root || !root.querySelectorAll) return;
    if (root.matches && root.matches("input,textarea,[contenteditable]")) off(root);
    root.querySelectorAll("input,textarea,[contenteditable]").forEach(off);
  }
  function start() {
    scan(document.body);
    new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(scan))).observe(document.body, { childList: true, subtree: true });
  }
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
})();
</script>
"""
src = sub("</body></html>", nocorrect + "</body></html>", src)

for gone in ("mcp.callTool", "window.claude", "order_request_${"):
    if gone in src:
        sys.exit(f"build_app: leftover Drive code: {gone}")
os.makedirs(os.path.join(HERE, "app"), exist_ok=True)
open(os.path.join(HERE, "app", "index.html"), "w", encoding="utf-8").write(src)
print("built cloud/app/index.html", len(src), "bytes")
