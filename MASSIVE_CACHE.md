# Massive cache

Mobile reads only public GitHub cache objects on `codex/massive-stock-cache-v1`. The API credential is a GitHub Actions secret and never enters mobile assets or the data branch.

The initial cache comes from the downloaded 2016–2026 daily flat files. Daily market summaries exclude OTC and include the complete exchange-listed symbol universe, including case-sensitive preferred-share and warrant suffixes. Historical files retain delisted symbols for research.

Actions checks every 30 minutes. GitHub schedules can be delayed. A new completed session (30-minute settlement buffer) or the first UTC-day check fetches market-wide daily summaries for missing sessions and the latest three sessions to repair corrections. This takes one API request per session, plus split-event pagination. Original history objects stay immutable; per-stock deltas carry additions and corrections. A checksum-pinned manifest publishes stock indexes and pattern observations atomically in one Git commit.

`live-stocks.json` is empty initially: zero intraday requests and no frequent feed. Adding stocks requires a separate access-control decision; this public cache does not authenticate users. The Developer plan has a 15-minute market-data delay.

Mobile caches the catalog, immutable objects, and merged split-adjusted histories. The model consumes the same saved histories but still uses its existing trained ticker universe and saved fundamental/earnings context. New prices do not expand or retrain those models. AI results persist until the broom clears them.

Invalid rows are quarantined by symbol/date and do not block valid peers. Incomplete whole-market responses fail before publishing; old valid rows remain. Missing provider bars are never invented. New symbols without flat-file history are marked `historyBackfillNeeded` and cannot supply a full model history yet.

Run safeguards: `python -m unittest -v test_massive_cache`.

## Acquisition repair and retention

The updater rejects timestamps whose UTC date differs from their source daily filename. The versioned history repair restores authoritative original-session rows for the 29 known mismatches without modifying downloaded raw files. Repairs are idempotent.

The mobile-approved pattern snapshot has the same price rows and moving averages as the full snapshot, restricted to `pattern-tickers.json`. Existing clients keep the full snapshot; updated clients use the compact snapshot. Detectors are unchanged.

Current objects and snapshots from the preceding seven days are protected. Unreferenced objects are removed only after a persisted seven-day grace period; fresh checkout mtimes do not reset this clock. Git history retains prior publications.

Refresh runs have bounded retries, additional closing-session catch-up attempts, and a saved `health.json` freshness report. A delayed GitHub schedule remains possible; a scheduled interval is not a delivery guarantee. Intraday eligibility remains empty.

## Approved-stock delayed snapshots

A separate `snapshots.yml` workflow requests only the 803 symbols in `pattern-tickers.json`, in batches of at most 100, and publishes `latest.json` atomically on `codex/market-snapshots-v1`. It does not modify the completed daily cache, scanner inputs, or daily acquisition workflow. The existing GitHub `MASSIVE_API_KEY` secret is reused.

Scheduled checks are every 15 minutes, offset from the top of the hour. The saved exchange calendar and America/New_York timezone gate acquisition to trading days from 04:15 through 20:45 ET, allowing for the provider's 15-minute delay and final extended-hours prints. Manual dispatch can fetch outside that window. GitHub schedules are best-effort and can be delayed or skipped; this is not a real-time feed. No local machine or local scheduler is required.

Each observation retains its provider timestamp and is classified pre-market, regular, post-market, or closed using that timestamp and the exchange close (including early closes). Missing tickers are explicit nulls, never fabricated prices; malformed/empty/failed batches do not overwrite the previous publication. A snapshot is latest state, not an intraday candle history. The normalized day/minute fields are provider data; no OHLC is synthesized. The mobile quote uses the observation independently of completed daily strategy/model inputs and labels delayed/stale data. The branch retains publication audit history through Git commits.

Validation: `python -m unittest -v test_snapshot_feed`. Initial deployment is verified by a manual GitHub Actions run and read-back of the public data file.
