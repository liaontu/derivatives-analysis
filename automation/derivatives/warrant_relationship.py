"""One-month spot/warrant relationships, using dated terms and partial broker ranks."""
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import fmean
import argparse
import json
import math
import re

try:
    from .daily_reports import read, save, aligned, WARRANT_SOURCES
    from .warrant_journal import branch_evidence, valid_rows, sessions, price_change
    from .warrant_focus import numeric
    from .trading_calendar import load_calendar
    from .backfill_warrants import month_start
except ImportError:
    from daily_reports import read, save, aligned, WARRANT_SOURCES
    from warrant_journal import branch_evidence, valid_rows, sessions, price_change
    from warrant_focus import numeric
    from trading_calendar import load_calendar
    from backfill_warrants import month_start

VERSION = 'warrant-relationship-v2'
HINTS = {'call-up': '上漲日認購連續買超', 'call-down': '下跌日認購連續買超',
         'put-down': '下跌日認售連續買超', 'put-up': '上漲日認售連續買超',
         'roll': '近到期賣超與長天期買超並存', 'both': '同分點認購、認售同日買超'}
RULES = {'version': VERSION, 'branchStreak': 3, 'rollSellDays': 30, 'rollBuyDays': 60,
         'minCorrelationPairs': 10, 'topWarrants': 5, 'branchLeadersPerSide': 3,
         'basis': '分點是交易通路，非單一大戶；成交量不是主動買進量；歷史重建不是當時可得訊號。',
         'issuerBasis': '發行商由權證名稱判定；發行商總公司分點視為造市部位，其淨賣超反號當作市場淨買進的粗估，不列入策略線索；同券商其他分點另標示。'}
# Issuers as they appear inside warrant names (e.g. 台積電永豐64購16), and the broker each ranked branch belongs to.
ISSUERS = ('元大', '凱基', '群益', '永豐', '統一', '國票', '台新', '富邦', '國泰', '中信', '兆豐', '元富', '康和')
ISSUER_PATTERN = re.compile('(' + '|'.join(ISSUERS) + r')[0-9A-Z]{2}[購售牛熊][0-9]{2}$')
BROKERS = {'元大': '元大', '元大證券': '元大', '凱基': '凱基', '凱基證券': '凱基', '群益金鼎': '群益', '群益': '群益',
           '永豐金': '永豐', '永豐金證券': '永豐', '統一': '統一', '統一證券': '統一', '國票': '國票', '國票證券': '國票',
           '台新': '台新', '台新證券': '台新', '富邦': '富邦', '富邦證券': '富邦', '國泰': '國泰', '國泰證券': '國泰',
           '中國信託': '中信', '中信': '中信', '兆豐': '兆豐', '兆豐證券': '兆豐', '元富': '元富', '元富證券': '元富',
           '康和': '康和', '康和證券': '康和'}
RELATIONS = {'issuer': '發行商造市', 'sameBroker': '同券商分點', 'other': '其他分點', 'unknown': '發行商未判定'}


def issuer_of(name):
    """The issuer written into a warrant name, or None when the name does not follow the convention."""
    match = ISSUER_PATTERN.search(name or '')
    return match.group(1) if match else None


def branch_relation(branch_name, issuer):
    """issuer: the warrant issuer's own head-office branch (market making); sameBroker: one of its retail branches."""
    if not issuer:
        return 'unknown'
    parts = re.split(r'[-－]', branch_name or '')
    broker = BROKERS.get(parts[0].strip())
    if broker != issuer:
        return 'other'
    return 'issuer' if len(parts) == 1 else 'sameBroker'


def market_making(branches):
    """Per side, the issuers' own net flow; its negative is a rough estimate of the market's net buying."""
    out = {}
    for cp in ('C', 'P'):
        rows = [r for r in branches if r['cp'] == cp and r.get('relation') == 'issuer']
        net = sum(r['net'] for r in rows)
        out[cp] = {'rows': len(rows), 'warrants': len({r['code'] for r in rows}), 'issuerNet': net, 'marketNet': -net}
    return out


