"""Dated free-public-data coverage, stock futures matching and expiry research."""
import argparse
from collections import defaultdict
from datetime import date,datetime,timedelta,timezone
import gzip
import hashlib
import json
from pathlib import Path
import re

if __package__:
    from . import market_history as h, cross_market as cross, settlement_study as study
    from .stock_institutional import normalize
else:
    import market_history as h, cross_market as cross, settlement_study as study
    from stock_institutional import normalize

VERSION='market-completion-v1'
FIELDS=['date','close','volume','amount','returnPct','referenceAdjustedPct','foreign','trust','dealer','dealerOwn','dealerHedge','total','referenceAction']
FUTURE_FIELDS=['date','contract','month','close','volume','oi','bid','ask','returnPct','closingBasis','basisPct']
LIMITS=[
    {'item':'無成交的實際成交價','status':'不能補造','effect':'缺價格、價差與回測進出場仍留白；到期結算僅處理已有有效進場的到期部位。'},
    {'item':'同步三邊報價、深度與可成交數量','status':'免費日報不足','effect':'收盤價差不能證明可執行套利；未建立訂單簿成交模擬。'},
    {'item':'券商實際費率、借券與資金成本','status':'依帳戶而異','effect':'官方稅率與交易所費表可查；其餘只作可修改成本情境，實際值須由券商取得。'},
    {'item':'權證全部券商分點與投資人身分','status':'來源僅排行','effect':'沿用網站每日 300 檔加自選排行；不能還原全部分點、持倉、大戶本人或交易動機。'},
    {'item':'美國全商品歷史與即時鏈','status':'公開來源有限','effect':'維持原有 CFE、Cboe 與 CFTC 範圍；沒有將它們宣稱為全美市場。'},
    {'item':'完整公司行動與歷史退市名錄','status':'部分可核對','effect':'補入官方除權息參考價及期交所契約調整。公司名錄是收集時點名錄，仍有存活偏差；減資、分割等未具完整因子時不算調整報酬。'},
]


def digest(value):return hashlib.sha256(h.encode(value)).hexdigest()


def verified(output,entry,compressed=False):
    file=(output/entry['path']).resolve()
    if not file.is_relative_to(output.resolve()):raise ValueError('Unsafe source path')
    raw=file.read_bytes();raw=gzip.decompress(raw) if compressed else raw
    # Published JSON files are compact canonical data; raw source bytes hash separately.
    if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('Source hash mismatch: '+entry['path'])
    return json.loads(raw)


def merge_settlements(documents,end):
    values={}
    for key,rows in documents.items():
        if 'settlement-' not in key:continue
        for row in rows:
            if row['date']>end:continue
            identity=(row['kind'],row['contract'],row['month']);old=values.get(identity)
            if old and any(old[k]!=row[k] for k in ('date','price')):raise ValueError('Conflicting settlement '+str(identity))
            values[identity]={**(old or {}),**row}
    return sorted(values.values(),key=lambda r:(r['date'],r['kind'],r['contract']))


def returns(current,previous,action=None,unresolved=False):
    if current is None or previous is None or current<=0 or previous<=0:return None,None
    plain=(current/previous-1)*100
    if unresolved:return plain,None
    if not action:return plain,plain
    if abs(previous-action['before'])>max(.011,previous*1e-6):return plain,None
    return plain,(current/(previous*action['factor'])-1)*100


