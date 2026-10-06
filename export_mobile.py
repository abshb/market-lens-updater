"""Export only chart prices for Pages; never copy the private state tree."""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import re


def export(root: Path, destination: Path):
    import pyarrow.parquet as pq
    report = json.loads((root / 'data/scheduled_refresh.json').read_text())
    updated = report['completedAt']
    datetime.fromisoformat(updated.replace('Z', '+00:00'))
    source = {'provider': 'github', 'updatedAt': updated, 'session': report['session']}
    columns = ['ticker', 'date', 'open', 'high', 'low', 'close', 'volume']
    table = pq.read_table(root / 'data/approved_large_mega/v1/prices/adjusted_daily.parquet', columns=columns)
    tickers = {}
    for batch in table.to_batches(max_chunksize=50000):
        for row in batch.to_pylist():
            ticker = row['ticker']
            if not re.fullmatch(r'[A-Z0-9.^-]{1,20}', ticker):
                raise ValueError('Invalid ticker')
            day = str(row['date'])[:10]
            datetime.strptime(day, '%Y-%m-%d')
            values = {k: round(float(row[k]), 4) for k in columns[2:6]}
            values['volume'] = int(row['volume'])
            if not all(math.isfinite(v) for v in values.values()):
                raise ValueError('Non-finite price')
            bars = tickers.setdefault(ticker, {})
            if day in bars:
                raise ValueError('Duplicate ticker date')
            bars[day] = {'time': day, **values}
    if not tickers:
        raise ValueError('Empty dataset')
    if destination.exists():
        raise ValueError('Output directory must be new')
    output = destination / 'api/saved-stocks'
    output.mkdir(parents=True)
    for ticker, bars in sorted(tickers.items()):
        payload = {'ticker': ticker, 'rows': [bars[d] for d in sorted(bars)], 'dataSource': source}
        (output / f'{ticker}.json').write_text(json.dumps(payload, separators=(',', ':'), allow_nan=False))
    scan_stocks = []
    for ticker, bars in sorted(tickers.items()):
        ordered = [bars[d] for d in sorted(bars)]
        ema = ordered[0]['close']
        for bar in ordered[1:]:
            ema += (bar['close'] - ema) * (2 / 22)
        sma = sum(bar['close'] for bar in ordered[-200:]) / 200 if len(ordered) >= 200 else None
        scan_stocks.append({'ticker': ticker, 'ema21': ema if len(ordered) >= 21 else None, 'sma200': sma,
                            'rows': [[bar[k] for k in ['time', 'open', 'high', 'low', 'close', 'volume']] for bar in ordered[-252:]]})
    (destination / 'pattern-input.json').write_text(json.dumps({'dataSource': source, 'stocks': scan_stocks}, separators=(',', ':'), allow_nan=False))
    (destination / 'manifest.json').write_text(json.dumps({'dataSource': source, 'tickers': sorted(tickers)}, separators=(',', ':')))
    (destination / 'index.html').write_text('<!doctype html><title>Market Lens saved data</title><h1>Market Lens saved data</h1><p>Historical daily prices for the Market Lens app. Updates are scheduled every three hours.</p>')
    (destination / '.nojekyll').touch()
    print(f'Exported {len(tickers)} ticker files.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('private/backend'))
    parser.add_argument('--output', type=Path, default=Path('_site'))
    args = parser.parse_args()
    export(args.root, args.output)
