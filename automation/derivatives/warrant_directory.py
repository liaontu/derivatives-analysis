"""Daily free directory. Actual broker rankings remain a separate, partial dataset."""
from __future__ import annotations
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
import re
try:
    from . import collect as c
    from .governance import digest
except ImportError:
    import collect as c
    from governance import digest

MASTER = {
    'listedCompanies': 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
    'otcCompanies': 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O',
    'listedIssues': 'https://openapi.twse.com.tw/v1/opendata/t187ap37_L',
    'otcIssues': 'https://mopsfin.twse.com.tw/opendata/t187ap37_O.csv',
}
FIELDS = ('listedDate','exerciseStart','lastTrade','expiry','strike','ratio','style',
          'reportedIssueK','initialIssueK','addedK','cancelledK','originalStrike','kind','quoteMethod',
          'settlementCode','cap','floor','reset')
CODE = re.compile(r'\d{5}[0-9A-Z]')
def dated(rows, field, today):
    if not isinstance(rows, list) or not rows:
        raise ValueError('empty official directory')
    dates = {c.iso_date(r.get(field)) for r in rows}
    if len(dates) != 1 or None in dates: raise ValueError('ambiguous directory dates')
    day = dates.pop()
    if not 0 <= (today - date.fromisoformat(day)).days <= 14: raise ValueError('stale directory')
    return day

def issue_index(rows, required):
    by_identity = defaultdict(list)
    for row in rows:
        if not required <= row.keys():
            raise ValueError('official issue columns changed')
        identity = (str(row['權證代號']).strip(), str(row['權證簡稱']).strip(), c.iso_date(row['履約截止日']))
        by_identity[identity].append(row)
    return by_identity


def build(catalog, sources, raw, today):
    memberships = set()
    versions = {}
    for key, dc, cc in [('listedCompanies','出表日期','公司代號'),('otcCompanies','Date','SecuritiesCompanyCode')]:
        versions[key] = dated(raw[key], dc, today)
        codes = {str(r.get(cc) or '').strip() for r in raw[key]}
        if any(not re.fullmatch(r'[0-9A-Za-z]{2,12}',v) for v in codes): raise ValueError('invalid company code')
        memberships.update(codes)
    versions['listedIssues'] = dated(raw['listedIssues'], '出表日期', today)
    versions['otcIssues'] = dated(raw['otcIssues'], '出表日期', today)
    identity_fields = {'權證代號','權證簡稱','履約截止日'}
    extra = issue_index(raw['listedIssues'], identity_fields | {'發行單位數量(仟單位)','原始履約價格(元)/履約指數'})
    otc_dates = issue_index(raw['otcIssues'], identity_fields | {'履約開始日','最後交易日','權證類型','類別','結算方式(詳附註編號說明)'})
    by_market={}
    for market, key in [('TWSE','twse-warrants'),('TPEX','tpex-terms')]:
        payload=sources[key]
        if payload.get('status')!='ok': raise ValueError('source not refreshed: '+key)
        versions[key] = dated(payload['rows'],'date',today)
        by_market[market] = {r['code']:r for r in payload['rows']}
    groups=defaultdict(list); seen=set(); skipped=Counter()
    for q in catalog:
        if q.get('underlying') not in memberships: skipped['outsideCompanyLists']+=1; continue
        if q.get('cp') not in ('C','P') or not CODE.fullmatch(q.get('code') or ''): skipped['otherType']+=1; continue
        code=q['code']
        if code in seen: raise ValueError('duplicate quote code')
        seen.add(code)
        source=by_market[q['market']].get(code,{})
        identity=('name','expiry','underlying')
        if not all(q.get(k) and q[k]==source.get(k) for k in identity): source={}
        row={k:q.get(k) for k in ('code','name','market','underlying','underlyingName','cp','date','volume','price')}
        for k in FIELDS: row[k]=source.get(k)
        row['termsDate']=source.get('date');row['issueDateOfReport']=None
        if q['market']=='TWSE':
            candidates=extra.get((code,q['name'].strip(),q.get('expiry')),[])
            if source and len(candidates)==1:
                e=candidates[0]
                row.update(reportedIssueK=c.number(e.get('發行單位數量(仟單位)')),originalStrike=c.number(e.get('原始履約價格(元)/履約指數')),
                           kind=e.get('類別'),quoteMethod=e.get('流動量提供者報價方式'),settlementCode=e.get('結算方式(詳附註編號說明)'),
                           issueDateOfReport=versions['listedIssues'])
        elif source:
            if row['initialIssueK'] is not None:
                row['issueDateOfReport'] = source['date']
            matches = otc_dates.get((code, q['name'].strip(), q.get('expiry')), [])
            if len(matches) == 1 and versions['otcIssues'] == source['date'] == q['date'] and c.cp(matches[0]['權證類型']) == q['cp']:
                issue = matches[0]
                start, last = (c.iso_date(issue.get(key)) for key in ('履約開始日','最後交易日'))
                row.update(exerciseStart=start if start and source.get('listedDate','') and source['listedDate'] <= start <= q['expiry'] else None,
                           lastTrade=last if last and source.get('listedDate','') and source['listedDate'] <= last <= q['expiry'] else None,
                           kind=issue.get('類別'), settlementCode=issue.get('結算方式(詳附註編號說明)'),
                           scheduleReportDate=versions['otcIssues'])
        groups[q['underlying']].append(row)
    if not groups: raise ValueError('no verified individual stock warrants')
    items=[]; coverage=Counter();markets=Counter();trade=Counter()
    for code,rows in sorted(groups.items()):
        rows.sort(key=lambda r:r['code'])
        for r in rows:
            markets[r['market']]+=1
            trade['unknown' if r['volume'] is None else 'zero' if r['volume']==0 else 'positive']+=1
            coverage.update(k for k in FIELDS if r[k] is not None and r[k]!='')
        items.append({'code':code,'name':rows[0]['underlyingName'],'count':len(rows),'hash':digest(rows)})
    return {'schemaVersion':1,'status':'ok','sourceDates':versions,'sourceUrls':MASTER,
            'count':len(seen),'items':items,'excluded':dict(skipped),'marketCounts':dict(markets),
            'quoteDates':sorted({r['date'] for rows in groups.values() for r in rows}),'fieldCounts':dict(coverage),
            'volumeCounts':dict(trade),'issueMatches':sum(bool(r['issueDateOfReport']) for rows in groups.values() for r in rows)}, groups

