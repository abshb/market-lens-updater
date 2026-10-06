# Market Lens scheduled updater

Public orchestration for a private market-data processing job. Runs every three
hours at minute 37 UTC when REFRESH_ENABLED=true. GitHub may delay scheduled runs.
Manual dispatch is available for deployment verification.

Only orchestration is public. Processing code, model weights, saved data, provider
credentials, and provider logs remain private. No public data feed is published.

## Configuration

- STATE_TOKEN: fine-grained token restricted to the private state repository with
  Contents read/write. The deployment token has no expiration; revoke if compromised.
- ALPHA_VANTAGE_API_KEY: earnings provider credential.
- SEC_USER_AGENT: application name and contact email for SEC requests.
- REFRESH_ENABLED: enable only after a successful manual run.

The private repository has a `market-data-state` release with
`backend-source.tar.gz` and timestamped `state-*.tar.gz` assets. Failed refreshes
retain the last saved state. Successful uploads retain the newest two generations.
The worker checks completed daily candles, spaces price requests 12 seconds apart,
stops when Yahoo throttles, and refreshes earnings at most once per New York day.
It does not provide intraday quotes or guarantee exemption from provider limits.

No pull-request trigger is configured. Do not run untrusted code with these secrets.
The mobile app's public data delivery remains a separate deployment step.

## Verify saved state

Manually dispatch **Verify saved private data** to restore the latest complete
archive and validate its prices, SPY benchmark, scan date, and refresh timestamps.
It makes no supplier requests and does not change or publish saved datasets.
Logs contain aggregate verification counts only; source records stay private.

Public/mobile distribution is currently blocked pending a verified supplier
redistribution agreement. A free consumer app is not permission to redistribute
Yahoo or Alpha Vantage data. Do not expose the private release or its archives.

## Mobile HTTPS serving (prepared, not enabled)

`export_mobile.py` creates `_site` containing only per-ticker daily OHLCV JSON,
a ticker manifest, and a landing page. It does not copy the private state tree.

Publication requires enabling Actions-based Pages and setting `PUBLIC_DATA_ENABLED=true`.
This makes price histories publicly readable. Leave this variable unset until
the owner approves publication. Dispatch with `publish_only=true` to publish
the saved snapshot without new supplier requests. Subsequent successful scheduled
refreshes publish automatically.

The mobile client uses `https://abshb.github.io/market-lens-updater` and requests
`/api/saved-stocks/<TICKER>.json` without GitHub credentials or a local server.
