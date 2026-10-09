"""Incremental Massive stock cache. Provider requests run only on the updater."""
from __future__ import annotations
import argparse, base64, csv, gzip, hashlib, json, math, os, re, time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

TICKER = re.compile(r'^[A-Za-z0-9.^-]{1,24}$')
REQUIRED = ['time','open','high','low','close','volume']


def encoded(value):
    return json.dumps(value, separators=(',', ':'), allow_nan=False, sort_keys=True).encode()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = encoded(value)
    if path.exists() and path.read_bytes() == raw:
        return False
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_bytes(raw); tmp.replace(path)
    return True


def packed(value):
    compressed=bytearray(gzip.compress(encoded(value),mtime=0))
    compressed[9]=255 # Stable gzip header on macOS, Linux, and Python versions.
    return {'encoding':'gzip-base64-v1','data':base64.b64encode(compressed).decode()}


def unpacked(path):
    value = json.loads(path.read_bytes())
    if value.get('encoding') == 'gzip-base64-v1':
        return json.loads(gzip.decompress(base64.b64decode(value['data'])))
    return value


def shard(ticker):
    return hashlib.sha256(ticker.encode()).hexdigest()[:2]


def valid(row):
    return isinstance(row,(list,tuple)) and len(row)==6 and bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(row[0]))) and all(isinstance(v,(int,float)) and math.isfinite(v) for v in row[1:]) and min(row[1:5])>0 and row[5]>=0 and row[3]<=min(row[1],row[4])<=max(row[1],row[4])<=row[2]


def immutable(root, kind, ticker, value):
    payload=packed(value); digest=hashlib.sha256(encoded(payload)).hexdigest()
    path=f'{kind}/{shard(ticker)}/{ticker}-{digest[:16]}.json'
    save(root/path,payload)
    return {'path':path,'sha256':digest}


class Provider:
    def __init__(self,key): self.key=key; self.calls=0
    def get(self,path,params=None):
        url=path if path.startswith('https:') else 'https://api.massive.com'+path
        if urlparse(url).hostname!='api.massive.com': raise ValueError('Unexpected pagination host')
        if params: url+='?'+urlencode(params)
        # Never allow credentials into a URL or logs.
        for attempt in range(3):
            self.calls+=1
            try:
                with urlopen(Request(url,headers={'Authorization':'Bearer '+self.key,'Accept':'application/json'}),timeout=45) as res: return json.load(res)
            except HTTPError as error:
                if error.code not in (429,500,502,503,504) or attempt==2: raise RuntimeError(f'Massive HTTP {error.code}') from None
                time.sleep(2**attempt)
        raise RuntimeError('Provider request failed')
    def splits(self,start,end):
        data=self.get('/stocks/v1/splits',{'execution_date.gte':start,'execution_date.lte':end,'limit':5000,'sort':'execution_date.asc'})
        result=list(data.get('results',[]))
        while data.get('next_url'):
            data=self.get(data['next_url']);result.extend(data.get('results',[]))
        return result
    def day(self,day):
        data=self.get('/v2/aggs/grouped/locale/us/market/stocks/'+day,{'adjusted':'false','include_otc':'false'})
        if data.get('adjusted') is not False: raise ValueError('Grouped prices must be unadjusted')
        result={}; invalid=[]
        for bar in data.get('results',[]):
            ticker=bar.get('T',''); row=[day,bar.get('o'),bar.get('h'),bar.get('l'),bar.get('c'),bar.get('v')]
            timestamp=bar.get('t')
            try:dated=isinstance(timestamp,(int,float)) and math.isfinite(timestamp) and datetime.fromtimestamp(timestamp/1000,timezone.utc).date().isoformat()==day
            except (ValueError,OverflowError,OSError):dated=False
            if isinstance(ticker,str) and TICKER.fullmatch(ticker) and valid(row) and dated: result[ticker]=row
            else: invalid.append({'ticker':ticker,'date':day,'reason':'Invalid symbol or OHLCV'})
        return result,invalid


def indexes(root):
    return {p.stem:json.loads(p.read_bytes()) for p in (root/'index').glob('*.json')} if (root/'index').exists() else {}


