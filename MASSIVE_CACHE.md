# Massive cache

Mobile reads only public GitHub cache objects on `codex/massive-stock-cache-v1`. The API credential is a GitHub Actions secret and never enters mobile assets or the data branch.

The initial cache comes from the downloaded 2016–2026 daily flat files. Daily market summaries exclude OTC and include the complete exchange-listed symbol universe, including case-sensitive preferred-share and warrant suffixes. Historical files retain delisted symbols for research.

Actions checks every 30 minutes. GitHub schedules can be delayed. A new completed session (30-minute settlement buffer) or the first UTC-day check fetches market-wide daily summaries for missing sessions and the latest three sessions to repair corrections. This takes one API request per session, plus split-event pagination. Original history objects stay immutable; per-stock deltas carry additions and corrections. A checksum-pinned manifest publishes stock indexes and pattern observations atomically in one Git commit.

`live-stocks.json` is empty initially: zero intraday requests and no frequent feed. Adding stocks requires a separate access-control decision; this public cache does not authenticate users. The Developer plan has a 15-minute market-data delay.

Mobile caches the catalog, immutable objects, and merged split-adjusted histories. The model consumes the same saved histories but still uses its existing trained ticker universe and saved fundamental/earnings context. New prices do not expand or retrain those models. AI results persist until the broom clears them.

Invalid rows are quarantined by symbol/date and do not block valid peers. Incomplete whole-market responses fail before publishing; old valid rows remain. Missing provider bars are never invented. New symbols without flat-file history are marked `historyBackfillNeeded` and cannot supply a full model history yet.

Run safeguards: `python -m unittest -v test_massive_cache`.