def build(output):
    source=h.read(output/'market-supplement.json',{});history=h.read(output/'market-history.json',{})
    start,end=history['start'],history['end'];cal=h.load_calendar(output)
    base=date.fromisoformat(start)-timedelta(days=1)
    while not cal.is_open(base):base-=timedelta(days=1)
    base=base.isoformat();days=h.expected_days(output,'futures',start,end)
    docs={};errors=[];evidence={}
    for key,entry in source.get('documents',{}).items():
        try:
            doc=verified(output,entry)
            if doc['id']!=key:raise ValueError('Reference identity mismatch')
            docs[key]=doc['rows'];evidence[key]=entry
        except Exception as exc:errors.append({'source':key,'error':str(exc)})
    if not docs.get('companies-TWSE') or not docs.get('companies-TPEX'):raise ValueError('Both official ordinary-stock rosters required')
    members={m:{r['code']:r for r in docs['companies-'+m]} for m in ('TWSE','TPEX')}
    universe={(m,c):r for m,items in members.items() for c,r in items.items()}
    actions={(r['market'],r['code'],r['date']):r for k,rows in docs.items() if k.startswith('exrights-') for r in rows}
    contract_actions=docs.get('contract-adjustments',[])
    issues={(r['code'],r['date']) for r in contract_actions if r['action'] not in ('除息','除權','除權息')}
    mappings={r['contract']:r for r in docs.get('stock-futures',[]) if r['type'] in ('上市普通股標的證券','上櫃普通股標的證券')}
    data={key:{**r,'fields':FIELDS,'futureFields':FUTURE_FIELDS,'rows':[],'futures':[],
               'actions':sorted([a for (m,c,d),a in actions.items() if (m,c)==key and start<=d<=end],key=lambda a:a['date'])} for key,r in universe.items()}
    coverage=[];source_days={};prior_quotes={};prior_futures={};unmapped=set()
    for day in [base]+days:
        quotes={};flows={};sources=[];day_errors=[]
        try:
            cash,proof=cross.cash_snapshot(output,day);quotes['TWSE']=cash['quotes'];sources.append(proof)
        except Exception as exc:day_errors.append({'source':'cash-TWSE','date':day,'error':str(exc)})
        q=docs.get('cash-TPEX-'+day)
        if q is not None:quotes['TPEX']={r['code']:r for r in q};sources.append(evidence['cash-TPEX-'+day])
        else:day_errors.append({'source':'cash-TPEX','date':day,'error':'Missing validated daily source'})
        if day!=base:
            snap=h.read(output/'stock-institutional'/(day+'.json'),{})
            for market in members:
                try:
                    candidates=[s for s in snap.get('sources',[]) if s.get('market')==market and s.get('date')==day]
                    if len(candidates)!=1:raise ValueError('Missing/ambiguous institutional source')
                    e=candidates[0];raw=verified(output,e,True)
                    flows[market]={r['code']:r for r in normalize(raw,market,day,set(members[market]))};sources.append(e)
                except Exception as exc:day_errors.append({'source':'institutional-'+market,'date':day,'error':str(exc)})
        futures={}
        entry=history.get('feeds',{}).get('futures',{}).get(day)
        if entry:
            doc=h.load_day(output,entry)
            if doc['date']!=day:raise ValueError('Futures date mismatch')
            sources.append(entry)
            for r in doc['rows']:
                if r['session']!='一般' or not re.fullmatch(r'\d{6}',r['month']):continue
                if r['contract'] not in mappings:
                    unmapped.add(r['contract']);continue
                futures[(r['contract'],r['month'])]=r
        elif day!=base:day_errors.append({'source':'stock-futures','date':day,'error':'Missing futures day'})
        if day!=base:
            for (market,code),payload in data.items():
                q=quotes.get(market,{}).get(code,{});old=prior_quotes.get(market,{}).get(code,{})
                action=actions.get((market,code,day));plain,adjusted=returns(q.get('close'),old.get('close'),action,(code,day) in issues and not action)
                f=flows.get(market,{}).get(code,{})
                payload['rows'].append([day,q.get('close'),q.get('volume'),q.get('amount'),plain,adjusted,
                    *[f.get(k) for k in FIELDS[6:12]],action['action'] if action else ('契約調整待核' if (code,day) in issues else None)])
            for (symbol,month),r in futures.items():
                m=mappings[symbol];market='TWSE' if m['type'].startswith('上市') else 'TPEX';key=(market,m['code'])
                if key not in data:continue
                close=r['last'] if r.get('last') is not None and r['last']>0 else None
                spot=quotes.get(market,{}).get(m['code'],{}).get('close');old=prior_futures.get((symbol,month),{})
                change,_=returns(close,old.get('last'))
                basis=close-spot if close is not None and spot is not None else None
                data[key]['futures'].append([day,symbol,month,close,r['volume'],r.get('oi'),r.get('bid'),r.get('ask'),change,basis,basis/spot*100 if basis is not None else None])
            for market in members:
                eligible=set(members[market]);q=quotes.get(market,{});f=flows.get(market,{})
                coverage.append({'date':day,'market':market,'universe':len(eligible),'quoteRows':len(eligible&q.keys()),
                    'priced':sum(q[c].get('close') is not None for c in eligible&q.keys()),'institutional':len(eligible&f.keys()),
                    'missingQuoteCodes':sorted(eligible-q.keys()),'missingInstitutionalCodes':sorted(eligible-f.keys()),
                    'futureContracts':len({symbol for symbol,month in futures if mappings[symbol]['code'] in eligible and mappings[symbol]['type'].startswith('上市' if market=='TWSE' else '上櫃')})})
        source_days[day]={'sources':sources,'errors':day_errors};errors.extend(day_errors)
        prior_quotes=quotes;prior_futures=futures
    catalog=[]
    for key,payload in sorted(data.items()):
        payload.update(version=VERSION,start=start,end=end,base=base)
        path='market-completion/stocks/'+payload['market']+'-'+payload['code']+'.json';h.save(output/path,payload)
        catalog.append({k:payload[k] for k in ('code','name','market')}|{'path':path,'sha256':digest(payload),
            'futureContracts':sorted({r[1] for r in payload['futures']})})
    fees={r['商品']:r for r in docs.get('official-fees',[])}
    if any(float(fees.get(k,{}).get('期貨交易稅比率',-1))!=v for k,v in [('TXO',.001),('TX',.00002)]):
        raise ValueError('Official tax rates changed/missing; preserve study pending cost-rule review')
    settlements=merge_settlements(docs,end);research=study.build(output,settlements,end)
    study_path='option-settlement-study/TXO.json';h.save(output/study_path,research)
    reference={'settlements':settlements,'contractAdjustments':contract_actions,'adjustedContracts':docs.get('adjusted-contracts',[]),
               'officialFees':docs.get('official-fees',[]),'stockFutures':docs.get('stock-futures',[])}
    refpath='market-completion/references.json';h.save(output/refpath,reference)
    result={'schemaVersion':1,'version':VERSION,'start':start,'end':end,'base':base,'checkedAt':source['checkedAt'],
        'status':'partial' if errors or source.get('status')!='ok' else 'ok','error':'; '.join(e['error'] for e in errors) or source.get('error'),'errors':errors,'coverage':coverage,'stocks':catalog,
        'references':{'path':refpath,'sha256':digest(reference)},'research':{'path':study_path,'sha256':digest(research),
             'version':study.VERSION,'summary':research['summary'],'splitDate':research['splitDate'],'signals':len(research['events']),'rules':research['rules']},
        'sourceDays':source_days,'referenceSources':evidence,'limitations':LIMITS,
        'scope':'目前官方上市櫃普通股名錄，按原始報表逐檔核對；歷史重建並非無存活偏差的歷史成分名單。法人單位為股，不能跨股票相加解讀為資金流。',
        'futureScope':'一般盤、原始標準商品代碼、單一月份契約；大小型各列，不合併口數。報酬比較同商品同月份的前一交易日；收盤價差非同步、未扣持有成本。調整型代碼不倒套最新條款。',
        'unmappedFutures':sorted(unmapped),'summary':{'stocks':len(catalog),'futureContracts':len(mappings),
            'futureUnderlyings':len({r['code'] for r in mappings.values()}),'settlements':len(settlements),'exRights':sum(len(p['actions']) for p in data.values())}}
    h.save(output/'market-completion.json',result)
    return result