def publish_manifest(root,manifest,index):
    for bucket,entries in index.items():
        payload={'schema':1,'stocks':entries}
        raw=encoded(payload);digest=hashlib.sha256(raw).hexdigest()
        path=f'indexes/{bucket}-{digest[:16]}.json'
        save(root/path,payload)
        manifest.setdefault('indexes',{})[bucket]={'path':path,'sha256':digest}
        save(root/'index'/f'{bucket}.json',entries) # updater state, not used by mobile
    manifest['updatedAt']=datetime.now(timezone.utc).isoformat()
    save(root/'manifest.json',manifest)


def split_map(events):
    result=defaultdict(list)
    for event in events:
        ticker=event.get('ticker','');day=event.get('execution_date','')
        a,b=event.get('split_from'),event.get('split_to')
        if isinstance(ticker,str) and TICKER.fullmatch(ticker) and isinstance(a,(int,float)) and isinstance(b,(int,float)) and a>0 and b>0 and re.fullmatch(r'\d{4}-\d{2}-\d{2}',day):
            result[ticker].append({'date':day,'factor':a/b})
    return result


def bootstrap(raw,root,provider,calendar,context_root=None):
    import duckdb
    inventory=json.loads((raw/'inventory.json').read_bytes())
    first,last=inventory[0]['date'],inventory[-1]['date']
    print(f'Building initial cache from {len(inventory)} local daily files.',flush=True)
    splits=split_map(provider.splits(first,last))
    db=duckdb.connect(str(root.parent/'massive-bootstrap.duckdb'))
    db.execute("SET threads=4")
    files=[str(raw/'raw'/item['date'][:4]/Path(item['key']).name) for item in inventory]
    db.execute('CREATE OR REPLACE TABLE candles AS SELECT *, strftime(to_timestamp(window_start/1000000000),\'%Y-%m-%d\') AS day FROM read_csv(?,header=true,union_by_name=true)',[files])
    tickers=[r[0] for r in db.execute('SELECT DISTINCT ticker FROM candles ORDER BY ticker').fetchall()]
    index=indexes(root); count=0; invalid_count=0; unsupported=[]
    db.execute('CREATE INDEX IF NOT EXISTS ticker_index ON candles(ticker)')
    for i,ticker in enumerate(tickers):
        if not TICKER.fullmatch(ticker):unsupported.append(ticker);continue
        if ticker in index.get(shard(ticker),{}):continue
        # Indexed storage makes one stock query cheap; grouping avoids repeated full scans.
        rows=db.execute('SELECT day,open,high,low,close,volume FROM candles WHERE ticker=? ORDER BY day',[ticker]).fetchall()
        good=[list(row) for row in rows if valid(row)];invalid_count+=len(rows)-len(good)
        if not good: continue
        base=immutable(root,'history',ticker,{'ticker':ticker,'rows':good})
        entry={'base':base,'baseEnd':good[-1][0],'revision':base['sha256'],'splits':splits.get(ticker,[]),'lastDate':good[-1][0]}
        if context_root and (context_root/f'{ticker}.json').exists():
            old=json.loads((context_root/f'{ticker}.json').read_bytes())
            metadata={'earnings':old.get('earnings',[]),'modelContext':old.get('modelContext')}
            entry['metadata']=immutable(root,'metadata',ticker,metadata)
        index.setdefault(shard(ticker),{})[ticker]=entry;count+=len(good)
        if (i+1)%2000==0: print(f'Cached {i+1}/{len(tickers)} tickers',flush=True)
    db.close()
    (root.parent/'massive-bootstrap.duckdb').unlink(missing_ok=True)
    manifest={'schema':1,'provider':'github','origin':'massive','encoding':'gzip-base64-v1','firstDate':first,'session':last,'baselineSession':last,'inputSchema':1,'intradayDelayMinutes':15,'liveStocks':[],'calendar':calendar,'tickers':sorted(t for entries in index.values() for t,e in entries.items() if e['lastDate']==last),'historyTickerCount':sum(map(len,index.values())),'invalidRowsSkipped':invalid_count,'unsupportedSymbols':unsupported}
    publish_manifest(root,manifest,index)
    print(f'Initial cache: {manifest["historyTickerCount"]} tickers, {count} valid daily candles.',flush=True)


