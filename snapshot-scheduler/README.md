# Independent snapshot clock

Cloudflare cron at UTC minutes 02, 17, 32 and 47 dispatches only the existing
`snapshots.yml` workflow in `abshb/market-lens-updater`. GitHub scheduled runs remain
a fallback. This does not touch the iOS repository or trigger Xcode Cloud.

The worker checks the saved exchange calendar and New York 04:15–20:45 window
(including delayed premarket and after-hours observations), skips recent
publications under 12 minutes old, and skips queued/running jobs. The existing
workflow serializes publications and retains old data on acquisition failure.
GitHub runner queueing and provider latency still apply; 15 minutes is the trigger
cadence, not a guarantee of a publication at an exact second. Cloudflare cron
changes can take up to 15 minutes to propagate. No local computer is required.

`GITHUB_TOKEN` is an encrypted Worker secret, never in source. It must permit
reading workflow runs and dispatching Actions in the updater repository. Rotate
with `wrangler secret put GITHUB_TOKEN --config snapshot-scheduler/wrangler.jsonc`.
Prefer a repository-restricted Actions read/write token when rotating.

Validation:
- `node --test snapshot-scheduler/worker.test.mjs`
- `python3 -m unittest test_snapshot_feed`
- `wrangler deploy --dry-run --config snapshot-scheduler/wrangler.jsonc`

Deployment: `wrangler deploy --config snapshot-scheduler/wrangler.jsonc`.
Check Cloudflare cron invocation logs for `dispatched`, `recent_publication`,
`job_already_running`, or `outside_market_window`; errors fail visibly. Verify
Actions completion and `latest.json` publication time, not only dispatch success.
The calendar currently expires in 2030; an expired calendar fails visibly.