def optional(path, default):
    return read(path) if path.exists() else default


def positive(value):
    return numeric(value) and value > 0


def identity(row):
    return tuple(row.get(k) for k in ('code', 'name', 'market', 'underlying', 'cp'))


def standard(row):
    return bool(re.fullmatch(r'\d{6}' if row.get('cp') == 'C' else r'\d{5}[PUT]' if row.get('cp') == 'P' else r'(?!)', row.get('code', '')))


def term_fields(row, day, spot):
    if row.get('termsDate') != day:
        return {'strike': None, 'ratio': None, 'expiry': None, 'lastTrade': None, 'style': None,
                'termsDate': None, 'remainingDays': None, 'moneynessPct': None}
    values = {k: row.get(k) for k in ('strike', 'ratio', 'expiry', 'lastTrade', 'style', 'termsDate')}
    values['remainingDays'] = (date.fromisoformat(row['expiry']) - date.fromisoformat(day)).days if row.get('expiry') else None
    values['moneynessPct'] = ((spot-row['strike'])/row['strike']*100*(1 if row['cp']=='C' else -1)
                             if positive(spot) and positive(row.get('strike')) else None)
    return values


def pearson(pairs):
    values = [(x,y) for x,y in pairs if numeric(x) and numeric(y)]
    n = len(values)
    if n < RULES['minCorrelationPairs']:
        return {'n': n, 'r': None, 'status': '少於10組可比較日變動'}
    mx, my = fmean(x for x,y in values), fmean(y for x,y in values)
    xx = sum((x-mx)**2 for x,y in values)
    yy = sum((y-my)**2 for x,y in values)
    return {'n': n, 'r': sum((x-mx)*(y-my) for x,y in values)/math.sqrt(xx*yy) if xx and yy else None,
            'status': '同期描述性關聯，非因果或預測能力' if xx and yy else '序列沒有變動，無法計算'}


def classify_hints(branches, spot_pct):
    """Strategy clues from ranked branches; the issuers' own market-making branches never count as clues."""
    hints, groups = [], defaultdict(list)
    for r in branches:
        if r.get('relation') == 'issuer':
            continue
        groups[r['branchCode']].append(r)
        if r['net'] > 0 and r['streak'] >= 3 and numeric(spot_pct) and spot_pct != 0:
            kind = ('call-' if r['cp']=='C' else 'put-') + ('up' if spot_pct>0 else 'down')
            hints.append({'kind': kind, 'branchCode': r['branchCode'], 'branchName': r['branchName'],
                          'a': r['code'], 'aNet': r['net'], 'streak': r['streak'], 'streakCapped':r['streakCapped'],
                          'sameBroker': r.get('relation') == 'sameBroker'})
    for branch, rows in groups.items():
        buys = sorted((r for r in rows if r['net']>0), key=lambda r:-r['net'])
        sells = sorted((r for r in rows if r['net']<0), key=lambda r:r['net'])
        calls, puts = [r for r in buys if r['cp']=='C'], [r for r in buys if r['cp']=='P']
        if calls and puts:
            a,b = calls[0], puts[0]
            hints.append({'kind':'both','branchCode':branch,'branchName':a['branchName'],
                          'a':a['code'],'aNet':a['net'],'b':b['code'],'bNet':b['net'],
                          'sameTerms': bool(a.get('expiry') and a['expiry']==b.get('expiry') and positive(a.get('strike')) and a['strike']==b.get('strike')),
                          'sameBroker': 'sameBroker' in (a.get('relation'), b.get('relation'))})
        for cp in ('C','P'):
            short = [r for r in sells if r['cp']==cp and r.get('remainingDays') is not None and 0<=r['remainingDays']<=30]
            long = [r for r in buys if r['cp']==cp and r.get('remainingDays') is not None and r['remainingDays']>=60]
            if short and long:
                a,b = short[0],long[0]
                hints.append({'kind':'roll','branchCode':branch,'branchName':a['branchName'],'cp':cp,
                              'a':a['code'],'aNet':a['net'],'aDays':a['remainingDays'],
                              'aExpiry':a.get('expiry'),'aStrike':a.get('strike'),'aRatio':a.get('ratio'),
                              'b':b['code'],'bNet':b['net'],'bDays':b['remainingDays'],
                              'bExpiry':b.get('expiry'),'bStrike':b.get('strike'),'bRatio':b.get('ratio'),
                              'sameBroker': 'sameBroker' in (a.get('relation'), b.get('relation'))})
    return sorted(hints,key=lambda h:(h['kind'], -abs(h['aNet']), h['branchCode'],h['a']))


