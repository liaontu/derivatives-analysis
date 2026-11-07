"""Fixed institutional-flow event study with dated price evidence, not trade P&L."""
from collections import Counter,defaultdict
from datetime import date,datetime,timedelta,timezone
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from statistics import median
import argparse,json,math
try:
    from .daily_reports import read,save
    from .institutional_history import sessions
    from .trading_calendar import load_calendar
except ImportError:
    from daily_reports import read,save
    from institutional_history import sessions
    from trading_calendar import load_calendar

RULES={'version':'institutional-event-study-v1','streak':3,'horizons':[1,3,5],'cooldownSessions':7,
       'baseline':'訊號後首個交易日收盤，觀察其後 1／3／5 個交易日；缺價不順延。',
       'warrantSelection':'依訊號當日量價日報，選對應權別放量且成交量最高的一檔；同量依代碼排序。',
       'minVolume':100,'volumeMultiple':2,'minimumSamples':30,'minimumDates':10}
STRATEGIES=[{'id':'foreign-buy','name':'外資連買三日','field':'foreign','sign':1,'cp':'C','confirmation':False},
            {'id':'foreign-buy-call','name':'外資連買＋認購放量','field':'foreign','sign':1,'cp':'C','confirmation':True},
            {'id':'trust-buy-call','name':'投信連買＋認購放量','field':'trust','sign':1,'cp':'C','confirmation':True},
            {'id':'foreign-sell-put','name':'外資連賣＋認售放量','field':'foreign','sign':-1,'cp':'P','confirmation':True}]
LIMITS=['全部為固定規則的價格事件研究，沒有真實下單、成交驗證或資金帳戶損益。',
        '歷史法人與權證量價是事後補抓；以目前權證標的名錄篩選，可能漏掉已退出名錄的標的。',
        '現股與權證價格均未還原；除權息、條款變更可能影響變動率。未驗證歷史完整條款、買賣價差、可成交量與避險成本，不解讀為可實現報酬。',
        '同股同規則七個交易日內去重；不同規則或同日股票仍有相關性，樣本不是獨立交易。',
        '前後段依首次研究區間的交易日 70/30 固定切分，跨界觀察排除於分段統計；兩段都是歷史研究，非即時樣本外績效。',
        '上市、上櫃分市場統計；法人分類與券商分點不能識別同一投資人。樣本不足不評選較佳策略。']


def finite(x):return isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x)
def digest(x):return sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def optional(path,default):return read(path) if path.exists() else default
def key(row):return row['market']+':'+row['code']


def next_session(calendar,day,n=1):
    cursor=date.fromisoformat(day)
    for _ in range(n):
        cursor+=timedelta(days=1)
        while not calendar.is_open(cursor):cursor+=timedelta(days=1)
    return cursor.isoformat()


def trend(rows_by_day,calendar,day,stock):
    dates=[day];cursor=date.fromisoformat(day)
    for _ in range(4):
        cursor-=timedelta(days=1)
        while not calendar.is_open(cursor):cursor-=timedelta(days=1)
        dates.append(cursor.isoformat())
    result={}
    for field in ('foreign','trust','dealerOwn','dealerHedge','total'):
        values=[rows_by_day.get(d,{}).get(stock,{}).get(field) for d in dates]
        for count in (3,5):result[field+str(count)]=sum(values[:count]) if all(finite(v) for v in values[:count]) else None
        # A signed streak is explicitly capped at the five-session window.
        sign=1 if finite(values[0]) and values[0]>0 else -1 if finite(values[0]) and values[0]<0 else 0
        streak=0
        if sign:
            for value in values:
                if not finite(value) or value*sign<=0:break
                streak+=sign
        result[field+'Streak5']=streak if finite(values[0]) else None
    result['basisDates']=dates
    return result