def report_sections(output,start,end):
    """Cutoff-aware summary; future outcomes never enter earlier reports."""
    index=h.read(output/'market-completion.json',{})
    if not index:return [],None
    coverage=[r for r in index['coverage'] if start<=r['date']<=end]
    if not coverage:return [],None
    research=verified(output,index['research']);events=[]
    for e in research['events']:
        if e['date']>end:continue
        # Hide subsequently observed entries/outcomes; summarize signals in the report period.
        if e['date']<start:continue
        row=dict(e)
        if not row.get('exitDate') or row['exitDate']>end:
            row.update(status='pending',returnPct=None,costs={},v1Status='pending')
        events.append(row)
    stats=study.summary(events)
    sections=[{'title':'免費補資料：現股、法人與個股期貨涵蓋','note':index['scope']+' '+index['futureScope'],
        'headers':['日期','市場','名錄檔數','行情列','有成交價','法人列','期貨商品'],
        'rows':[[r[k] for k in ('date','market','universe','quoteRows','priced','institutional','futureContracts')] for r in coverage]},
        {'title':'到期結算研究 v2：本期訊號截至報告日','note':'沿用 v1 固定訊號，另加官方到期內含價值；成本為每口券商費 20 元、價內履約費 20 元、成交滑價 0.5%、年資金成本 2% 的假設。未到觀察終點不計報酬；非實盤。',
         'headers':['規則','C/P','區段','訊號','可評估','到期結算','成本後中位數 %'],
         'rows':[[r['rule'],r['cp'],r['segment'],r['signals'],r['evaluated'],r['settled'],round(r['scenarios']['base']['median'],2) if r['scenarios']['base']['median'] is not None else None] for r in stats]},
        {'title':'仍不能由免費公開日報補出的資料','note':'缺漏影響判讀、價差和模擬成交，並非只影響模擬交易。','headers':['項目','狀態','對分析的影響'],
         'rows':[[r['item'],r['status'],r['effect']] for r in index['limitations']]}]
    proof={'id':'market-completion','label':'免費來源補齊與到期結算研究','date':max(r['date'] for r in coverage),'baseDate':None,
           'days':len({r['date'] for r in coverage}),'expected':len(h.expected_days(output,'futures',start,end)),
           'missingDates':sorted(set(h.expected_days(output,'futures',start,end))-{r['date'] for r in coverage}),
           'status':'available' if index['status']=='ok' else 'partial','sha256':digest({'coverage':coverage,'stats':stats,'sources':{d:e for d,e in index['sourceDays'].items() if start<=d<=end}}),
           'note':'事後重建；報告只納入截止日已到期的結果，來源收集時間可能晚於行情日。','url':'https://openapi.taifex.com.tw/'}
    return sections,proof


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,default=Path('data/derivatives'));args=parser.parse_args()
    try:
        result=build(args.data);print(json.dumps({'status':result['status'],**result['summary'],'errors':result['errors']},ensure_ascii=False))
        if result['status']!='ok':raise SystemExit(1)
    except Exception as exc:
        path=args.data/'market-completion.json';old=h.read(path,{})
        failure={'checkedAt':datetime.now(timezone.utc).isoformat(),'error':str(exc)}
        h.save(args.data/'market-completion/failures'/('build-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'.json'),failure)
        if old:h.save(path,{**old,'status':'retained','error':str(exc)})
        raise


if __name__=='__main__':main()
