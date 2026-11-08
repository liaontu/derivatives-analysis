"""Separate expiry-aware TXO research; never rewrite the original event study."""
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from statistics import mean, median
import re

if __package__:
    from . import market_history as h
else:
    import market_history as h

VERSION='option-settlement-study-v2'
COST_VERSION='long-txo-cost-scenarios-v1'
SCENARIOS=[{'id':'base','label':'示例成本','commission':20,'exerciseFee':20,'slippagePct':0.5,'fundingPct':2},
           {'id':'stress','label':'較高成本','commission':50,'exerciseFee':50,'slippagePct':1,'fundingPct':5}]


def tax(value):
    return int(Decimal(str(value)).quantize(Decimal('1'),rounding=ROUND_HALF_UP))


def cost(entry,exit,days,settlement_price=None,scenario=None):
    """One long standard TXO, multiplier 50. Exercise tax uses underlying value."""
    s=scenario or SCENARIOS[0]
    if entry<=0 or exit<0 or days<0:raise ValueError('Invalid option cost input')
    expiry=settlement_price is not None
    exercised=expiry and exit>0
    buy_tax=tax(entry*50*.001)
    sell_tax=tax(settlement_price*50*.00002) if exercised else (0 if expiry else tax(exit*50*.001))
    commission=s['commission']+(s['exerciseFee'] if exercised else (0 if expiry else s['commission']))
    slip=50*(entry+(0 if expiry else exit))*s['slippagePct']/100
    funding=entry*50*s['fundingPct']/100*days/365
    total=buy_tax+sell_tax+commission+slip+funding
    pnl=(exit-entry)*50-total
    return {'tax':buy_tax+sell_tax,'commission':commission,'slippage':round(slip,4),'funding':round(funding,4),
            'total':round(total,4),'netPnl':round(pnl,4),'netReturnPct':pnl/(entry*50)*100}


def outcome(event,settlement,cutoff):
    e=dict(event);e['v1Status']=event['status'];e['exitBasis']=None;e['costs']={}
    e['intendedExitDate']=event.get('exitDate')
    if e['date']>cutoff:return None
    expiry=settlement if settlement and settlement['date']<=cutoff else None
    # Missing entry cannot be repaired by a final settlement price.
    if not e.get('entryDate') or e['entryDate']>cutoff:
        e.update(status='pending',entry=None,exit=None,returnPct=None);return e
    if expiry and e['entryDate']>expiry['date']:
        e.update(status='expired-before-entry',entry=None,exit=None,returnPct=None);return e
    if e.get('entry') is None or e['entry']<=0:
        e.update(status='missing-entry',exit=None,returnPct=None);return e
    if expiry and (not e.get('exitDate') or expiry['date']<=e['exitDate']):
        payoff=max(0,expiry['price']-e['strike']) if e['cp']=='C' else max(0,e['strike']-expiry['price'])
        e.update(exitDate=expiry['date'],exit=payoff,status='evaluated',exitBasis='official-settlement',settlementPrice=expiry['price'])
    elif e.get('exitDate') and e['exitDate']<=cutoff and e.get('exit') is not None:
        e.update(status='evaluated',exitBasis='traded-close')
    else:
        e.update(status='pending' if not e.get('exitDate') or e['exitDate']>cutoff else 'missing-exit',exit=None,returnPct=None);return e
    e['returnPct']=(e['exit']/e['entry']-1)*100
    held=(date.fromisoformat(e['exitDate'])-date.fromisoformat(e['entryDate'])).days
    e['costs']={s['id']:cost(e['entry'],e['exit'],held,e.get('settlementPrice'),s) for s in SCENARIOS}
    return e