def summarize(day, rows, previous, previous_spot, payloads, calendar):
    rows = [r for r in rows if standard(r)]
    spots = {r.get('underlyingPrice') for r in rows if positive(r.get('underlyingPrice'))}
    spot = next(iter(spots)) if len(spots)==1 else None
    old = {identity(r):r for r in previous}
    record = {'date':day,'spot':spot,'spotPct':price_change(spot,previous_spot),
              'spotStatus':'同日標的價一致' if len(spots)==1 else '標的價缺漏或來源不一致',
              'previousDate':sessions(day,1,calendar)[0], 'count':len(rows), 'active':0,
              'branchWarrants':0,'branchRows':0, 'branchVolume':0,'branchIssues':0, 'sides':{}}
    all_branches, top = [], []
    for cp in ('C','P'):
        side_rows = [r for r in rows if r['cp']==cp]
        vols = [r['volume'] for r in side_rows if numeric(r.get('volume')) and r['volume']>=0]
        info = {'count':len(side_rows),'active':sum(v>0 for v in vols), 'volume':sum(vols),
                'missingVolume':len(side_rows)-len(vols),'knownTermVolume':0,'termsCount':0,
                'knownExpiryVolume':0, 'knownStrikeVolume':0,
                'expiry':{'0-30':0,'31-90':0,'91+':0},'moneyness':{'in':0,'at':0,'out':0},
                'style':{'歐式':0,'美式':0,'其他／缺資料':0}}
        matched = [(r,old[identity(r)]) for r in side_rows if identity(r) in old
                   and numeric(r.get('volume')) and r['volume']>=0 and numeric(old[identity(r)].get('volume')) and old[identity(r)]['volume']>=0]
        now_vol, old_vol = sum(r['volume'] for r,o in matched), sum(o['volume'] for r,o in matched)
        info.update(matchedCount=len(matched), matchedVolumePct=(now_vol/old_vol-1)*100 if old_vol>0 else None,
                    logVolumeChange=math.log(now_vol/old_vol) if now_vol>0 and old_vol>0 else None)
        for r in side_rows:
            volume = r['volume'] if numeric(r.get('volume')) and r['volume']>=0 else 0
            terms = term_fields(r,day,spot)
            if r.get('termsDate')==day:
                info['termsCount']+=1
                info['knownTermVolume']+=volume
            info['style'][terms['style'] if terms['style'] in ('歐式','美式') else '其他／缺資料']+=volume
            dte, money = terms['remainingDays'], terms['moneynessPct']
            if dte is not None and dte>=0:
                info['knownExpiryVolume']+=volume
                info['expiry']['0-30' if dte<=30 else '31-90' if dte<=90 else '91+']+=volume
            if money is not None:
                info['knownStrikeVolume']+=volume
                info['moneyness']['in' if money>0 else 'out' if money<0 else 'at']+=volume
            if volume>0:
                top.append({**{k:r.get(k) for k in ('code','name','market','cp','volume')},**terms,
                            'price':r.get('price') if positive(r.get('price')) else None})
            payload=payloads.get(r['code'])
            if not payload or not any(p.get('date')==day for p in payload.get('history',[])):
                continue
            evidence=branch_evidence(r,payload,calendar)
            if evidence is None:
                record['branchIssues']+=1
                continue
            snapshot=next(p for p in payload['history'] if p['date']==day)
            points=valid_rows(snapshot)
            signals={s['branchCode']:s for s in evidence['signals']}
            record['branchWarrants']+=1
            record['branchVolume']+=volume
            record['branchRows']+=len(points)
            issuer=issuer_of(r.get('name'))
            for branch in points.values():
                if branch['net']==0:
                    continue
                signal=signals.get(branch['branchCode'],{})
                all_branches.append({**{k:r.get(k) for k in ('code','name','cp')}, **terms,
                    **{k:branch[k] for k in ('branchCode','branchName','buy','sell','net')},
                    'streak':signal.get('streak',0),'streakCapped':signal.get('streakCapped',False),
                    'issuer':issuer,'relation':branch_relation(branch['branchName'],issuer)})
        record['sides'][cp]=info
        record['active']+=info['active']
    top.sort(key=lambda r:(-r['volume'],r['code']))
    record['topWarrants']=top[:RULES['topWarrants']]
    record['leaders']=leaders(all_branches)
    record['customerLeaders']=leaders([r for r in all_branches if r['relation']!='issuer'])
    record['marketMaking']=market_making(all_branches)
    record['relationRows']=dict(Counter(r['relation'] for r in all_branches))
    record['hints']=classify_hints(all_branches,record['spotPct'])
    return record