def summarize(events,as_of,start=None,end=None,cutoff=None,observation_start=None):
    end=min(end or as_of,as_of);groups=[]
    selected=[e for e in events if (not start or e['date']>=start) and e['date']<=end]
    for market in ('TWSE','TPEX'):
      for strategy in STRATEGIES:
       for horizon in RULES['horizons']:
        for phase in ('all','early','later'):
            rows=[];statuses=Counter()
            for event in selected:
                if event['market']!=market or event['rule']!=strategy['id']:continue
                outcome=event['outcomes'][str(horizon)];split=outcome['phase']
                if observation_start and (outcome['exitDate']<observation_start or (outcome['exitDate']>end and event['date']<observation_start)):continue
                if phase!='all' and split!=phase:
                    if split=='boundary':statuses['boundary-excluded']+=1
                    continue
                if event.get('inputRevised'):
                    statuses['source-revised']+=1;continue
                if outcome['exitDate']>end:
                    statuses['pending']+=1;continue
                statuses[outcome['status']]+=1;rows.append((event,outcome))
            stocks=[o['stockPct'] for _,o in rows if finite(o['stockPct'])]
            warrants=[o['warrantPct'] for _,o in rows if finite(o['warrantPct'])]
            dates=len({e['date'] for e,o in rows if finite(o['stockPct'])})
            pairs=[o for _,o in rows if finite(o['stockPct']) and finite(o['warrantPct'])]
            groups.append({'market':market,'rule':strategy['id'],'label':strategy['name'],'horizon':horizon,'phase':phase,
                'events':sum(statuses.values()),'statuses':dict(statuses),'stockSamples':len(stocks),'warrantSamples':len(warrants),'pairedSamples':len(pairs),'signalDates':dates,
                'stockMedianPct':median(stocks) if stocks else None,'stockDirectionShare':sum(v*strategy['sign']>0 for v in stocks)/len(stocks)*100 if stocks else None,
                'warrantMedianPct':median(warrants) if warrants else None,'warrantPositiveShare':sum(v>0 for v in warrants)/len(warrants)*100 if warrants else None,
                'sampleStatus':'僅描述統計，非策略驗證' if len(stocks)>=30 and dates>=10 else '樣本或日期不足，僅列觀察'})
    return groups


