# Congress trade signals

Runs every morning on GitHub Actions: collects US congressional trade disclosures and
related public records, scores each trade, backtests the strategy and updates
`dashboard_data.json` in Google Drive, which the Capitol Capital dashboard reads.

- Manual run: **Actions → Daily congress trade signals → Run workflow**.
- The first full collection takes more than one run; each run saves its progress and the
  next one continues.
- Secrets used: `SEC_USER_AGENT`, `CONGRESS_API_KEY`, `LDA_API_KEY`, `GDRIVE_SERVICE_ACCOUNT_JSON`,
  `GDRIVE_FOLDER_ID` (optional: `QUIVER_API_KEY`, `FMP_API_KEY`).
- Optional repository variable `USE_TUNED_WEIGHTS` = `true` to use tuned weights.