def leaders(branches):
    n=RULES['branchLeadersPerSide']
    return (sorted((r for r in branches if r['net']>0),key=lambda r:(-r['net'],r['code']))[:n]+
            sorted((r for r in branches if r['net']<0),key=lambda r:(r['net'],r['code']))[:n])


def load_branches(output):
    return {p.stem:read(p) for p in (output/'warrant-branches').glob('*.json')}


def store_day(output, day, rows, previous, previous_spots, members, payloads, calendar, sources, membership):
    groups, old_groups=defaultdict(list),defaultdict(list)
    for r in rows:
        if r.get('underlying') in members and r.get('date')==day:
            groups[r['underlying']].append(r)
    for r in previous:
        old_groups[r['underlying']].append(r)
    folder=output/'warrant-relationship'
    for code,values in groups.items():
        path=folder/'stocks'/(code+'.json')
        saved=optional(path,{'code':code,'name':members[code],'days':[]})
        records={d['date']:d for d in saved['days']}
        records[day]=summarize(day,values,old_groups[code],previous_spots.get(code),payloads,calendar)
        saved.update(name=members[code],days=[records[d] for d in sorted(records)])
        save(path,saved)
    ledger=optional(folder/'sources.json',{'days':{}})
    ledger['days'][day]={'sources':sources,'membership':membership}
    save(folder/'sources.json',ledger)