def collect(output,now=None):
    now=now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    index=read(output/'stock-institutional.json');calendar=load_calendar(output)
    snapshots={d:read(output/'stock-institutional'/(d+'.json')) for d in index['dates']}
    for d,snapshot in snapshots.items():
        if snapshot.get('date')!=d or snapshot.get('status')!='ok':raise ValueError('Unverified institutional input date')
        if len({key(r) for r in snapshot['rows']})!=len(snapshot['rows']):raise ValueError('Duplicate institutional stock')
    maps={d:{key(r):r for r in snapshot['rows']} for d,snapshot in snapshots.items()}
    as_of=max(snapshots);history=optional(output/'stock-institutional-backfill.json',{})
    folder=output/'stock-institutional-research'
    cohort=optional(folder/'cohort.json',None)
    if cohort and cohort['rules']!=RULES:raise ValueError('Research rule version changed; archive before migration')
    start=cohort['start'] if cohort else history.get('range',{}).get('start',min(snapshots))
    available=sessions(calendar,start,as_of)
    if not available:raise ValueError('No institutional research sessions')
    if not cohort:
        cutoff=available[max(0,int(len(available)*.7)-1)]
        cohort={'rules':RULES,'createdAt':now,'start':start,'initialEnd':as_of,'splitDate':cutoff,'mode':'historical-research','items':[]}
    cutoff=cohort['splitDate'];events={e['id']:e for e in cohort['items']};ordinal={d:i for i,d in enumerate(available)}
    last={};revised=set();present=set();frozen=defaultdict(list)
    for event in cohort['items']:
        frozen[(key(event),event['rule'])].append(ordinal[event['date']])
    @lru_cache(maxsize=None)
    def volume(day):
        data=optional(output/'daily-reports/warrant-volume'/(day+'.json'),{})
        return {r['code']:r for r in data.get('stocks',[])} if data.get('date')==day else {}
    for day in sorted(maps):
        trends={}
        for stock,row in maps[day].items():
            trends[stock]=trend(maps,calendar,day,stock)
            if day<start:continue
            dates=list(reversed(trends[stock]['basisDates'][:3]))
            for strategy in STRATEGIES:
                identity=day+':'+stock+':'+strategy['id'];event_id=sha256(identity.encode()).hexdigest()[:24]
                flows=[maps.get(d,{}).get(stock,{}).get(strategy['field']) for d in dates]
                qualifies=all(finite(v) and v*strategy['sign']>0 for v in flows)
                candidates=[r for r in volume(day).get(row['code'],{}).get('hits',[]) if r.get('cp')==strategy['cp'] and finite(r.get('volume')) and r['volume']>=100 and finite(r.get('multiple')) and r['multiple']>=2]
                candidate=sorted(candidates,key=lambda r:(-r['volume'],r['code']))[0] if candidates else None
                if strategy['confirmation'] and not candidate:qualifies=False
                evidence={'dates':dates,'flows':flows,'warrant':candidate}
                input_hash=digest(evidence)
                if event_id in events:
                    present.add(event_id)
                    if not qualifies or events[event_id]['inputSha256']!=input_hash:revised.add(event_id)
                    last[(stock,strategy['id'])]=ordinal[day]
                    continue
                if not qualifies:continue
                # Late repairs cannot insert an earlier event inside an already frozen window.
                if any(abs(ordinal[day]-value)<RULES['cooldownSessions'] for value in frozen[(stock,strategy['id'])]):continue
                prior=last.get((stock,strategy['id']))
                if prior is not None and ordinal[day]-prior<RULES['cooldownSessions']:continue
                last[(stock,strategy['id'])]=ordinal[day]
                events[event_id]={'id':event_id,'date':day,'code':row['code'],'name':row['name'],'market':row['market'],'rule':strategy['id'],
                    'warrant':candidate,'evidence':evidence,'inputSha256':input_hash,'firstRecordedAt':now,'mode':'historical-research',
                    'sourceHashes':[{'date':d,'sources':[s['sha256'] for s in snapshots[d]['sources']]} for d in dates]}
                present.add(event_id)
        save(output/'stock-institutional-trends'/(day+'.json'),{'date':day,'rows':trends,'streakWindow':5})
    cohort['items']=sorted(events.values(),key=lambda e:(e['date'],e['market'],e['code'],e['rule']))
    prices=optional(folder/'prices.json',{'stocks':{},'warrants':{},'revisions':[]})
    @lru_cache(maxsize=None)
    def price_history(code):return optional(output/'warrant-history'/(code+'.json'),{})
    def remember(kind,identity,day,value):
        saved=prices[kind].setdefault(identity,{})
        if value is not None:
            if day in saved and saved[day]!=value:prices['revisions'].append({'kind':kind,'identity':identity,'date':day,'before':saved[day],'after':value,'recordedAt':now})
            saved[day]=value
        return saved.get(day)
    results=[]
    for event in cohort['items']:
        ph=price_history(event['code']);spots=dict(ph.get('underlyingPrices',[]));candidate=event['warrant']
        warrant=ph.get('quotes',{}).get(candidate['code'],{}) if candidate else {}
        identity_ok=bool(candidate and warrant.get('name')==candidate.get('name') and warrant.get('market')==event['market'] and warrant.get('cp')==candidate.get('cp'))
        quoted={r[0]:r[1:3] for r in warrant.get('rows',[])} if identity_ok else {}
        def spot(day):
            value=spots.get(day);return remember('stocks',key(event),day,value if finite(value) and value>0 else None)
        def quote(day):
            if not candidate:return None
            value=remember('warrants',event['market']+':'+candidate['code'],day,quoted.get(day))
            return value[0] if value and all(finite(v) and v>0 for v in value) else None
        entry=next_session(calendar,event['date']);outcomes={}
        for horizon in RULES['horizons']:
            end=next_session(calendar,entry,horizon)
            phase='early' if end<=cutoff else 'later' if event['date']>cutoff else 'boundary'
            row={'entryDate':entry,'exitDate':end,'phase':phase,'stockPct':None,'warrantPct':None,'status':'pending'}
            if end<=as_of:
                base,close=spot(entry),spot(end);wb,wc=quote(entry),quote(end)
                row.update(stockPct=(close/base-1)*100 if base and close else None,warrantPct=(wc/wb-1)*100 if wb and wc else None)
                row['status']='paired' if row['stockPct'] is not None and row['warrantPct'] is not None else 'stock-only' if row['stockPct'] is not None else 'warrant-only' if row['warrantPct'] is not None else 'missing-prices'
                row['warrantStatus']='no-signal-warrant' if not candidate else 'unadjusted-close-observation' if wb and wc else 'missing-or-untraded-price'
            outcomes[str(horizon)]=row
        results.append({**event,'inputRevised':event['id'] in revised or event['id'] not in present,'outcomes':outcomes})
    save(folder/'cohort.json',cohort);save(folder/'prices.json',prices)
    outcome_data={'rules':RULES,'asOf':as_of,'splitDate':cutoff,'items':results,'mode':'historical-research'}
    save(folder/'outcomes.json',outcome_data)
    result={'schemaVersion':1,'status':'ok' if index['status']=='ok' and history.get('status','ok')=='ok' else 'partial',
            'asOf':as_of,'start':start,'splitDate':cutoff,'rules':RULES,'strategies':STRATEGIES,'mode':'historical-research','limitations':LIMITS,
            'days':len(snapshots),'analysisDays':len([d for d in snapshots if d>=start]),'stockDaySamples':sum(len(v) for v in maps.values()),
            'analysisStockDaySamples':sum(len(v) for d,v in maps.items() if d>=start),
            'missingVolumeDates':[d for d in snapshots if d>=start and not volume(d)],
            'events':len(results),'revisedEvents':sum(e['inputRevised'] for e in results),'priceRevisions':len(prices['revisions']),
            'inputSha256':digest({d:digest(s) for d,s in snapshots.items()}),'rows':summarize(results,as_of,cutoff=cutoff),
            'recentEvents':[e for market in ('TWSE','TPEX') for e in list(reversed([r for r in results if r['market']==market]))[:40]],'missingDates':history.get('missingDates',[])}
    save(output/'stock-institutional-research.json',result);return result