def refresh_daily(root,provider,now):
    manifest=json.loads((root/'manifest.json').read_bytes());index=indexes(root)
    cutoff=now.timestamp()-1800
    completed=[day for day,close in manifest['calendar'] if close/1000<=cutoff]
    if now.timestamp()*1000>manifest['calendar'][-1][1]+7*86400000:raise ValueError('Market calendar expired')
    if not completed:raise ValueError('Calendar has no completed session')
    target=completed[-1]
    if target<manifest['session']:raise ValueError('Cannot regress saved session')
    # Three completed sessions repair recent vendor corrections; backfill every missed session.
    days=[day for day in completed if day>manifest['session'] or day in completed[-3:]]
    downloaded=[];rejected=[]
    for day in days:
        bars,invalid=provider.day(day)
        if not bars or len(bars)<max(50,int(len(manifest['tickers'])*0.8)):
            raise ValueError(f'Incomplete market-wide daily response for {day}')
        downloaded.append((day,bars));rejected.extend(invalid)
    events=split_map(provider.splits(days[0],target))
    changed=set()
    for day,bars in downloaded:
        for ticker,row in bars.items():
            entries=index.setdefault(shard(ticker),{});entry=entries.get(ticker)
            if not entry:
                # Newly seen symbols get one seed candle; history backfill is a separate queued task.
                base=immutable(root,'history',ticker,{'ticker':ticker,'rows':[row]})
                entry=entries[ticker]={'base':base,'baseEnd':day,'revision':base['sha256'],'splits':[],'lastDate':day,'historyBackfillNeeded':True}
            delta=unpacked(root/entry['delta']['path'])['rows'] if entry.get('delta') else []
            merged={r[0]:r for r in delta}
            # Corrections override baseline candles without downloading or rewriting baseline history.
            merged[day]=row
            updates=[merged[d] for d in sorted(merged)]
            pointer=immutable(root,'deltas',ticker,{'ticker':ticker,'rows':updates})
            prior_splits=[s for s in entry.get('splits',[]) if s['date']<days[0]]
            current_splits=prior_splits+events.get(ticker,[])
            revision_fields={'base':entry['base'],'delta':pointer,'splits':current_splits}
            if entry.get('metadata'):revision_fields['metadata']=entry['metadata']
            revision=hashlib.sha256(encoded(revision_fields)).hexdigest()
            if entry['revision']!=revision:changed.add(ticker)
            entry.update(delta=pointer,splits=current_splits,revision=revision,lastDate=max(day,entry['lastDate']))
    manifest['session']=target;manifest['tickers']=sorted(downloaded[-1][1]);manifest['historyTickerCount']=sum(map(len,index.values()))
    manifest['lastDailyCheck']=now.date().isoformat()
    manifest['lastRefresh']={'changedStocks':len(changed),'providerRequests':provider.calls,'sessions':days,'invalidRowsRejected':len(rejected)}
    if rejected:save(root/'rejections'/f'{target}.json',rejected)
    publish_manifest(root,manifest,index)
    return manifest['lastRefresh']


def build_pattern_input(root):
    manifest=json.loads((root/'manifest.json').read_bytes());index=indexes(root);stocks=[]
    for ticker in manifest['tickers']:
        entry=index[shard(ticker)][ticker]
        rows=unpacked(root/entry['base']['path'])['rows']
        merged={r[0]:r for r in rows}
        if entry.get('delta'):
            merged.update({r[0]:r for r in unpacked(root/entry['delta']['path'])['rows']})
        adjusted=[]
        for day,o,h,l,c,v in [merged[d] for d in sorted(merged) if d<=manifest['session']]:
            factor=math.prod(e['factor'] for e in entry['splits'] if day<e['date']<=manifest['session'])
            adjusted.append([day,o*factor,h*factor,l*factor,c*factor,v/factor])
        if not adjusted:continue
        ema=adjusted[0][4]
        for row in adjusted[1:]:ema+=(row[4]-ema)*(2/22)
        stocks.append({'ticker':ticker,'rows':adjusted[-252:],'ema21':ema if len(adjusted)>=21 else None,'sma200':sum(r[4] for r in adjusted[-200:])/200 if len(adjusted)>=200 else None})
    source={'provider':'github','updatedAt':manifest['updatedAt'],'session':manifest['session']}
    chunks=[immutable(root,'metadata',f'pattern-{i//250}',{'stocks':stocks[i:i+250]}) for i in range(0,len(stocks),250)]
    manifest['patternInput']=immutable(root,'metadata','pattern-input',{'dataSource':source,'total':len(stocks),'shards':chunks})
    save(root/'manifest.json',manifest)
    print(f'Pattern input: {len(stocks)} stocks',flush=True)


