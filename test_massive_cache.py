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
    def test_provider_quarantines_bad_prices_and_wrong_dates(self):
        p=c.Provider('fixture');bar={'T':'BRK.B','o':11,'h':12,'l':10,'c':11,'v':100,'t':datetime(2026,10,8,20,tzinfo=timezone.utc).timestamp()*1000}
        p.get=lambda *a,**k:{'adjusted':False,'results':[bar,{**bar,'T':'BadpA','c':0},{**bar,'T':'WRONG','t':1e100},{**bar,'T':None}]}
        rows,rejections=p.day('2026-10-08')
        self.assertEqual(list(rows),['BRK.B']);self.assertEqual(len(rejections),3)
    def test_packed_header_is_portable(self):
        import base64
        self.assertEqual(base64.b64decode(c.packed({'rows':[]})['data'])[9],255)
    def test_earnings_restoration_preserves_prices_and_model_context(self):
        idx=c.indexes(self.root);entry=idx[c.shard('T1')]['T1'];base=entry['base'];context={'schema':1,'sector':'Health Care'}
        entry['metadata']=c.immutable(self.root,'metadata','T1',{'earnings':[],'modelContext':context});c.publish_manifest(self.root,json.loads((self.root/'manifest.json').read_bytes()),idx)
        event={'date':'2026-10-01','reportedEps':1.2,'estimated':False}
        snapshot={'asOfDate':'2026-10-06','stocks':{'T1':[event]}}
        self.assertEqual(c.restore_earnings(self.root,snapshot)['earningsStocksUpdated'],1)
        entry=c.indexes(self.root)[c.shard('T1')]['T1'];payload=c.unpacked(self.root/entry['metadata']['path'])
        self.assertEqual(entry['base'],base);self.assertEqual(payload['modelContext'],context);self.assertEqual(payload['earnings'],[event])
        self.assertEqual(c.restore_earnings(self.root,snapshot)['earningsStocksUpdated'],0)
    def test_date_repair_uses_authoritative_row_and_is_idempotent(self):
        idx=c.indexes(self.root);entry=idx[c.shard('T1')]['T1']
        good=['2026-10-07',10,11,9,10,100];bad=['2026-10-07',11,12,10,11,100]
        entry['base']=c.immutable(self.root,'history','T1',{'ticker':'T1','rows':[bad,good]})
        c.publish_manifest(self.root,json.loads((self.root/'manifest.json').read_bytes()),idx)
        repair={'schema':1,'sourceFile':'wrong-date.csv.gz','stocks':{'T1':{'rejected':[bad],'authoritative':[good]}}}
        self.assertEqual(c.repair_history_dates(self.root,repair)['historyRepairs'],1)
        entry=c.indexes(self.root)[c.shard('T1')]['T1']
        self.assertEqual(c.unpacked(self.root/entry['base']['path'])['rows'],[good])
        self.assertEqual(c.repair_history_dates(self.root,repair)['historyRepairs'],0)
    def test_retention_protects_current_objects_and_uses_persisted_age(self):
        base=c.indexes(self.root)[c.shard('T1')]['T1']['base']
        obsolete=c.immutable(self.root,'metadata','orphan',{'unused':True});path=self.root/obsolete['path']
        c.prune_cache(self.root,NOW);self.assertTrue(path.exists())
        import os;os.utime(path,None)
        c.prune_cache(self.root,NOW+c.timedelta(days=8))
        self.assertFalse(path.exists());self.assertTrue((self.root/base['path']).exists())
    def test_approved_patterns_exclude_unrequested_history(self):
        c.build_pattern_input(self.root,['T1'])
        m=json.loads((self.root/'manifest.json').read_bytes());p=c.unpacked(self.root/m['patternInputApproved']['path'])
        self.assertEqual(p['tickers'],['T1']);self.assertEqual(p['total'],1)
        self.assertEqual(c.unpacked(self.root/p['shards'][0]['path'])['stocks'][0]['ticker'],'T1')
    def test_bootstrap_rejects_timestamp_filename_mismatch(self):
        import csv,gzip
        raw=self.root/'raw-input';raw.mkdir();folder=raw/'raw'/'2026';folder.mkdir(parents=True)
        file=folder/'2026-10-07.csv.gz'
        with gzip.open(file,'wt',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(['ticker','volume','open','close','high','low','window_start','transactions'])
            for day in ['2026-10-07','2026-10-08']:
                writer.writerow(['TEST',100,10,10,11,9,int(datetime.fromisoformat(day+'T04:00:00+00:00').timestamp()*1e9),1])
        (raw/'inventory.json').write_text(json.dumps([{'date':'2026-10-07','key':file.name}]))
        target=self.root/'bootstrap';target.mkdir()
        c.bootstrap(raw,target,Fake(),[['2026-10-07',NOW.timestamp()*1000]])
        entry=c.indexes(target)[c.shard('TEST')]['TEST']
        self.assertEqual(c.unpacked(target/entry['base']['path'])['rows'],[['2026-10-07',10.,11.,9.,10.,100.]])
        self.assertEqual(json.loads((target/'manifest.json').read_bytes())['invalidRowsSkipped'],1)
    def test_invalid_rows(self):
        for row in [[],None,['2026-10-08',0,12,10,11,100],['2026-10-08',11,9,10,11,100],['2026-10-08',11,12,10,11,-1]]:self.assertFalse(c.valid(row))
if __name__=='__main__':unittest.main()
