import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from export_mobile import export

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
            self.assertEqual({str(p.relative_to(output)) for p in output.rglob('*') if p.is_file()}, {'api/saved-stocks/AAPL.json', 'manifest.json', 'index.html', '.nojekyll'})
            payload = json.loads((output / 'api/saved-stocks/AAPL.json').read_text())
            self.assertEqual(payload['rows'][0]['time'], '2026-10-05')
            self.assertNotIn('private', payload['dataSource'])
            pq.write_table(pa.Table.from_pylist([row, row]), prices / 'adjusted_daily.parquet')
            with self.assertRaises(ValueError):
                export(root, Path(directory) / 'invalid')
            self.assertFalse((Path(directory) / 'invalid').exists())
