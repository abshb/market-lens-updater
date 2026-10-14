import unittest
from datetime import datetime, timezone
from snapshot_feed import acquire, normalize, should_fetch, phase

class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,14,22,30,tzinfo=timezone.utc)
        self.sessions={'2026-10-14':1792008000000} # 20:00 UTC
    def item(self,ticker='A', hour=22):
        timestamp=int(self.now.replace(hour=hour,minute=0).timestamp()*1000)
        return {'ticker':ticker,'lastTrade':{'p':110,'t':timestamp*1000000},'prevDay':{'c':100}}
    def test_batch_scope_and_missing(self):
        tickers=[f'A{i}' for i in range(203)];calls=[]
        def request(batch):
            calls.append(batch);return {'status':'OK','tickers':[self.item(t) for t in batch[:-1]]}
        value=acquire(tickers,self.sessions,self.now,request)
        self.assertEqual([len(x) for x in calls],[100,100,3]);self.assertEqual(len(value['missing']),3)
        self.assertEqual(value['stocks']['A0']['session'],'post')
    def test_premarket_and_early_close(self):
        stamp=int(self.now.replace(hour=12,minute=0).timestamp()*1000)
        self.assertEqual(phase(stamp,self.sessions),'pre')
        self.sessions['2026-10-14']=int(self.now.replace(hour=17,minute=0).timestamp()*1000)
        self.assertEqual(phase(stamp+6*3600000,self.sessions),'post')
    def test_weekend_and_collection(self):
        self.assertTrue(should_fetch(self.now,self.sessions))
        self.assertFalse(should_fetch(self.now.replace(day=17),self.sessions))
        self.assertFalse(should_fetch(self.now.replace(hour=7),self.sessions))
    def test_invalid_and_empty_fail(self):
        with self.assertRaises(ValueError):acquire(['A'],self.sessions,self.now,lambda b:{'status':'ERROR'})
        with self.assertRaises(ValueError):acquire(['A'],self.sessions,self.now,lambda b:{'status':'OK','tickers':[]})
        with self.assertRaises(ValueError):acquire(['A'],self.sessions,self.now,lambda b:{'status':'OK','tickers':[self.item('B')]})
    def test_no_fake_current_timestamp(self):
        self.assertIsNone(normalize({'day':{'c':10}},self.now,self.sessions))
        item=self.item();item['lastTrade']['t']*=1000
        self.assertIsNone(normalize(item,self.now,self.sessions))
        value=normalize(self.item(),self.now,self.sessions)
        self.assertEqual(value['asOf'],'2026-10-14T22:00:00+00:00')
        self.assertAlmostEqual(value['changePercent'],10)
if __name__=='__main__':unittest.main()
