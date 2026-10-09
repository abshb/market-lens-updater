import unittest,tempfile,json
from pathlib import Path
from datetime import datetime,timezone
import massive_cache as c
NOW=datetime(2026,10,9,4,tzinfo=timezone.utc)
class Fake:
    calls=0
    def __init__(self,bad=False,partial=False):self.bad=bad;self.partial=partial
    def day(self,day):
        self.calls+=1
        bars={f'T{i}':[day,11,12,10,11,100] for i in range(60)}
        invalid=[]
        if self.bad:bars.pop('T0');invalid=[{'ticker':'T0','date':day,'reason':'invalid price'}]
        if self.partial:bars=dict(list(bars.items())[:5])
        return bars,invalid
    def splits(self,a,b):self.calls+=1;return [{'ticker':'T1','execution_date':'2026-10-08','split_from':1,'split_to':2}]
class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);index={}
        for i in range(60):
            t=f'T{i}';base=c.immutable(self.root,'history',t,{'ticker':t,'rows':[['2026-10-07',10,11,9,10,100]]})
            index.setdefault(c.shard(t),{})[t]={'base':base,'baseEnd':'2026-10-07','revision':base['sha256'],'splits':[],'lastDate':'2026-10-07'}
        calendar=[[day,datetime.fromisoformat(day+'T20:00:00+00:00').timestamp()*1000] for day in ['2026-10-06','2026-10-07','2026-10-08','2026-10-09']]
        c.publish_manifest(self.root,{'schema':1,'provider':'github','session':'2026-10-07','calendar':calendar,'tickers':[f'T{i}' for i in range(60)],'liveStocks':[]},index)
    def tearDown(self):self.temp.cleanup()
    def test_bad_row_preserves_prior_and_updates_peers(self):
        before=c.indexes(self.root)[c.shard('T0')]['T0']
        result=c.refresh_daily(self.root,Fake(bad=True),NOW)
        after=c.indexes(self.root)
        self.assertEqual(after[c.shard('T0')]['T0'],before)
        self.assertEqual(after[c.shard('T1')]['T1']['lastDate'],'2026-10-08')
        self.assertEqual(result['changedStocks'],59);self.assertEqual(result['invalidRowsRejected'],3)
        self.assertEqual(after[c.shard('T1')]['T1']['splits'],[{'date':'2026-10-08','factor':.5}])
    def test_corrections_do_not_rewrite_baseline(self):
        before=c.indexes(self.root)[c.shard('T1')]['T1']['base'];raw=(self.root/before['path']).read_bytes()
        c.refresh_daily(self.root,Fake(),NOW);entry=c.indexes(self.root)[c.shard('T1')]['T1']
        self.assertEqual(entry['base'],before);self.assertEqual((self.root/before['path']).read_bytes(),raw)
        self.assertEqual(c.unpacked(self.root/entry['delta']['path'])['rows'][-1][0],'2026-10-08')
        result=c.refresh_daily(self.root,Fake(),NOW);self.assertEqual(result['changedStocks'],0)
    def test_incomplete_publication_keeps_manifest(self):
        old=(self.root/'manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'Incomplete'):c.refresh_daily(self.root,Fake(partial=True),NOW)
        self.assertEqual((self.root/'manifest.json').read_bytes(),old)
    def test_no_allowlist_no_provider_calls(self):
        f=Fake();self.assertEqual(c.refresh_live(self.root,f,[],NOW)['providerRequests'],0)
    def test_invalid_rows(self):
        for row in [[],None,['2026-10-08',0,12,10,11,100],['2026-10-08',11,9,10,11,100],['2026-10-08',11,12,10,11,-1]]:self.assertFalse(c.valid(row))
if __name__=='__main__':unittest.main()
