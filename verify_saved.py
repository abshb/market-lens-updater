"""Read-only verification of the latest private refresh, without provider calls."""
import json
from pathlib import Path

import pandas as pd
import worker


def verify(root: Path):
    report = json.loads((root / 'data/scheduled_refresh.json').read_text())
    scan = json.loads((root / 'outputs/scanners/v2-latest-all-stocks/result.json').read_text())
    prices = pd.read_parquet(root / 'data/approved_large_mega/v1/prices/adjusted_daily.parquet',
                             columns=['ticker', 'date', 'open', 'high', 'low', 'close', 'volume'])
    benchmark = pd.read_parquet(root / 'data/approved_large_mega/v1/benchmarks/spy_adjusted.parquet',
                                columns=['date'])
    session = report['session']
    if prices.empty or prices.duplicated(['ticker', 'date']).any():
        raise RuntimeError('Saved price history is empty or duplicated')
    values = prices[['open', 'high', 'low', 'close', 'volume']]
    import numpy as np
    if not np.isfinite(values.to_numpy()).all() or (prices.close <= 0).any():
        raise RuntimeError('Saved price history failed numeric validation')
    if str(prices.date.max().date()) != session or str(benchmark.date.max().date()) != session or scan['asOfDate'] != session:
        raise RuntimeError('Saved prices, benchmark, and scan disagree with the saved session')
    if pd.Timestamp(report['completedAt']) <= pd.Timestamp(report['startedAt']):
        raise RuntimeError('Saved refresh timestamps are invalid')
    # Metadata only: never log prices, signals, model contents, or raw provider output.
    return {
        'verified': True,
        'session': session,
        'completedAt': report['completedAt'],
        'symbols': int(prices.ticker.nunique()),
        'priceRows': len(prices),
        'symbolsAtSession': int((prices.groupby('ticker', observed=True).date.max() == pd.Timestamp(session)).sum()),
        'priceFailures': len(report.get('priceFailures', [])),
        'emptyTickers': len(report.get('emptyTickers', [])),
        'earningsQuotaReached': bool(report.get('earningsQuotaReached')),
        'unresolvedEarnings': report.get('unresolvedEarnings'),
    }


if __name__ == '__main__':
    try:
        summary = verify(worker.PRIVATE / 'backend')
    except Exception:
        raise SystemExit('Saved private state failed verification; inspect it privately.')
    print(json.dumps(summary, indent=2))