def finish(output, end, calendar):
    folder=output/'warrant-relationship'
    start=month_start(date.fromisoformat(end)+timedelta(days=1)).isoformat()
    summaries=[]
    for path in sorted((folder/'stocks').glob('*.json')):
        stock=read(path)
        days=[d for d in stock['days'] if start<=d['date']<=end]
        stock['days']=days
        save(path,stock)
        if not days:
            continue
        known=[d for d in days if positive(d['spot'])]
        volume=sum(d['sides'][cp]['volume'] for d in days for cp in ('C','P'))
        terms=sum(d['sides'][cp]['knownTermVolume'] for d in days for cp in ('C','P'))
        hints=Counter(h['kind'] for d in days for h in d['hints'])
        making={cp:{'issuerNet':sum(d.get('marketMaking',{}).get(cp,{}).get('issuerNet',0) for d in days),
                    'days':sum(bool(d.get('marketMaking',{}).get(cp,{}).get('rows')) for d in days)} for cp in ('C','P')}
        summaries.append({'code':stock['code'],'name':stock['name'],'days':len(days),
                          'firstDate':known[0]['date'] if known else None,'lastDate':known[-1]['date'] if known else None,
                          'spotPct':price_change(known[-1]['spot'],known[0]['spot']) if len(known)>1 else None,
                          'volume':volume,'termVolumePct':terms/volume*100 if volume>0 else None,
                          'branchDays':sum(d['branchWarrants']>0 for d in days),'hints':dict(hints),'marketMaking':making,
                          'callCorrelation':pearson([(d['spotPct'],d['sides']['C']['logVolumeChange']) for d in days]),
                          'putCorrelation':pearson([(d['spotPct'],d['sides']['P']['logVolumeChange']) for d in days])})
    sources=optional(folder/'sources.json',{'days':{}})
    sources['days']={d:v for d,v in sources['days'].items() if start<=d<=end}
    save(folder/'sources.json',sources)
    dates=[];cursor=date.fromisoformat(start)
    while cursor.isoformat()<=end:
        dates.append({'date':cursor.isoformat(),'status':'available' if cursor.isoformat() in sources['days'] else 'missing' if calendar.is_open(cursor) else 'closed'})
        cursor+=timedelta(days=1)
    index={'schemaVersion':1,'status':'ok','start':start,'end':end,'dates':dates,'rules':RULES,
           'items':sorted(summaries,key=lambda s:(-s['volume'],s['code'])),
           'limits':['現股為官方報表標的收盤，未做除權息還原，不代表總報酬。',
                     '條款僅採同日保存／補抓資料，缺歷史條款不套用目前履約價或到期日。',
                     '成交量是買賣雙方成交的張數，不是淨買進或主動買進量；不同權證張數不等於相同曝險。',
                     '分點只有保留的買超／賣超排行，名單每日不同；跨日觀測總量不可當成大戶庫存。',
                     '相關係數採股價日變動與相同權證樣本成交量的對數日變動，最多一個月，沒有因果或策略有效性推定。',
                     '同分點的不同權證交易可能來自不同客戶，換倉／雙邊買超僅為需要後續確認的線索。',
                     '發行商由權證名稱判定；發行商總公司分點的買賣超視為造市與避險部位，不列入策略線索，其淨賣超反號只是市場淨買進的粗估（排行不完整、造市也可能透過其他帳戶）。',
                     '同券商的其他分點可能是一般客戶，也可能與造市部位有關，線索中另標示供保留判讀。']}
    save(folder/'index.json',index)
    return index


def daily(output):
    manifest=read(output/'manifest.json')
    day,sources=aligned(manifest['sources'],WARRANT_SOURCES)
    directory=read(output/'stock-warrant-directory.json')
    if directory.get('quoteDates')!=[day] or directory.get('status')!='ok':
        raise ValueError('等待同日個股權證名錄')
    calendar=load_calendar(output)
    prior=sessions(day,1,calendar)[0]
    members={r['code']:r['name'] for r in directory['items']}
    rows=[r for r in read(output/'warrants.json')['rows'] if r.get('underlying') in members and r.get('date')==day]
    histories={code:optional(output/'warrant-history'/(code+'.json'),{}) for code in members}
    previous=[];previous_spots={}
    for code,h in histories.items():
        spots=dict(h.get('underlyingPrices',[]));previous_spots[code]=spots.get(prior)
        for symbol,q in h.get('quotes',{}).items():
            point=next((r for r in q.get('rows',[]) if r[0]==prior),None)
            if point:
                previous.append({'code':symbol,'name':q['name'],'market':q['market'],'cp':q['cp'],'underlying':code,'volume':point[2]})
    for r in rows:
        if not positive(r.get('underlyingPrice')):
            r['underlyingPrice']=dict(histories[r['underlying']].get('underlyingPrices',[])).get(day)
    membership={'dates':{k:v for k,v in directory['sourceDates'].items() if k.endswith('Companies')},'basis':'當日保存公司名錄'}
    store_day(output,day,rows,previous,previous_spots,members,load_branches(output),calendar,sources,membership)
    return finish(output,day,calendar)