def period_review(output,start,end):
    index=optional(output/'stock-institutional.json',{});calendar=load_calendar(output)
    expected=sessions(calendar,start,end);days=[d for d in index.get('dates',[]) if start<=d<=end]
    grouped=defaultdict(list);sources=[];paired=0
    for day in days:
        report=read(output/'stock-institutional'/(day+'.json'))
        if report.get('date')!=day or report.get('status')!='ok':raise ValueError('Institutional report date mismatch')
        sources.append({'date':day,'sha256':digest(report),'sources':report.get('sources',[])})
        for row in report['rows']:
            grouped[key(row)].append(row)
            paired+=finite(row.get('stockPct')) and finite(row.get('volumeEligible')) and row['volumeEligible']>0
    stocks=[]
    for stock,rows in grouped.items():
        last=rows[-1];item={k:last[k] for k in ('code','name','market')}
        item.update(days=len(rows),missingDays=len(expected)-len(rows))
        for field in ('foreign','trust','dealerOwn','dealerHedge','total'):item[field]=sum(r[field] for r in rows)
        stocks.append(item)
    leaders={market:sorted((r for r in stocks if r['market']==market),key=lambda r:(-abs(r['foreign']),r['code']))[:10] for market in ('TWSE','TPEX')}
    outcomes=optional(output/'stock-institutional-research/outcomes.json',{})
    research=summarize(outcomes.get('items',[]),min(end,outcomes.get('asOf',end)),end=end,cutoff=outcomes.get('splitDate'),observation_start=start) if outcomes else []
    return {'dates':days,'days':len(days),'expectedDays':len(expected),'missingDates':[d for d in expected if d not in days],
            'missingVolumeDates':[d for d in days if not (output/'daily-reports/warrant-volume'/(d+'.json')).exists()],
            'stockDaySamples':sum(len(v) for v in grouped.values()),'pairedStockDaySamples':paired,'leaders':leaders,'sources':sources,
            'research': [r for r in research if r['phase']=='all' and r['horizon']==5],
            'message':'法人日報事後補抓，按目前權證標的名錄回查，存在選樣偏差；未收錄不填零。回測只計本期到觀察日的結果，本期新訊號尚未到期另計。未還原價格變化不是可成交或扣費後績效。'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('data/derivatives'));a=p.parse_args()
    try:
        r=collect(a.data);print(json.dumps({k:r[k] for k in ('status','asOf','days','analysisDays','events','revisedEvents')}))
    except Exception as exc:
        path=a.data/'stock-institutional-research.json';old=optional(path,{})
        save(path,{**old,'status':'retained','error':str(exc)[:300]});raise
