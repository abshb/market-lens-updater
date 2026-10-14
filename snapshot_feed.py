"""Selected-stock delayed snapshots. Separate from completed daily history."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
BATCH_SIZE = 100

def positive(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0

def calendar_sessions(path):
    return dict(json.loads(Path(path).read_text())['sessions'])

def should_fetch(now, sessions):
    local = now.astimezone(NY)
    # Include the delayed final after-hours prints. Holidays use the saved calendar.
    return local.date().isoformat() in sessions and 4 * 60 + 15 <= local.hour * 60 + local.minute <= 20 * 60 + 45

def phase(timestamp, sessions):
    local = datetime.fromtimestamp(timestamp / 1000, timezone.utc).astimezone(NY)
    close = sessions.get(local.date().isoformat())
    if close is None or local.hour < 4 or local.hour >= 20:
        return 'closed'
    if local.hour * 60 + local.minute < 570:
        return 'pre'
    return 'regular' if timestamp < close else 'post'

def normalize(item, now, sessions):
    candidates = []
    for key, price_key, scale in [('lastTrade', 'p', 1_000_000), ('min', 'c', 1)]:
        data = item.get(key) or {}
        if positive(data.get(price_key)) and positive(data.get('t')):
            stamp = int(data['t'] / scale)
            if 946684800000 <= stamp <= int(now.timestamp() * 1000) + 60000:
                candidates.append((stamp, data[price_key], key))
    if not candidates:
        return None
    stamp, price, source = max(candidates)
    previous = (item.get('prevDay') or {}).get('c')
    return {'price': price, 'asOf': datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat(),
            'session': phase(stamp, sessions), 'source': source,
            'previousClose': previous if positive(previous) else None,
            'changePercent': (price / previous - 1) * 100 if positive(previous) else None,
            'day': item.get('day'), 'minute': item.get('min')}

def acquire(tickers, sessions, now, request):
    stocks = {ticker: None for ticker in tickers}
    received = set()
    for start in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[start:start+BATCH_SIZE]
        payload = request(batch)
        if payload.get('status') not in ('OK', 'DELAYED') or not isinstance(payload.get('tickers'), list):
            raise ValueError('Provider snapshot response invalid; previous publication retained')
        for item in payload['tickers']:
            symbol = item.get('ticker')
            if symbol not in batch or symbol in received:
                raise ValueError('Unexpected or duplicate provider ticker')
            received.add(symbol)
            stocks[symbol] = normalize(item, now, sessions)
    if not any(stocks.values()):
        raise ValueError('No usable snapshot prices; previous publication retained')
    return {'schema': 1, 'provider': 'massive', 'delayMinutes': 15, 'refreshMinutes': 15,
            'publishedAt': now.isoformat(), 'universeCount': len(tickers),
            'universeSha256': hashlib.sha256(json.dumps(tickers, separators=(',', ':')).encode()).hexdigest(),
            'requested': tickers, 'missing': [t for t, v in stocks.items() if v is None], 'stocks': stocks}

def provider_request(batch):
    url = 'https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers?' + urlencode({'tickers': ','.join(batch), 'include_otc': 'false'})
    req = Request(url, headers={'Authorization': 'Bearer ' + os.environ['MASSIVE_API_KEY'], 'Accept': 'application/json'})
    for attempt in range(3):
        try:
            with urlopen(req, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f'Massive request failed (HTTP {error.code}); no publication') from None
        except (URLError, TimeoutError):
            if attempt == 2:
                raise RuntimeError('Massive connection failed; no publication') from None
        time.sleep(2 ** (attempt + 1))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    tickers = json.loads(Path('pattern-tickers.json').read_text())
    if not tickers or len(tickers) != len(set(tickers)) or any(not re.fullmatch(r'[A-Za-z0-9.^-]{1,24}', t) for t in tickers):
        raise ValueError('Invalid approved universe')
    sessions = calendar_sessions('market-sessions.json')
    now = datetime.now(timezone.utc)
    if not args.force and not should_fetch(now, sessions):
        print('Outside delayed extended-market collection window; retained previous snapshot')
        return
    result = acquire(tickers, sessions, now, provider_request)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(result, separators=(',', ':'), allow_nan=False) + '\n'
    path.with_suffix('.tmp').write_text(data)
    path.with_suffix('.tmp').replace(path)
    print(json.dumps({'requested': len(tickers), 'available': len(tickers)-len(result['missing']), 'missing': result['missing'], 'bytes': len(data.encode()), 'publishedAt': result['publishedAt']}))

if __name__ == '__main__':
    main()