def backfill(output,cache,terms_cache,start,end):
    try:
        from .backfill_volume_reports import Sources,read_cache
        from .collect import number
    except ImportError:
        from backfill_volume_reports import Sources,read_cache
        from collect import number
    calendar=load_calendar(output)
    membership=read_cache(cache/'companies.json.gz')
    members={k:v['name'] for k,v in membership['names'].items()}
    source=Sources(cache,cache)
    payloads=load_branches(output)
    previous=[];previous_spots={}
    cursor=date.fromisoformat(sessions(start,1,calendar)[0])
    while cursor.isoformat()<=end:
        day=cursor.isoformat()
        if not calendar.is_open(cursor):
            cursor+=timedelta(days=1);continue
        required=[cache/(n+'-'+day+'.json.gz') for n in ('twse-warrants','twse-volume','twse-volume-put','tpex-quotes')]
        if not all(p.exists() for p in required):
            raise ValueError('缺少已核實快取：'+day)
        rows,audit=source.listed(cursor)
        otc,otc_audit=source.otc(cursor)
        term_path=terms_cache/('tpex-terms-'+day+'.json.gz')
        terms=read_cache(term_path) if term_path.exists() else None
        indexed={r['code']:r for r in terms['rows']} if terms else {}
        if terms and (terms['audit']['date']!=day or any(r.get('date')!=day for r in terms['rows'])):
            raise ValueError('歷史上櫃條款日期不一致')
        raw=read_cache(cache/('tpex-quotes-'+day+'.json.gz'))['raw']
        values={r[0]:number(r[9]) for t in raw['tables'] if '成交金額' in t.get('fields',[]) for r in t['data']}
        for r in otc:
            t=indexed.get(r['code'],{})
            if all(t.get(k)==r.get(k) for k in ('name','underlying','cp')):
                r.update({k:t.get(k) for k in ('strike','ratio','style','expiry','lastTrade')})
                r['termsDate']=day
            r['value']=values.get(r['code'])
        rows+=otc;audit+=otc_audit
        if terms:audit.append(terms['audit'])
        rows=[r for r in rows if r.get('underlying') in members]
        if day>=start:
            store_day(output,day,rows,previous,previous_spots,members,payloads,calendar,audit,
                      {'dates':membership['dates'],'basis':'較晚公司名錄回查；先前已退出名錄標的未驗證'})
            print('archived',day,flush=True)
        previous=rows
        spots=defaultdict(set)
        for r in rows:
            if positive(r.get('underlyingPrice')):spots[r['underlying']].add(r['underlyingPrice'])
        previous_spots={k:next(iter(v)) if len(v)==1 else None for k,v in spots.items()}
        cursor+=timedelta(days=1)
    return finish(output,end,calendar)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--data',type=Path,default=Path('data/derivatives'))
    p.add_argument('--backfill-cache',type=Path)
    p.add_argument('--terms-cache',type=Path)
    p.add_argument('--start');p.add_argument('--end')
    args=p.parse_args()
    try:
        result=backfill(args.data,args.backfill_cache,args.terms_cache,args.start,args.end) if args.backfill_cache else daily(args.data)
        print(json.dumps({'start':result['start'],'end':result['end'],'stocks':len(result['items']),'days':Counter(d['status'] for d in result['dates'])},ensure_ascii=False))
    except Exception as exc:
        path=args.data/'warrant-relationship/index.json'
        old=optional(path,{'items':[],'dates':[]})
        save(path,{**old,'status':'retained','error':str(exc)[:180]})
        raise


if __name__=='__main__':
    main()
