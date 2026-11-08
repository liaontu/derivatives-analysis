"""Evidence-based cash/futures/options synthesis using dated verified archives."""
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import correlation, median
import hashlib
import re

if __package__:
    from . import derivative_period as p, market_history as h, market_completion as mc, cross_market as cross
else:
    import derivative_period as p, market_history as h, market_completion as mc, cross_market as cross

VERSION='cash-futures-options-review-v1'
METHODS=[
    {'label':'期交所法人交易統計口徑','url':'https://www.taifex.com.tw/file/taifex/CHINESE/3/7_12_help.htm'},
    {'label':'期交所契約調整事項','url':'https://www.taifex.com.tw/cht/4/contractAdj'},
    {'label':'期交所結算制度問答','url':'https://www.taifex.com.tw/cht/9/tradersQAClearing'}]


def settlement_expiries(output):
    index=h.read(output/'market-completion.json',{})
    if not index.get('references'):return {}
    refs=mc.verified(output,index['references'])
    return {r['month']:r['date'] for r in refs['settlements'] if r['kind']=='INDEX_OPTIONS' and r['contract']=='TXO'}


def active(month,day,expiries):
    return expiries.get(month,'9999-12-31')>day


def corr(x,y):
    pairs=[(a,b) for a,b in zip(x,y) if p.finite(a) and p.finite(b)]
    value=None
    if len(pairs)>=3:
        try:value=correlation([r[0] for r in pairs],[r[1] for r in pairs])
        except (ValueError,ArithmeticError):pass
    return {'r':value,'n':len(pairs)}


def med(values):
    values=[v for v in values if p.finite(v)]
    return median(values) if values else None


def traded(row,key='last'):
    return row.get(key) if p.finite(row.get(key)) and row[key]>0 and (row.get('volume') or 0)>0 else None


def maturity_oi(rows,day,expiries):
    groups=defaultdict(list)
    for r in rows:
        if r['contract']=='TXO' and r['session']=='一般':groups[r['month']].append(r)
    return {month:{'month':month,'expiry':expiries.get(month),'active':active(month,day,expiries),
            **{cp:p.total([r for r in values if r['cp']==cp],'oi') for cp in ('C','P')}} for month,values in groups.items()}


def compare_stock(doc,dates,base_quotes,base_futures,options,adjusted):
    """One fixed standard contract; missing observations never bridge daily returns."""
    if len({r[0] for r in doc['rows']})!=len(doc['rows']) or any(len(r)!=len(doc['fields']) for r in doc['rows']):
        raise ValueError('Ambiguous stock daily rows')
    if any(len(r)!=len(doc['futureFields']) for r in doc['futures']):raise ValueError('Stock futures schema mismatch')
    rows={r[0]:dict(zip(doc['fields'],r)) for r in doc['rows'] if r[0] in dates}
    last=rows.get(dates[-1],{});base=base_quotes.get(doc['code'],{}).get('close')
    events=sorted({r['date'] for r in doc.get('actions',[]) if dates[0]<=r['date']<=dates[-1]}|set(adjusted))
    q={'code':doc['code'],'name':doc['name'],'market':doc['market'],'base':base,'last':last.get('close'),
       'returnPct':p.pct(last.get('close'),base),'amount':last.get('amount'),'actionDates':events,
       'pricedDays':sum(p.finite(rows.get(d,{}).get('close')) for d in dates),'expectedDays':len(dates)}
    for key in ('foreign','trust'):
        values=[rows.get(d,{}).get(key) for d in dates]
        q[key+'Days']=sum(p.finite(v) for v in values)
        q[key]=sum(values) if all(p.finite(v) for v in values) else None
    futures=[dict(zip(doc['futureFields'],r)) for r in doc['futures'] if r[0] in dates]
    candidates=[r for r in futures if r['date']==dates[-1] and traded(r,'close') is not None and re.fullmatch(r'\d{6}',r['month'])]
    chosen=max(candidates,key=lambda r:(r['volume'],r['contract'],r['month'])) if candidates else None
    if chosen:
        identity=chosen['contract'],chosen['month'];old=base_futures.get(identity,{})
        quotes={r['date']:r for r in futures if (r['contract'],r['month'])==identity}
        prev_s=base;prev_f=traded(old);pairs_s=[];pairs_f=[]
        for day in dates:
            s=rows.get(day,{}).get('close');f=traded(quotes.get(day,{}),'close')
            if day not in events:
                pairs_s.append(p.pct(s,prev_s));pairs_f.append(p.pct(f,prev_f))
            prev_s,prev_f=s,f
        q['future']={'contract':identity[0],'month':identity[1],'base':traded(old),'last':chosen['close'],
            'returnPct':p.pct(chosen['close'],traded(old)),'basis':chosen['closingBasis'],'basisPct':chosen['basisPct'],
            'oi':chosen.get('oi'),'baseOi':old.get('oi'),'volume':chosen['volume'],'correlation':corr(pairs_s,pairs_f)}
    q['option']=options
    return q