def summary(events):
    groups=defaultdict(list)
    for e in events:groups[(e['rule'],e['cp'],e['segment'])].append(e)
    result=[]
    for (rule,side,segment),values in sorted(groups.items()):
        valid=[e for e in values if e['status']=='evaluated'];returns=[e['returnPct'] for e in valid]
        result.append({'rule':rule,'cp':side,'segment':segment,'signals':len(values),'evaluated':len(valid),
            'statusCounts':dict(Counter(e['status'] for e in values)),
            'gapReasonCounts':dict(Counter(e['dataReason'] for e in values if e.get('dataReason') and e['status'] in ('missing-entry','missing-exit'))),
            'settled':sum(e.get('exitBasis')=='official-settlement' for e in valid),
            'v1Evaluated':sum(e['v1Status']=='evaluated' for e in values),'distinctDates':len({e['date'] for e in values}),
            'grossMedian':median(returns) if returns else None,
            'scenarios':{s['id']:{'median':median(e['costs'][s['id']]['netReturnPct'] for e in valid) if valid else None,
                'mean':mean(e['costs'][s['id']]['netReturnPct'] for e in valid) if valid else None,
                'positivePct':sum(e['costs'][s['id']]['netReturnPct']>0 for e in valid)/len(valid)*100 if valid else None} for s in SCENARIOS}})
    return result


def audit_missing(output,events):
    """A zero-volume official row is evidence of no trade, never a fill price."""
    cache={};sources={};counts=Counter()
    for e in events:
        if e['status'] not in ('missing-entry','missing-exit'):continue
        month=e['month'];day=e['entryDate'] if e['status']=='missing-entry' else e['exitDate']
        if not re.fullmatch(r'\d{6}(?:[WF]\d)?',month):raise ValueError('Unsafe option month')
        if month not in cache:
            path='option-history/TXO/'+month+'.json';doc=h.read(output/path,{})
            if doc and (doc.get('contract'),doc.get('month'))!=('TXO',month):raise ValueError('Option history identity mismatch')
            cache[month]={d:{(r[0],r[1]):dict(zip(doc['fields'],r)) for r in rows} for d,rows in doc.get('days',{}).items()}
            sources[month]={'path':path,'sha256':hashlib.sha256((output/path).read_bytes()).hexdigest() if doc else None}
        r=cache[month].get(day,{}).get((e['strike'],e['cp']))
        reason='source-row-missing' if r is None else 'no-trade' if r.get('volume')==0 else 'traded-close-missing'
        e.update(dataReason=reason,dataReasonDate=day,quoteEvidence={'month':month,'date':day,'row':r})
        counts[reason]+=1
    return {'version':'official-row-gap-audit-v1','counts':dict(counts),'sources':sources,
        'note':'逐筆比對官方一般盤同月份／履約價／C/P。無成交與漏抓分列；結算參考價與買賣報價不替代實際成交價。'}


def build(output,settlements,cutoff):
    original=h.read(output/'option-backtest/TXO.json',{})
    if original.get('version')!='option-events-v1':raise ValueError('Unknown event cohort version; preserve previous study')
    final={r['month']:r for r in settlements if r['contract']=='TXO'}
    events=[r for e in original['events'] if (r:=outcome(e,final.get(e['month']),cutoff))]
    audit=audit_missing(output,events)
    return {'schemaVersion':1,'version':VERSION,'asOf':cutoff,'contract':'TXO','splitDate':original['splitDate'],
        'sourceVersion':original['version'],'sourceHash':hashlib.sha256(h.encode(original)).hexdigest(),
        'settlementHash':hashlib.sha256(h.encode(final)).hexdigest(),'historyType':'reconstructed','liveOos':False,
        'costVersion':COST_VERSION,'scenarios':SCENARIOS,'summary':summary(events),'events':events,'gapAudit':audit,
        'rules':['沿用 v1 訊號、去重與開發／評估分割；未依結果選樣。','次一交易日收盤進場，原定五個交易日後收盤退出；先到期則用官方最後結算價計算內含價值。',
            '無進場成交價不建立部位；未到期但缺退出成交價仍缺值。到期內含價值 0 是有效結算。',
            '僅標準 TXO，每點 50 元；未將此成本模型套用到調整型個股選擇權。',
            '買賣權利金交易稅千分之一；價內到期結算稅按最後結算指數×50×十萬分之二，四捨五入至元。',
            '券商手續費、履約費、滑價、融資利率均為明示情境，非實際報價。交易所費用列作參考，不與已含內部成本的券商費重複相加。',
            '沒有可成交數量、保證成交、帳戶資金限制與跨訊號組合；不是可執行策略或獨立樣本的實盤績效。',
            '歷史重建含事後取得的結算資料，評估區段不是前瞻實盤；與 v1 方法不同，分開保存。']}
