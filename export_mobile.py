"""Export stock observations for charts and local AI; never export model state."""
import argparse
from datetime import date, datetime
import json
import math
from pathlib import Path
import re


def finite_number(value):
    return float(value) if value is not None and math.isfinite(float(value)) else None


def query_earnings_dates(ticker: str, earnings_path: Path) -> list[dict[str, object]]:
    import pyarrow.parquet as pq
    if not earnings_path.exists():
        return []
    table = pq.read_table(
        earnings_path,
        columns=[
            "reported_date",
            "reported_eps",
            "estimated_eps",
            "surprise_percentage",
            "report_time",
        ],
        filters=[("ticker", "=", ticker)],
    )
    events_by_date: dict[str, dict[str, object]] = {}
    for row in table.to_pylist():
        reported_date = row.get("reported_date")
        date_key = (
            reported_date.date().isoformat()
            if isinstance(reported_date, datetime)
            else str(reported_date)[:10]
        )
        events_by_date[date_key] = {
            "date": date_key,
            "reportedEps": finite_number(row.get("reported_eps")),
            "estimatedEps": finite_number(row.get("estimated_eps")),
            "surprisePercentage": finite_number(row.get("surprise_percentage")),
            "reportTime": row.get("report_time"),
            "estimated": False,
            "sourceDate": None,
        }
    today = date.today()
    prior_year_dates = [
        date.fromisoformat(event_date)
        for event_date in events_by_date
        if date.fromisoformat(event_date).year == today.year - 1
    ]
    projected: list[tuple[date, date]] = []
    for source_date in prior_year_dates:
        try:
            estimate = source_date.replace(year=source_date.year + 1)
        except ValueError:
            estimate = source_date.replace(year=source_date.year + 1, day=28)
        if estimate <= today:
            try:
                estimate = estimate.replace(year=estimate.year + 1)
            except ValueError:
                estimate = estimate.replace(year=estimate.year + 1, day=28)
        projected.append((estimate, source_date))
    if projected:
        estimate, source_date = min(projected, key=lambda item: item[0])
        estimate_key = estimate.isoformat()
        if estimate_key not in events_by_date:
            events_by_date[estimate_key] = {
                "date": estimate_key,
                "reportedEps": None,
                "estimatedEps": None,
                "surprisePercentage": None,
                "reportTime": None,
                "estimated": True,
                "sourceDate": source_date.isoformat(),
            }
    return [events_by_date[event_date] for event_date in sorted(events_by_date)]



def _sector_from_sic(value: object):
    try:
        sic = int(value)
    except (TypeError, ValueError):
        return None
    if 100 <= sic <= 999 or 2000 <= sic <= 2199:
        return "Consumer Staples"
    if 1000 <= sic <= 1299 or 2900 <= sic <= 2999:
        return "Energy"
    if (
        1300 <= sic <= 1499
        or 2400 <= sic <= 2699
        or 2800 <= sic <= 2829
        or 2850 <= sic <= 2899
        or 3000 <= sic <= 3499
    ):
        return "Materials"
    if 2830 <= sic <= 2839 or 3840 <= sic <= 3859 or 8000 <= sic <= 8099:
        return "Health Care"
    if 3570 <= sic <= 3699 or 7370 <= sic <= 7379:
        return "Information Technology"
    if 4800 <= sic <= 4899:
        return "Communication Services"
    if 4900 <= sic <= 4999:
        return "Utilities"
    if 6000 <= sic <= 6499:
        return "Financials"
    if 6500 <= sic <= 6799:
        return "Real Estate"
    if (
        2200 <= sic <= 2399
        or 2500 <= sic <= 2599
        or 3100 <= sic <= 3199
        or 3700 <= sic <= 3799
        or 3900 <= sic <= 3999
        or 5200 <= sic <= 5999
        or 7000 <= sic <= 7299
        or 7500 <= sic <= 7999
    ):
        return "Consumer Discretionary"
    if (
        1500 <= sic <= 1799
        or 2700 <= sic <= 2799
        or 3500 <= sic <= 3569
        or 3800 <= sic <= 3839
        or 3860 <= sic <= 3899
        or 4000 <= sic <= 4799
        or 5000 <= sic <= 5199
        or 7300 <= sic <= 7369
        or 7380 <= sic <= 7499
        or 8100 <= sic <= 8999
    ):
        return "Industrials"
    return None


