"""Cash / TX / TXO interpretation from existing verified public snapshots only."""
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from statistics import correlation

if __package__:
    from . import derivative_period as p
    from . import market_history as h
    from .institutional_history import verified_snapshot
    from .stock_institutional import normalize as normalize_institutional
else:
    import derivative_period as p
    import market_history as h
    from institutional_history import verified_snapshot
    from stock_institutional import normalize as normalize_institutional

VERSION='cash-futures-options-v1'


def number(value, *, positive=False, integer=False):
    text=str(value).replace(',','').strip()
    if not re.fullmatch(r'\d+' if integer else r'-?\d+(?:\.\d+)?',text):
        raise ValueError('Missing or invalid cash-market number')
    result=int(text) if integer else float(text)
    if not p.finite(result) or (positive and result<=0):raise ValueError('Invalid cash-market value')
    return result


def one(values,label):
    if len(values)!=1:raise ValueError('Missing or ambiguous '+label)
    return values[0]


def parse_cash(raw,day):
    if raw.get('stat')!='OK' or raw.get('date')!=day.replace('-',''):
        raise ValueError('Cash source date/status mismatch')
    tables=raw.get('tables',[])
    def table(required):
        selected=one([t for t in tables if required<=set(t.get('fields') or [])],'cash table')
        fields=selected['fields']
        if len(fields)!=len(set(fields)):raise ValueError('Duplicate cash column')
        if any(len(r)!=len(fields) for r in selected['data']):raise ValueError('Cash row width mismatch')
        return [dict(zip(fields,r)) for r in selected['data']]
    # Multiple price-index tables share fields. Select the exact TAIEX row by name.
    candidates=[]
    for t in tables:
        fields=t.get('fields') or []
        if '指數' not in fields or '收盤指數' not in fields:continue
        if len(fields)!=len(set(fields)):raise ValueError('Duplicate index column')
        for r in t.get('data',[]):
            if len(r)!=len(fields):raise ValueError('Index row width mismatch')
            v=dict(zip(fields,r))
            if v['指數']=='發行量加權股價指數':candidates.append(v)
    index=one(candidates,'TAIEX index')
    turnover=one([r for r in table({'成交統計','成交金額(元)','成交股數(股)'}) if r['成交統計']=='1.一般股票'],'ordinary stock turnover')
    breadth={r['類型']:r['股票'] for r in table({'類型','整體市場','股票'})}
    def count(label):
        text=str(breadth.get(label,'')).replace(',','').strip()
        if not re.fullmatch(r'\d+(?:\(\d+\))?',text):raise ValueError('Invalid stock breadth count')
        return int(text.split('(')[0])
    quotes={}
    for r in table({'證券代號','證券名稱','成交股數','成交金額','收盤價'}):
        code=r['證券代號'].strip()
        if code in quotes:raise ValueError('Duplicate cash security')
        volume=number(r['成交股數'],integer=True)
        close=None if r['收盤價'].strip() in ('--','---','') or volume==0 else number(r['收盤價'],positive=True)
        quotes[code]={'name':r['證券名稱'].strip(),'close':close,'volume':volume,'amount':number(r['成交金額'],integer=True)}
    return {'date':day,'close':number(index['收盤指數'],positive=True),
            'amount':number(turnover['成交金額(元)'],integer=True),'volume':number(turnover['成交股數(股)'],integer=True),
            'up':count('上漲(漲停)'),'down':count('下跌(跌停)'),'flat':count('持平'),'quotes':quotes}


def cash_snapshot(output,day):
    watch=h.read(output/'stock-option-watch'/(day+'.json'),{})
    if watch.get('date')!=day:raise ValueError('Missing dated stock-option snapshot')
    evidence=watch.get('spotEvidence') or {}
    # The daily stock-options step may refresh its projection; use the already
    # validated full-source cache retained by the history step in that case.
    if not evidence:
        url='https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?type=ALLBUT0999&response=json&date='+day.replace('-','')
        key=hashlib.sha256(h.encode([url,None])).hexdigest()
        evidence=h.read(output/'market-history/requests'/(key+'.json'),{})
        if not evidence.get('validated'):raise ValueError('No validated cash-market source cache')
    file=(output/evidence.get('path','')).resolve()
    if not file.is_relative_to((output/'market-history/sources').resolve()):raise ValueError('Unsafe cash source path')
    raw=gzip.decompress(file.read_bytes())
    if hashlib.sha256(raw).hexdigest()!=evidence.get('sha256'):raise ValueError('Cash source hash mismatch')
    return parse_cash(json.loads(raw),day),{k:evidence.get(k) for k in ['url','path','sha256','fetchedAt']}