def build(output,analysis):
    index=h.read(output/'market-completion.json',{})
    if not index.get('stocks'):return None
    start,end,base=analysis['start'],analysis['end'],analysis['base'];dates=[r['date'] for r in analysis['daily']]
    if not dates:return None
    if index.get('start','9999')>start or index.get('end','')<end:raise ValueError('Cross-market review requires complete dated stock range')
    refs=mc.verified(output,index['references']);expiries=settlement_expiries(output)
    history=h.read(output/'market-history.json');proof={};cache={};missing=[]
    def load(feed,day):
        key=feed,day
        if key in cache:return cache[key]
        entry=history['feeds'].get(feed,{}).get(day)
        if not entry:missing.append({'feed':feed,'date':day});cache[key]=[];return []
        doc=h.load_day(output,entry)
        if doc['date']!=day or doc['feed']!=feed or any(r['date']!=day for r in doc['rows']):raise ValueError('Cross-market source identity changed')
        proof.setdefault(feed,{})[day]=entry['sha256'];cache[key]=doc['rows'];return doc['rows']
    base_cash,cash_proof=cross.cash_snapshot(output,base)
    supplement=h.read(output/'market-supplement.json');otc_source=supplement['documents']['cash-TPEX-'+base]
    otc=mc.verified(output,otc_source)
    baselines={'TWSE':base_cash['quotes'],'TPEX':{r['code']:r for r in otc['rows']}}
    base_futures={(r['contract'],r['month']):r for r in load('futures',base) if r['session']=='一般' and re.fullmatch(r'\d{6}',r['month'])}
    watch=h.read(output/'stock-option-watch'/(end+'.json'),{})
    if watch.get('date')!=end:raise ValueError('Missing same-day stock-option roster')
    option_map=watch.get('stocks',{});option_stats={}
    for symbol,stock in option_map.items():
        per_day=[]
        for day in dates:
            rows=[r for r in load('options',day) if r['contract']==symbol and r['session']=='一般']
            per_day.append({'date':day,'C':p.total([r for r in rows if r['cp']=='C'],'volume'),
                'P':p.total([r for r in rows if r['cp']=='P'],'volume'),'tradedSeries':sum((r.get('volume') or 0)>0 for r in rows)})
        option_stats[stock['code']]={'contract':symbol,'dataDays':sum(p.finite(r['C']) and p.finite(r['P']) for r in per_day),
            'activeDays':sum(r['tradedSeries']>0 for r in per_day),'callVolume':p.total(per_day,'C'),
            'putVolume':p.total(per_day,'P'),'latest':per_day[-1]}
    adjustments=defaultdict(set)
    for row in refs.get('contractAdjustments',[]):
        if start<=row['date']<=end:adjustments[row['code']].add(row['date'])
    stocks=[];stock_proof={};docs={}
    for entry in index['stocks']:
        doc=mc.verified(output,entry)
        if (doc['market'],doc['code'])!=(entry['market'],entry['code']):raise ValueError('Stock shard identity mismatch')
        stocks.append(compare_stock(doc,dates,baselines[doc['market']],base_futures,option_stats.get(doc['code']),adjustments[doc['code']]))
        docs[doc['code']]=doc;stock_proof[entry['path']]=entry['sha256']
    aggregates=[];cohorts=[];breadth=[]
    for market,label in [('TWSE','上市'),('TPEX','上櫃')]:
        members=[r for r in stocks if r['market']==market]
        valid=[r for r in members if p.finite(r['returnPct']) and not r['actionDates']]
        aggregates.append({'market':market,'label':label,'universe':len(members),'valid':len(valid),
            'actionExcluded':sum(bool(r['actionDates']) for r in members),'missingEndpoint':sum(not p.finite(r['returnPct']) for r in members),
            'up':sum(r['returnPct']>0 for r in valid),'down':sum(r['returnPct']<0 for r in valid),'flat':sum(r['returnPct']==0 for r in valid),
            'median':med(r['returnPct'] for r in valid),'down20':sum(r['returnPct']<=-20 for r in valid)})
        for field,label2 in [('foreign','外資'),('trust','投信')]:
            for sign,desc in [(1,'累計買超'),(-1,'累計賣超')]:
                group=[r for r in valid if p.finite(r[field]) and r[field]*sign>0]
                cohorts.append({'market':market,'label':label+' '+label2+' '+desc,'stocks':len(group),
                    'median':med(r['returnPct'] for r in group),'down':sum(r['returnPct']<0 for r in group)})
        for day in dates:
            points=[]
            for stock in members:
                row=next((dict(zip(docs[stock['code']]['fields'],r)) for r in docs[stock['code']]['rows'] if r[0]==day),{})
                # Exclude the ex-date only for daily breadth, all event stocks for period returns.
                if day not in stock['actionDates'] and p.finite(row.get('returnPct')):points.append(row['returnPct'])
            up=sum(v>0 for v in points);down=sum(v<0 for v in points)
            breadth.append({'date':day,'market':market,'valid':len(points),'up':up,'down':down,'upShare':p.ratio(up,up+down)})
    # Official PCR excludes OI that expires on the observation date. Keep that
    # traded volume, but do not carry its displayed intraday OI into future exposure.
    oi_days={day:maturity_oi(load('options',day),day,expiries) for day in [base]+dates}
    maturity=[]
    for month,row in sorted(oi_days[end].items()):
        old=oi_days[base].get(month,{});maturity.append({**row,'baseC':old.get('C') if old.get('active') else None,
            'baseP':old.get('P') if old.get('active') else None,'pc':p.ratio(row['P'],row['C'])})
    option_audit=[];tx_daily=[]
    for day in [base]+dates:
        current=oi_days[day];pc=load('pcr',day);pc=pc[0] if len(pc)==1 else {}
        c=p.total([r for r in current.values() if r['active']],'C');put=p.total([r for r in current.values() if r['active']],'P')
        option_audit.append({'date':day,'activeC':c,'activeP':put,'pc':p.ratio(put,c),
            'expiredC':sum(r['C'] or 0 for r in current.values() if not r['active']),
            'expiredP':sum(r['P'] or 0 for r in current.values() if not r['active']),
            'callDiff':p.change(c,pc.get('callOi')),'putDiff':p.change(put,pc.get('putOi'))})
        fs=[r for r in load('futures',day) if r['contract']=='TX' and r['session']=='一般' and re.fullmatch(r'\d{6}',r['month']) and active(r['month'],day,expiries)]
        tx_daily.append({'date':day,'oi':p.total(fs,'oi')})
    oi_mismatches=[r for r in option_audit if r['callDiff']!=0 or r['putDiff']!=0]
    daily=analysis['crossMarket']['daily'];weeks=[];previous_spot=base_cash['close']
    tx=next(r for r in analysis['futures'] if r['contract']=='TX')
    for w in analysis['weeks']:
        ds=[r for r in daily if w['start']<=r['date']<=w['end']];last=ds[-1]
        weeks.append({**w,'spotPct':p.pct(last['spot'],previous_spot),'stockAmountMean':p.total(ds,'amount')/len(ds) if p.total(ds,'amount') is not None else None,
            'breadthMean':med(r['upShare'] for r in ds),'closingGap':last['closingGap']})
        previous_spot=last['spot']
    foreign=next((r for r in analysis['institutions'] if r['feed']=='futures-institutional' and r['investor']=='外資及陸資'),{})
    trust=next((r for r in analysis['institutions'] if r['feed']=='futures-institutional' and r['investor']=='投信'),{})
    prior_foreign=foreign.get('base');prior_pc=analysis['options']['base'].get('oiRatio');features=[]
    for r in daily:
        features.append({'date':r['date'],'spotPct':r['spotPct'],'foreignChange':p.change(r['foreignFuturesNetOi'],prior_foreign),
            'oiPCChange':p.change(r['oiPC'],prior_pc),'futurePct':r['futurePct']})
        prior_foreign,prior_pc=r['foreignFuturesNetOi'],r['oiPC']
    correlations=[]
    for field,label in [('futurePct','大台日漲跌'),('foreignChange','外資大台淨部位日變化'),('oiPCChange','臺指 OI P/C 日變化')]:
        x=[r[field] for r in features];y=[r['spotPct'] for r in features]
        correlations.append({'label':label,'same':corr(x,y),'next':corr(x[:-1],y[1:])})
    mature_active=[r for r in maturity if r['active']]
    common_months=[r for r in mature_active if re.fullmatch(r'\d{6}',r['month']) and p.finite(r['baseC']) and p.finite(r['baseP'])]
    fixed=max(common_months,key=lambda r:(r['C'] or 0)+(r['P'] or 0)) if common_months else None
    focus=sorted([r for r in stocks if r['option']],key=lambda r:-(r['amount'] or 0))
    leaders=sorted([r for r in stocks if r.get('future')],key=lambda r:-(r['amount'] or 0))[:20]
    sections=[]
    def section(title,note,headers,rows):sections.append({'title':title,'note':note,'headers':headers,'rows':rows})
    section('補齊後的上市櫃市場廣度',
        '按目前官方普通股名錄；期間報酬須同時有 '+base+' 與 '+end+' 有成交收盤。排除期間有公司行動／契約調整的股票，避免減資等機械價格跳動；排除數與缺端點可能重疊。等權中位數不是加權指數，名錄仍有存活偏差。',
        ['市場','目前名錄','有效比較','公司行動排除／缺端點','上漲／下跌／持平','報酬中位數','跌幅至少 20%'],
        [[r['label'],p.n(r['universe']),p.n(r['valid']),p.n(r['actionExcluded'])+' / '+p.n(r['missingEndpoint']),
            ' / '.join(p.n(r[k]) for k in ['up','down','flat']),p.signed(r['median'],2)+'%',p.n(r['down20'])] for r in aggregates])
    section('三市場逐週關係：轉折、交易熱度與籌碼',
        '第一週僅含本期日期；現股量為一般股票成交金額，期貨量為全到期別日夜盤單邊口數，不能相加。股票上漲占比列逐日中位數；價差為非同步收盤。',
        ['期間','現股／大台週漲跌','現股日均成交（億元）','大台日均成交（口）','上漲占比中位數','外資淨部位週變化','成交／OI P/C','期末現期收盤價差'],
        [[r['start']+'～'+r['end'],p.signed(r['spotPct'],2)+'% / '+p.signed(r['txChangePct'],2)+'%',p.n(r['stockAmountMean']/1e8 if r['stockAmountMean'] is not None else None,2),
            p.n(r['txVolumeMean']),p.n(r['breadthMean'],2)+'%',p.signed(r['foreignChange']),p.n(r['volumePC'],2)+'% / '+p.n(r['oiPC'],2)+'%',p.signed(r['closingGap'],2)] for r in weeks])
    section('到期與換月核對：未平倉不能直接當新資金',
        '大台全月合計為一般時段各存續月份的 OI；固定 '+tx['month']+' 契約從遠月變近月，不能把其全部增加都算新資金。選擇權只把官方結算日在觀察日之後的契約列作存續部位；已到期契約的當日成交仍保留。',
        ['項目','基準','期末','可支持的解讀'],[
            ['大台全存續月份 OI（口）',p.n(tx_daily[0]['oi']),p.n(tx_daily[-1]['oi']),'變化 '+p.signed(p.change(tx_daily[-1]['oi'],tx_daily[0]['oi']))+' 口；每口同時有多空'],
            ['TXO 存續買權／賣權 OI',p.n(option_audit[0]['activeC'])+' / '+p.n(option_audit[0]['activeP']),p.n(option_audit[-1]['activeC'])+' / '+p.n(option_audit[-1]['activeP']),'按官方結算日排除已到期'],
            ['原表當日已到期 C／P OI',p.n(option_audit[0]['expiredC'])+' / '+p.n(option_audit[0]['expiredP']),p.n(option_audit[-1]['expiredC'])+' / '+p.n(option_audit[-1]['expiredP']),'不能用於隔日履約價觀察'],
            ['與官方 P/C 原始口數核對',str(len(option_audit)-len(oi_mismatches))+' / '+str(len(option_audit))+' 日一致',str(len(oi_mismatches))+' 日未一致','包含基準日；不一致仍保留證據']])
    section('期末仍存續的選擇權：分到期別比較',
        '同到期別跨期比較，新增履約價仍可能改變結構；沒有基準契約就留白。P/C 為賣權 OI ÷ 買權 OI，未作 Delta 加權，不能辨識買方或賣方策略。',
        ['到期別','基準買權／賣權 OI','期末買權／賣權 OI','期末 OI P/C'],
        [[r['month'],p.n(r['baseC'])+' / '+p.n(r['baseP']),p.n(r['C'])+' / '+p.n(r['P']),p.n(r['pc'],2)+'%'] for r in mature_active])
    section('法人買賣與個股表現：完整資料樣本分組',
        '各股法人累計必須每個交易日有資料，且有起訖價、沒有期間公司行動；缺列不補零。按全期實現買賣超事後分組，屬同期關係，不是可提前交易的訊號；家數及中位報酬不是法人投資組合報酬，也不是買賣超金額。',
        ['市場／法人／分組','完整樣本數','期間報酬中位數','其中下跌家數'],
        [[r['label'],p.n(r['stocks']),p.signed(r['median'],2)+'%',p.n(r['down'])] for r in cohorts])
    def stock_row(r):
        f=r.get('future',{});o=r.get('option') or {}
        return [r['code']+' '+r['name'],p.signed(r['returnPct'],2)+'%',f.get('contract','無成交')+'/'+f.get('month','—'),
            p.signed(f.get('returnPct'),2)+'%',p.signed(f.get('basisPct'),2)+'%',p.signed(r['foreign'])+' / '+p.signed(r['trust']),
            str(r['foreignDays'])+'/'+str(r['expectedDays']),o.get('contract','無個股選擇權'),
            p.n(o.get('callVolume'))+' / '+p.n(o.get('putVolume')),str(o.get('activeDays','—'))+'/'+str(o.get('dataDays','—')),
            '公司行動：'+','.join(r['actionDates']) if r['actionDates'] else '原始價格；非含息報酬']
    columns=['個股','現股期間漲跌','固定股期契約','股期期間漲跌','期末收盤價差%','外資／投信累計（股）','外資資料日','個股選擇權','全期一般盤買／賣權量','有成交／有表日','比較限制']
    section('個股三市場對照：有個股選擇權的全部標的',
        '期末選擇權名錄；股期從該股期末有成交的標準商品選最大成交量契約並固定同月份，大小型不混加。缺基準不跨月補價；現股與股期收盤不同步。股期 OI 與買賣權口數不能合成曝險。',columns,[stock_row(r) for r in focus])
    section('活躍個股期貨與現股對照',
        '有期末股期成交的標的中，按期末現股成交金額列前 20 檔，非報酬排名或買賣推薦；完整普通股名錄仍可在「三市場補資料」逐檔查詢。',columns,[stock_row(r) for r in leaders])
    section('同期關係與下一日關係分開檢查',
        'Pearson 相關使用日報酬及部位／P/C 的日變化，不用價格水準。缺值不接續、下一日嚴格對齊下一個應有交易日；只有本期少量且非獨立樣本，未做樣本外檢驗，係數不能證明領先、因果或策略有效。',
        ['觀察變數','與同日現股漲跌相關／樣本','與下一日現股漲跌相關／樣本'],
        [[r['label'],p.n(r['same']['r'],3)+' / '+str(r['same']['n']),p.n(r['next']['r'],3)+' / '+str(r['next']['n'])] for r in correlations])
    last=daily[-1];latest=weeks[-1];previous=weeks[-2] if len(weeks)>1 else {}
    period_pct=p.pct(last['spot'],base_cash['close']);same=Counter(r['relation'] for r in daily)
    conclusions=[{'topic':'最新綜合結論：價格、廣度與交易熱度',
        'fact':'加權指數期間 '+p.signed(period_pct,2)+'%、大台固定 '+tx['month']+' 契約 '+p.signed(tx['returnPct'],2)+'%；同向 '+str(same['現期同漲']+same['現期同跌'])+'/'+str(analysis['crossMarket']['correlationDays'])+' 日。'+latest['start']+'～'+end+' 現股 '+p.signed(latest['spotPct'],2)+'%，大台 '+p.signed(latest['txChangePct'],2)+'%。',
        'reading':('現股與期貨同步下跌，最新一週跌幅較前週擴大，風險尚未見價格確認的改善。' if p.finite(period_pct) and period_pct<0 and p.finite(latest['spotPct']) and p.finite(latest['txChangePct']) and latest['txChangePct']<0 and latest['spotPct']<min(0,previous.get('spotPct') or 0) else '按本期及最新一週漲跌確認方向，避免只憑一天反彈判定趨勢改變。')+'這是截至資料日的描述，不是下一日預測。'},
        {'topic':'下跌或上漲是否只集中在權值股',
         'fact':'；'.join(r['label']+'排除公司行動後 '+str(r['valid'])+' 檔，'+str(r['down'])+' 檔下跌，中位數 '+p.signed(r['median'],2)+'%' for r in aggregates)+'。',
         'reading':'全體普通股廣度補充加權指數；等權中位數與市值加權指數差異，不能單憑相關推定資金流向。排除事件與缺價後的樣本不是無偏歷史全市場。'},
        {'topic':'交易熱度與換月效應分開看',
         'fact':'最新一週現股日均成交 '+p.n(latest['stockAmountMean']/1e8 if p.finite(latest['stockAmountMean']) else None,2)+' 億元（較前週 '+p.signed(p.pct(latest['stockAmountMean'],previous.get('stockAmountMean')),2)+'%）；大台日均量較前週 '+p.signed(p.pct(latest['txVolumeMean'],previous.get('txVolumeMean')),2)+'%。全存續月份 OI '+p.n(tx_daily[0]['oi'])+' → '+p.n(tx_daily[-1]['oi'])+'。',
         'reading':'價跌量增反映交易活躍，可能包含停損、換手及避險。固定契約由遠月轉近月的 OI 增量含換月，不全等於新空單或新資金。'},
        {'topic':'法人在期貨與選擇權的配置分歧',
         'fact':'外資大台淨部位 '+p.signed(foreign.get('base'))+' → '+p.signed(foreign.get('last'))+'；投信 '+p.signed(trust.get('base'))+' → '+p.signed(trust.get('last'))+' 口。',
         'reading':'期末部位方向可與價格對照，但不代表同一戶的完整風險；期貨、買權、賣權口數不能直接相加。各法人買賣權淨部位另表列出，僅支持結構判讀，不能確認避險或備兌組合。'},
        {'topic':'選擇權 P/C 變化的實際來源',
         'fact':'排除已到期後，買權 OI '+p.n(option_audit[0]['activeC'])+' → '+p.n(option_audit[-1]['activeC'])+'，賣權 '+p.n(option_audit[0]['activeP'])+' → '+p.n(option_audit[-1]['activeP'])+'；OI P/C '+p.n(option_audit[0]['pc'],2)+'% → '+p.n(option_audit[-1]['pc'],2)+'%。'+('同 '+fixed['month']+' 到期別 P/C '+p.n(p.ratio(fixed['baseP'],fixed['baseC']),2)+'% → '+p.n(fixed['pc'],2)+'%。' if fixed else ''),
         'reading':'先看分子、分母及到期結構；買權增加可能來自買進買權或賣出買權，不能直接宣布市場看多，也不能因低 P/C 推論即將反彈。'},
        {'topic':'個股三市場可以確認到哪一步',
         'fact':str(len(focus))+' 檔個股選擇權標的中，'+str(sum(r['option']['activeDays']>0 for r in focus))+' 檔本期有成交、'+str(sum(r['option']['latest']['tradedSeries']>0 for r in focus))+' 檔期末有成交。已加入同一股票的標準股期對照，缺基準與公司行動分開標示。',
         'reading':'可區分現股與股期價格同步、法人分歧及選擇權交易稀疏；缺少同步深度與帳戶配對，仍不能認定某大戶在作套利、保護性賣權或備兌買權。'},
        {'topic':'同期高度相關，未證明能預測下一日',
         'fact':'大台日漲跌與同日現股相關 '+p.n(correlations[0]['same']['r'],3)+'（'+str(correlations[0]['same']['n'])+' 日），對下一日為 '+p.n(correlations[0]['next']['r'],3)+'（'+str(correlations[0]['next']['n'])+' 日）；外資淨部位變化對下一日為 '+p.n(correlations[1]['next']['r'],3)+'。',
         'reading':'這段樣本支持現期價格同步，不能直接轉成領先交易策略。短期相關可能不穩定，亦未控制其他因素或通過樣本外檢驗。'},
        {'topic':'接下來如何驗證改善或續弱',
         'fact':'每日追蹤現期是否持續同向、上市櫃上漲家數比例、同到期及全月份 OI、外資淨部位變化，以及排除到期後的買賣權結構。',
         'reading':'若反彈同時伴隨多數股票回升、下跌放量減弱及淨空縮小，才增加改善的佐證；若再跌且廣度仍弱，則續弱證據增加。只出現單一訊號先列分歧，不給自動買賣指令。'}]
    buy=next((r for r in cohorts if r['label']=='上市 外資 累計買超'),None)
    sell=next((r for r in cohorts if r['label']=='上市 外資 累計賣超'),None)
    if buy and sell:
        conclusions.insert(4,{'topic':'現股法人買超不等於價格已止跌',
            'fact':'上市外資全期買超且資料完整的 '+str(buy['stocks'])+' 檔中，'+str(buy['down'])+' 檔下跌，中位數 '+p.signed(buy['median'],2)+'%；賣超組中位數 '+p.signed(sell['median'],2)+'%。',
            'reading':'買賣超與股價是同期觀察，可能受股票規模、產業或持股調整影響。不能把事後完整買超組當成事前可交易名單，更不能據此宣稱法人買超有保底效果。'})
    cash_path=p.price_stats([{'date':r['date'],'value':r['spot']} for r in daily],base_cash['close'])
    if cash_path['high']:
        high=cash_path['high']
        conclusions.insert(1,{'topic':'期間路徑：高點之後的變化',
            'fact':'本期最高現股收盤為 '+high['date']+' 的 '+p.n(high['value'],2)+'，期末距此 '+p.signed(p.pct(last['spot'],high['value']),2)+'%；觀察到的最大收盤回撤 '+p.signed(cash_path['maxPriceDrawdownPct'],2)+'%。各週價量與部位轉折見逐週表。',
            'reading':'高點是事後辨識，不是當時可知的賣出訊號；回撤依每日收盤，未涵蓋盤中最低點。'})
    sources={'stockShards':stock_proof,'references':index['references'],
        'referenceInputs':index.get('referenceSources',{}),
        'dailyStockInputs':{d:e for d,e in index.get('sourceDays',{}).items() if base<=d<=end},
        'baseTWSE':cash_proof,'baseTPEX':otc_source,
        'history':proof,'stockOptionRoster':{'path':'stock-option-watch/'+end+'.json','sha256':hashlib.sha256((output/'stock-option-watch'/(end+'.json')).read_bytes()).hexdigest()}}
    return {'version':VERSION,'start':start,'end':end,'base':base,'status':'partial' if missing or oi_mismatches or index.get('status')!='ok' else 'available',
        'missingSources':missing,'sources':sources,'aggregates':aggregates,'cohorts':cohorts,'dailyBreadth':breadth,
        'weeks':weeks,'maturities':maturity,'optionReconciliation':option_audit,'oiMismatches':oi_mismatches,'txOpenInterest':tx_daily,
        'correlations':correlations,'cashPricePath':cash_path,'stockOptions':focus,'activeStockFutures':leaders,
        'stockFuturesCoverage':{'rosterUnderlyings':sum(bool(r.get('futureContracts')) for r in index['stocks']),
            'tradedLatest':sum(bool(r.get('future')) for r in stocks),'sameContractEndpoints':sum(p.finite(r.get('future',{}).get('returnPct')) for r in stocks)},
        'sections':sections,'conclusions':conclusions,'methodReferences':METHODS,
        'charts':[p.chart('上市普通股上漲家數占比',[{'date':r['date'],'value':r['upShare']} for r in breadth if r['market']=='TWSE'],'%'),
                  p.chart('上櫃普通股上漲家數占比',[{'date':r['date'],'value':r['upShare']} for r in breadth if r['market']=='TPEX'],'%')]}
