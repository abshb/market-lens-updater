import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from export_mobile import export, query_earnings_dates, model_contexts

class ExportTests(unittest.TestCase):
    def test_exports_only_prices_and_rejects_duplicates(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'state'
            prices = root / 'data/approved_large_mega/v1/prices'
            prices.mkdir(parents=True)
            (root / 'data/scheduled_refresh.json').write_text(json.dumps({'completedAt': '2026-10-06T14:00:00Z', 'session': '2026-10-05', 'private': 'secret'}))
            (root / 'secret.env').write_text('must not publish')
            row = dict(ticker='AAPL', date=datetime(2026,10,5), open=1., high=2., low=1., close=2., volume=100)
            pq.write_table(pa.Table.from_pylist([row]), prices / 'adjusted_daily.parquet')
            output = Path(directory) / 'site'
            export(root, output)
            self.assertEqual({str(p.relative_to(output)) for p in output.rglob('*') if p.is_file()}, {'api/saved-stocks/AAPL.json', 'manifest.json', 'pattern-input.json', 'index.html', '.nojekyll'})
            payload = json.loads((output / 'api/saved-stocks/AAPL.json').read_text())
            self.assertEqual(payload['rows'][0]['time'], '2026-10-05')
            self.assertNotIn('private', payload['dataSource'])
            pq.write_table(pa.Table.from_pylist([row, row]), prices / 'adjusted_daily.parquet')
            with self.assertRaises(ValueError):
                export(root, Path(directory) / 'invalid')
            self.assertFalse((Path(directory) / 'invalid').exists())

    def test_earnings_fields_and_missing_eps(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'earnings.parquet'
            pq.write_table(pa.Table.from_pylist([dict(ticker='AAPL', reported_date=datetime(datetime.now().year-1,11,1), reported_eps=1.5, estimated_eps=float('nan'), surprise_percentage=2.0, report_time='post-market', private='secret')]), path)
            events = query_earnings_dates('AAPL', path)
            actual = next(e for e in events if not e['estimated'])
            self.assertEqual(actual['reportedEps'], 1.5)
            self.assertIsNone(actual['estimatedEps'])
            self.assertNotIn('private', actual)
            self.assertTrue(any(e['estimated'] for e in events))
            json.dumps(events, allow_nan=False)

    def test_model_context_allowlist_and_availability_dates(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / 'data/approved_large_mega/v1'
            (base / 'earnings/raw/submissions').mkdir(parents=True)
            (base / 'training').mkdir()
            eps = root / 'data/external_free/earnings_surprises/v1'
            eps.mkdir(parents=True)
            (base / 'earnings/raw/submissions/CIK000001.json').write_text(json.dumps({'tickers': ['AAPL'], 'sic': '3571', 'private': 'secret'}))
            pq.write_table(pa.Table.from_pylist([
                {'ticker': 'AAPL', 'entry_date': datetime(2026,10,5), 'source_type': 'confirmed_8k_item_2_02', 'private': 'secret'},
                {'ticker': 'AAPL', 'entry_date': datetime(2026,10,8), 'source_type': 'confirmed_8k_item_2_02', 'private': 'secret'},
                {'ticker': 'AAPL', 'entry_date': datetime(2026,10,4), 'source_type': 'estimated', 'private': 'secret'},
            ]), base / 'earnings/events.parquet')
            pq.write_table(pa.Table.from_pylist([
                {'ticker': 'AAPL', 'available_date': datetime(2026,10,6), 'surprise_percentage': 5., 'report_time': 'post-market', 'private': 'secret'},
                {'ticker': 'AAPL', 'available_date': datetime(2026,10,8), 'surprise_percentage': 90., 'report_time': 'pre-market', 'private': 'secret'},
            ]), eps / 'earnings_surprises.parquet')
            pq.write_table(pa.Table.from_pylist([
                {'ticker': 'AAPL', 'entry_date': datetime(2026,10,5), 'market_cap_usd': 30e9, 'private': 'secret'},
                {'ticker': 'AAPL', 'entry_date': datetime(2026,10,8), 'market_cap_usd': 100e9, 'private': 'secret'},
            ]), base / 'training/point_in_time_caps.parquet')
            c = model_contexts(root, '2026-10-07')['AAPL']
            self.assertEqual(c, {'schema': 1, 'sector': 'Information Technology', 'earningsDates': ['2026-10-05'],
                'epsSurprises': [{'availableDate': '2026-10-06', 'surprisePercentage': 5., 'reportTime': 'post-market'}],
                'marketCap': {'date': '2026-10-05', 'usd': 30e9}})
            self.assertNotIn('secret', json.dumps(c))