def fetch_directory(url):
    # The official JSON has the same dated fields as the large MOPS CSV.
    # Its verbose keys made the verified 2026-11-12 response about 45 MB;
    # keep an explicit bound for this endpoint without relaxing other feeds.
    if url == MASTER['listedIssues']:
        return c.request_bytes(url, c.decode, max_bytes=80_000_000)
    return c.fetch(url)


def publish(out,today=None,fetcher=fetch_directory):
    today=today or datetime.now(timezone(timedelta(hours=8))).date()
    index=out/'stock-warrant-directory.json';old=c.read(index) or {}
    try:
        cat=(c.read(out/'warrants.json') or {})['rows']
        manifest=c.read(out/'manifest.json') or {}
        feeds={s['id']:s for s in manifest.get('sources',[])}
        ids=('twse-warrants','tpex-terms','tpex-warrants','twse-volume','twse-volume-put')
        if any(feeds.get(k,{}).get('status')!='ok' for k in ids):raise ValueError('official market sources not all available')
        basis=digest(['official-extra-volume-v3',MASTER,[(k,feeds[k]['date'],feeds[k]['provenance']['sha256']) for k in ids],
                      feeds.get('twse-volume-extra',{}).get('provenance',{}).get('sha256')])
        if old.get('status')=='ok' and old.get('checkedDay')==today.isoformat() and old.get('basis')==basis:return True
        sources={k:{**(c.read(out/(k+'.json')) or {}),'status':feeds[k]['status']} for k in ('twse-warrants','tpex-terms')}
        with ThreadPoolExecutor(max_workers=2) as pool:
            raw=dict(zip(MASTER,pool.map(fetcher,MASTER.values())))
        ix,groups=build(cat,sources,raw,today)
        if any(d<old.get('sourceDates',{}).get(k,'') for k,d in ix['sourceDates'].items()):raise ValueError('official source date regressed')
        for g in ix['items']:
            c.save(out/'stock-warrant-directory'/(g['code']+'.json'),{'rows':groups[g['code']],'hash':g['hash']})
        c.save(index,{**ix,'basis':basis,'checkedDay':today.isoformat()})
        print('individual stock warrants:',ix['count'],'matched issue reports:',ix['issueMatches'])
        return True
    except Exception as exc:
        c.save(index,{**old,'status':'retained' if old.get('items') else 'unavailable','error':str(exc)[:180],'lastFailedDay':today.isoformat()})
        print('directory check failed:',type(exc).__name__,str(exc)[:180])
        return False

def record_coverage(out,today=None):
    """Audit actual same-day coverage after either independent publication job."""
    path=out/'stock-warrant-coverage.json'
    old=c.read(path) or {}; ix=c.read(out/'stock-warrant-directory.json') or {}
    if not ix.get('items'):return False
    try:
        br=c.read(out/'warrant-branches.json') or {}
        if ix.get('status')!='ok' or not br.get('items') or br.get('status') in ('retained','unavailable'):
            raise ValueError('來源或名錄沿用舊值')
        tracked={i['code']:i for i in br['items']}
        counts=defaultdict(Counter)
        for g in ix['items']:
            shard=c.read(out/'stock-warrant-directory'/(g['code']+'.json'))
            if not shard or shard['hash']!=g['hash'] or digest(shard['rows'])!=g['hash']:
                raise ValueError('分組資料版本不符')
            for r in shard['rows']:
                item=tracked.get(r['code']);stat=counts[(r['date'],r['market'])]
                if item and any(item.get(k)!=r.get(k) for k in ('market','underlying','name')):item=None
                if item and item.get('status')=='ok' and item.get('date')==r['date']:key='ranked'
                elif item:key='otherDateOrFailed'
                elif r['volume'] is None:key='unknownVolume'
                elif r['volume']==0:key='zeroWithoutRank'
                else:key='tradedWithoutRank'
                stat[key]+=1;stat['total']+=1
        current=[{'date':d,'market':m,**v} for (d,m),v in sorted(counts.items())]
        history={(v['date'],v['market']):v for v in old.get('history',[]) if v['date'] not in {None,''}}
        history.update({(v['date'],v['market']):v for v in current});days=sorted({d for d,m in history})[-30:]
        c.save(path,{'schemaVersion':1,'status':'ok','rankIndexAt':br.get('updatedAt'),'directoryDay':ix['checkedDay'],
                     'current':current,'history':[history[k] for k in sorted(history) if k[0] in days]})
        return True
    except Exception as exc:
        c.save(path,{**old,'status':'retained' if old.get('current') else 'unavailable','error':str(exc)[:160]})
        return False

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--coverage-only',action='store_true')
    args=parser.parse_args();out=Path('data/derivatives')
    ok=args.coverage_only or publish(out)
    coverage=record_coverage(out)
    raise SystemExit(0 if ok and coverage else 1)