def restore_earnings(root,snapshot):
    manifest=json.loads((root/'manifest.json').read_bytes());index=indexes(root);changed=0
    for ticker,events in snapshot['stocks'].items():
        entry=index.get(shard(ticker),{}).get(ticker)
        if entry is None:continue
        if not isinstance(events,list) or any(not isinstance(e,dict) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',e.get('date','')) for e in events):raise ValueError('Invalid earnings snapshot')
        old=unpacked(root/entry['metadata']['path']) if entry.get('metadata') else {}
        if old.get('earnings')==events:continue
        old['earnings']=events
        entry['metadata']=immutable(root,'metadata',ticker,old)
        # Metadata corrections must invalidate the phone's merged stock cache too.
        entry['revision']=hashlib.sha256(encoded({k:entry[k] for k in ['base','delta','splits','metadata'] if k in entry})).hexdigest()
        changed+=1
    if changed:
        manifest['earningsSnapshotAsOf']=snapshot['asOfDate']
        publish_manifest(root,manifest,index)
    return {'earningsStocksUpdated':changed}


def refresh_live(root,provider,allowlist,now):
    if not isinstance(allowlist,list) or len(allowlist)>100 or any(not isinstance(t,str) or not TICKER.fullmatch(t) for t in allowlist):raise ValueError('Invalid live stock allowlist')
    current=json.loads((root/'manifest.json').read_bytes())
    if not allowlist:
        # Empty means zero intraday provider calls, and removes any previously published eligibility.
        if current.get('liveStocks'):
            current['liveStocks']=[];save(root/'manifest.json',current)
        return {'liveStocks':0,'providerRequests':provider.calls}
    from zoneinfo import ZoneInfo
    day=now.astimezone(ZoneInfo('America/New_York')).date().isoformat()
    for ticker in allowlist:
        data=provider.get(f'/v2/aggs/ticker/{ticker}/range/1/day/{day}/{day}',{'adjusted':'false','sort':'desc','limit':1})
        bars=data.get('results',[])
        if bars:
            b=bars[-1];row=[day,b.get('o'),b.get('h'),b.get('l'),b.get('c'),b.get('v')]
            if not valid(row):raise ValueError('Invalid live candle')
            save(root/'live'/f'{ticker}.json',{'ticker':ticker,'row':row,'forming':True,'delayMinutes':15,'fetchedAt':now.isoformat()})
    current['liveStocks']=allowlist;current['liveUpdatedAt']=now.isoformat();save(root/'manifest.json',current)
    return {'liveStocks':len(allowlist),'providerRequests':provider.calls}


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['bootstrap','refresh']);p.add_argument('--cache',required=True);p.add_argument('--raw');p.add_argument('--context');p.add_argument('--calendar',required=True);p.add_argument('--allowlist',required=True);p.add_argument('--env-file');p.add_argument('--force-daily',action='store_true');p.add_argument('--earnings');args=p.parse_args()
    if args.env_file:
        for line in Path(args.env_file).read_text().splitlines():
            if '=' in line and not line.startswith('#'):k,v=line.split('=',1);os.environ[k]=v
    provider=Provider(os.environ['MASSIVE_API_KEY']);root=Path(args.cache);root.mkdir(parents=True,exist_ok=True)
    calendar=json.loads(Path(args.calendar).read_bytes())['sessions'];now=datetime.now(timezone.utc)
    if args.mode=='bootstrap':bootstrap(Path(args.raw),root,provider,calendar,Path(args.context) if args.context else None)
    else:
        manifest=json.loads((root/'manifest.json').read_bytes())
        latest=max(day for day,close in calendar if close/1000<=now.timestamp()-1800)
        if args.force_daily or latest>manifest['session'] or manifest.get('lastDailyCheck')!=now.date().isoformat():print(json.dumps(refresh_daily(root,provider,now)),flush=True);build_pattern_input(root)
        if args.earnings:print(json.dumps(restore_earnings(root,json.loads(Path(args.earnings).read_bytes()))),flush=True)
        print(json.dumps(refresh_live(root,provider,json.loads(Path(args.allowlist).read_bytes()),now)),flush=True)


if __name__=='__main__':main()
