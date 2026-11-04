# Family-account click-through (local, no real money)

Runs the app's server (cloud/worker.js) on this machine with Miniflare, a fake Alpaca/GitHub/Resend and test data
shaped like the real R2 files, then clicks through every page and button a family account can reach with Playwright,
logging what each button sends to the server and what comes back (out/log-runN.json, screenshots in out/shots).

    cd tools/app_test && npm i miniflare@4 && python3 ../../cloud/build_app.py
    node server.mjs &            # app on :8787, test controls on :8788
    node run1.mjs && node run2.mjs && node run3.mjs && node run4.mjs && node run5.mjs   # run6: re-checks of the Nov 4 fixes

run1 owner adds an account, sign-in link · run2 first screens, Today, chart, Members, More pages, Terms, Connect ·
run3 Account page, buy size, Buy / Buy more, Sell, Pause · run4 Alerts, Notifications, Settings, bug report, search,
email question, everything that must stay out of reach · run5 the owner's Accounts buttons · run6 Nov 4 fixes.