def price_relation(cash,future):
    if not p.finite(cash) or not p.finite(future):return '資料不足'
    if cash>0 and future>0:return '現期同漲'
    if cash<0 and future<0:return '現期同跌'
    if cash==0 or future==0:return '至少一邊持平'
    return '現期漲跌分歧'


def target_flows(output,snapshot,day,members):
    source=one([s for s in snapshot['sources'] if s['market']=='TWSE'],'TWSE institutional source')
    raw=json.loads(gzip.decompress((output/source['path']).read_bytes()))
    return {r['code']:r for r in normalize_institutional(raw,'TWSE',day,members)}


def build(output,analysis):
    start,end,base=analysis['start'],analysis['end'],analysis['base']
    dates=[r['date'] for r in analysis['daily']];cash={};evidence={};errors=[]
    for day in [base]+dates:
        try:cash[day],evidence[day]=cash_snapshot(output,day)
        except Exception as exc:errors.append({'date':day,'error':str(exc)[:250]})
    tx=next((r for r in analysis['futures'] if r['contract']=='TX'),{})
    previous=base;daily=[];pairs=[];base_cash=cash.get(base,{}).get('close')
    for item in analysis['daily']:
        day=item['date'];q=cash.get(day,{})
        cash_pct=p.pct(q.get('close'),cash.get(previous,{}).get('close'))
        prior_tx=tx.get('baseline') if previous==base else next((r['txClose'] for r in analysis['daily'] if r['date']==previous),None)
        future_pct=p.pct(item['txClose'],prior_tx)
        if p.finite(cash_pct) and p.finite(future_pct):pairs.append((cash_pct,future_pct))
        daily.append({'date':day,'spot':q.get('close'),'spotPct':cash_pct,'future':item['txClose'],'futurePct':future_pct,
            'closingGap':p.change(item['txClose'],q.get('close')),'relation':price_relation(cash_pct,future_pct),
            'amount':q.get('amount'),'up':q.get('up'),'down':q.get('down'),'flat':q.get('flat'),
            'upShare':p.ratio(q.get('up'),q['up']+q['down']) if q else None,
            'foreignFuturesNetOi':item['foreignNetOi'],'volumePC':item['volumeRatio'],'oiPC':item['oiRatio']})
        previous=day
    last=daily[-1] if daily else {};valid=len(cash.keys()&set(dates));relation=Counter(r['relation'] for r in daily)
    corr=None
    if len(pairs)>2:
        try:corr=correlation([r[0] for r in pairs],[r[1] for r in pairs])
        except (ValueError,ArithmeticError):pass
    # Institutional data covers the existing warrant-underlying roster, not all
    # market money flows. Keep each security's share count and coverage separate.
    latest=h.read(output/'stock-option-watch'/(end+'.json'),{})
    if latest.get('date')!=end:latest={}
    members={s['code'] for s in latest.get('stocks',{}).values()}
    inst={};individual_inst={};inst_evidence={};inst_errors=[]
    for day in dates:
        snapshot=h.read(output/'stock-institutional'/(day+'.json'),{})
        if snapshot.get('date')!=day or not verified_snapshot(output,snapshot):
            inst_errors.append({'date':day,'error':'個股法人快照缺少或來源校驗未通過'});continue
        if any(r.get('date')!=day for r in snapshot.get('rows',[])):raise ValueError('Institutional row date mismatch')
        keys=[(r['market'],r['code']) for r in snapshot['rows']]
        if len(keys)!=len(set(keys)):raise ValueError('Duplicate institutional stock')
        inst[day]={k:r for k,r in zip(keys,snapshot['rows'])}
        inst_evidence[day]={'sha256':hashlib.sha256(h.encode(snapshot)).hexdigest(),'sources':snapshot['sources']}
        if members:
            try:individual_inst[day]=target_flows(output,snapshot,day,members)
            except Exception as exc:inst_errors.append({'date':day,'error':'個股選擇權標的法人：'+str(exc)[:180]})
    history=h.read(output/'market-history.json',{})
    option_entry=history.get('feeds',{}).get('options',{}).get(end)
    option_doc=h.load_day(output,option_entry) if option_entry else {}
    if option_doc and (option_doc.get('date')!=end or option_doc.get('feed')!='options' or any(r.get('date')!=end for r in option_doc['rows'])):
        raise ValueError('Cross-market option archive identity mismatch')
    option_rows=option_doc.get('rows',[])
    stocks=[]
    for symbol,stock in latest.get('stocks',{}).items():
        code=stock['code'];q=cash.get(end,{}).get('quotes',{}).get(code,{})
        baseq=cash.get(base,{}).get('quotes',{}).get(code,{})
        selected=[r for r in option_rows if r['contract']==symbol and r.get('session')=='一般']
        flows=[individual_inst.get(d,{}).get(code,{}) for d in dates]
        days=sum(bool(r) for r in flows)
        def flow(field):return sum(r[field] for r in flows) if all(p.finite(r.get(field)) for r in flows) else None
        stocks.append({'code':code,'name':stock['name'],'option':symbol,'market':'TWSE' if q else 'unverified',
            'spot':q.get('close'),'base':baseq.get('close'),'returnPct':p.pct(q.get('close'),baseq.get('close')),
            'amount':q.get('amount'),'foreignShares':flow('foreign'),'trustShares':flow('trust'),'flowDays':days,
            'callVolume':p.total([r for r in selected if r['cp']=='C'],'volume'),
            'putVolume':p.total([r for r in selected if r['cp']=='P'],'volume'),
            'series':len(selected),'tradedSeries':sum((r.get('volume') or 0)>0 for r in selected)})
    stocks.sort(key=lambda r:(-(r.get('amount') or 0),r['code']))
    sample=[]
    for market,label in [('TWSE','上市'),('TPEX','上櫃')]:
        selected=[r for (m,_),r in inst.get(end,{}).items() if m==market]
        for field,name in [('foreign','外資'),('trust','投信')]:
            known=[r for r in selected if p.finite(r.get(field))]
            sample.append({'market':market,'label':label,'investor':name,'stocks':len(known),
                'buy':sum(r[field]>0 for r in known),'sell':sum(r[field]<0 for r in known),'flat':sum(r[field]==0 for r in known)})
    sections=[{'title':'現股、期貨與選擇權：逐日交叉判讀',
        'note':'現股採證交所加權指數與「股票」漲跌家數，不使用含權證的整體市場家數。大台固定 '+str(tx.get('month','缺到期別'))+' 契約一般時段成交收盤；現股與期貨收盤時點不同，收盤價差不是同步基差、合理價或可成交套利。P/C 只是結構資訊，不直接賦予多空分數。',
        'headers':['日期','加權指數／日漲跌','大台／日漲跌','收盤價差（非同步）','現期判讀','一般股票成交（億元）','上漲／下跌／持平家數','成交／OI P/C%'],
        'rows':[[r['date'],p.n(r['spot'],2)+' / '+p.signed(r['spotPct'],2)+'%',p.n(r['future'])+' / '+p.signed(r['futurePct'],2)+'%',
            p.signed(r['closingGap'],2),r['relation'],p.n(r['amount']/1e8 if p.finite(r['amount']) else None,2),
            ' / '.join(p.n(r[k]) for k in ['up','down','flat']),p.n(r['volumePC'],2)+' / '+p.n(r['oiPC'],2)] for r in daily]},
        {'title':'個股法人買賣方向：樣本廣度',
         'note':end+'；只計已有權證標的普通股樣本，上市／上櫃分開。不是全市場買賣超金額，也不能與期貨口數合併。外資不含外資自營商；「無買賣超」不是沒有成交。',
         'headers':['市場／法人','有效股票數','買超家數','賣超家數','無買賣超家數'],
         'rows':[[r['label']+' / '+r['investor']]+[str(r[k]) for k in ['stocks','buy','sell','flat']] for r in sample]},
        {'title':'有個股選擇權的現股：價格、法人與交易活躍度',
         'note':'依期末保存的個股選擇權名錄，可能漏掉歷史已退出標的。股價為未還原收盤；法人另從已驗證的證交所完整原表取出這些標的，按股累計，完整涵蓋才計算。選擇權量為期末一般時段各到期別／履約價合計，不表示買方方向。個股期貨尚未核對標的對照，沒有把大盤期貨當作個股期貨；上櫃或無同日上市來源者保留缺值。',
         'headers':['股票／選擇權','現股基準→期末／漲跌','期末現股成交（億元）','外資／投信累計買賣超（股）','法人涵蓋日','期末買權／賣權成交（口）','有成交／收錄序列'],
         'rows':[[r['code']+' '+r['name']+' / '+r['option'],p.n(r['base'],2)+' → '+p.n(r['spot'],2)+' / '+p.signed(r['returnPct'],2)+'%',
             p.n(r['amount']/1e8 if p.finite(r['amount']) else None,2),p.signed(r['foreignShares'])+' / '+p.signed(r['trustShares']),str(r['flowDays'])+'/'+str(len(dates)),
             p.n(r['callVolume'])+' / '+p.n(r['putVolume']),str(r['tradedSeries'])+'/'+str(r['series'])] for r in stocks]}]
    conclusions=[{'topic':'現股與期貨是否同向',
        'fact':'加權指數 '+p.n(base_cash,2)+' → '+p.n(last.get('spot'),2)+'（'+p.signed(p.pct(last.get('spot'),base_cash),2)+'%）；大台固定契約 '+p.signed(tx.get('returnPct'),2)+'%。同日漲跌同向 '+str(relation['現期同漲']+relation['現期同跌'])+'／'+str(len(pairs))+' 日；同期日報酬相關 '+p.n(corr,3)+'。',
        'reading':'相同標的的現期同向可交叉確認價格路徑，但日收盤時點不同，相關不是領先預測或因果，也不代表存在套利。'},
        {'topic':'價格變動是否擴及現股市場',
         'fact':end+' 股票上漲 '+p.n(last.get('up'))+' 家、下跌 '+p.n(last.get('down'))+' 家、持平 '+p.n(last.get('flat'))+' 家；上漲占有漲跌股票 '+p.n(last.get('upShare'),2)+'%。一般股票成交 '+p.n(last['amount']/1e8 if p.finite(last.get('amount')) else None,2)+' 億元。',
         'reading':'漲跌家數補充市值加權指數，幫助判斷是否為少數權值股帶動。成交增加不能單獨分辨追買、停損或換手。'},
        {'topic':'三市場能判讀到哪一步',
         'fact':'現股提供價格與市場廣度，期貨提供價格與法人部位，選擇權提供買賣權成交、未平倉和到期結構；各自口徑與日期獨立保留。',
         'reading':'可觀察同向、分歧與待查線索；目前不能把現股買超者、期貨空方與選擇權買方配成同一人，因此不宣稱已辨識保護性賣權、備兌買權或套利組合。'}]
    foreign_sample=next(r for r in sample if r['market']=='TWSE' and r['investor']=='外資')
    if foreign_sample['stocks']:
        conclusions.append({'topic':'現股法人家數與期貨部位的對照',
            'fact':end+' 上市權證標的樣本中，外資買超 '+str(foreign_sample['buy'])+' 家、賣超 '+str(foreign_sample['sell'])+' 家；同日期外資大台淨部位 '+p.signed(last.get('foreignFuturesNetOi'))+' 口。',
            'reading':'買超家數較多不等於買超金額較大，也不是全市場資金流。股票權重與帳戶無法配對，不能由這些數字確認「買股、賣期避險」策略。'})
    if stocks:
        active=sum((r['callVolume'] or 0)+(r['putVolume'] or 0)>0 for r in stocks)
        conclusions.append({'topic':'個股選擇權能提供多少交叉證據',
            'fact':end+' 收錄 '+str(len(stocks))+' 檔個股選擇權標的，其中 '+str(active)+' 檔的選擇權一般時段至少有一筆成交。',
            'reading':'只有名錄與報價並不代表市場活躍；少量成交難以辨識策略，無成交也不等於沒有避險需求。股價、法人買賣超與選擇權量分開列示，缺值不補零。'})
    missing=[d for d in dates if d not in cash]
    return {'version':VERSION,'start':start,'end':end,'base':base,'days':len(dates),'cashDays':valid,
        'status':'partial' if missing or base not in cash or inst_errors or not option_entry else 'available',
        'missingDates':missing,'baselineMissing':base not in cash,'errors':errors,'institutionalErrors':inst_errors,
        'cashSources':evidence,'institutionalSources':inst_evidence,'optionsSource':option_entry,
        'daily':daily,'stocks':stocks,'institutionalBreadth':sample,'correlation':corr,'correlationDays':len(pairs),
        'sections':sections,'conclusions':conclusions,
        'charts':[c for c in [p.chart('現股加權指數',[{'date':r['date'],'value':r['spot']} for r in daily],'指數點'),
            p.chart('大台減現股收盤價差（非同步）',[{'date':r['date'],'value':r['closingGap']} for r in daily],'指數點')] if c]}