def model_contexts(root: Path, session: str):
    """Explicitly allowlisted observations, not predictions or feature tensors."""
    import pyarrow.parquet as pq
    base = root / 'data/approved_large_mega/v1'
    events_path = base / 'earnings/events.parquet'
    caps_path = base / 'training/point_in_time_caps.parquet'
    eps_path = root / 'data/external_free/earnings_surprises/v1/earnings_surprises.parquet'
    if not all(p.exists() for p in (events_path, caps_path, eps_path)):
        return None
    contexts = {}
    def context(ticker):
        return contexts.setdefault(ticker, {'schema': 1, 'sector': None, 'earningsDates': [], 'epsSurprises': [], 'marketCap': None})
    for path in sorted((base / 'earnings/raw/submissions').glob('CIK*.json')):
        company = json.loads(path.read_text())
        sector = _sector_from_sic(company.get('sic'))
        if sector is not None:
            for ticker in company.get('tickers', []):
                context(str(ticker).upper())['sector'] = sector
    for row in pq.read_table(events_path, columns=['ticker', 'entry_date', 'source_type']).to_pylist():
        day = str(row['entry_date'])[:10]
        if row['entry_date'] is not None and row['source_type'] == 'confirmed_8k_item_2_02' and day <= session:
            context(row['ticker'])['earningsDates'].append(day)
    for row in pq.read_table(eps_path, columns=['ticker', 'available_date', 'surprise_percentage', 'report_time']).to_pylist():
        day = str(row['available_date'])[:10]
        if row['available_date'] is not None and day <= session:
            context(row['ticker'])['epsSurprises'].append({'availableDate': day, 'surprisePercentage': finite_number(row['surprise_percentage']), 'reportTime': row['report_time']})
    for row in pq.read_table(caps_path, columns=['ticker', 'entry_date', 'market_cap_usd']).to_pylist():
        day = str(row['entry_date'])[:10]
        cap = finite_number(row['market_cap_usd'])
        if cap is None or day > session:
            continue
        c = context(row['ticker'])
        if c['marketCap'] is None or c['marketCap']['date'] <= day:
            c['marketCap'] = {'date': day, 'usd': cap}
    for c in contexts.values():
        c['earningsDates'] = sorted(set(c['earningsDates']))
        c['epsSurprises'].sort(key=lambda e: e['availableDate'])
    return contexts


def export(root: Path, destination: Path):
    import pyarrow.parquet as pq
    report = json.loads((root / 'data/scheduled_refresh.json').read_text())
    updated = report['completedAt']
    datetime.fromisoformat(updated.replace('Z', '+00:00'))
    source = {'provider': 'github', 'updatedAt': updated, 'session': report['session']}
    contexts = model_contexts(root, report['session'])
    if contexts is not None:
        source['inputSchema'] = 1
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
            # Preserve the training data's precision for on-device preprocessing.
            values = {k: float(row[k]) for k in columns[2:6]}
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
        payload = {'ticker': ticker, 'rows': [bars[d] for d in sorted(bars)], 'dataSource': source, 'earnings': query_earnings_dates(ticker, root / 'data/external_free/earnings_surprises/v1/earnings_surprises.parquet')}
        if contexts is not None:
            payload['modelContext'] = contexts.get(ticker, {'schema': 1, 'sector': None, 'earningsDates': [], 'epsSurprises': [], 'marketCap': None})
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
