"""Descriptive period review from verified archives; no synthetic continuous futures."""
from collections import defaultdict
from datetime import date, timedelta
from base64 import b64encode
from html import escape
if __package__:
    from . import market_history as h
else:
    import market_history as h

VERSION='derivatives-period-v1'


def finite(x):
    import math
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)


def ratio(a,b):return 100*a/b if finite(a) and finite(b) and b>0 else None
def change(a,b):return a-b if finite(a) and finite(b) else None
def pct(a,b):return ratio(a-b,b) if finite(a) and finite(b) else None
def n(x,d=0):return f'{x:,.{d}f}' if finite(x) else '缺資料'
def signed(x,d=0):return ('+' if x>0 else '')+n(x,d) if finite(x) else '缺資料'
def total(rows,key):return sum(r[key] for r in rows) if rows and all(finite(r.get(key)) for r in rows) else None


def price_stats(points,baseline):
    valid=[p for p in points if finite(p['value']) and p['value']>0]
    high=max(valid,key=lambda p:p['value']) if valid else None
    low=min(valid,key=lambda p:p['value']) if valid else None
    # A price drawdown is not a leveraged account drawdown.
    peak=baseline if finite(baseline) and baseline>0 else None;dd=None
    for p in valid:
        peak=max(peak or p['value'],p['value'])
        dd=min(dd or 0,pct(p['value'],peak))
    last=points[-1]['value'] if points else None
    return {'baseline':baseline,'last':last,'returnPct':pct(last,baseline),
            'high':high,'low':low,'maxPriceDrawdownPct':dd,'pricedDays':len(valid)}


def chart(title,points,unit):
    values=[p['value'] for p in points if finite(p['value'])]
    if not values:return None
    lo,hi=min(values),max(values);span=hi-lo or max(abs(hi)*.02,1)
    lo-=span*.08;hi+=span*.08
    marks=[];path=[];connected=False
    for i,p in enumerate(points):
        if not finite(p['value']):connected=False;continue
        x=70+330*i/max(len(points)-1,1);y=185-155*(p['value']-lo)/(hi-lo)
        path.append(('L' if connected else 'M')+f'{x:.2f},{y:.2f}');connected=True
    for value in [lo,(lo+hi)/2,hi]:
        y=185-155*(value-lo)/(hi-lo)
        marks.append(f'<path d="M70 {y:.2f}H400" stroke="#dce5ec"/><text x="64" y="{y+4:.2f}" text-anchor="end">{escape(n(value,1))}</text>')
    for i in sorted({0,len(points)//2,len(points)-1}):
        x=70+330*i/max(len(points)-1,1)
        marks.append(f'<text x="{x:.2f}" y="214" text-anchor="'+('end' if i==len(points)-1 else 'start' if i==0 else 'middle')+'">'+escape(points[i]['date'][5:])+'</text>')
    svg='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 235" role="img"><title>'+escape(title)+'</title><rect width="420" height="235" rx="8" fill="#f8fafc"/><g font-family="sans-serif" font-size="13" fill="#334155"><text x="12" y="18">'+escape(unit)+'</text>'+''.join(marks)+'</g><path d="'+' '.join(path)+'" fill="none" stroke="#087f8c" stroke-width="2.5"/></svg>'
    return {'title':title,'image':'data:image/svg+xml;base64,'+b64encode(svg.encode()).decode(),
            'note':'橫軸依本期交易日等距排列，縱軸依資料範圍縮放；缺值斷線。'}


def build(output,report):
    index=h.read(output/'market-history.json',{})
    start,end,base=report['periodStart'],report['periodEnd'],report['baseDate']
    days=h.expected_days(output,'futures',start,end)
    used={};cache={}
    def rows(feed,day):
        key=feed,day
        if key in cache:return cache[key]
        entry=index.get('feeds',{}).get(feed,{}).get(day)
        if not entry:cache[key]=[];return []
        doc=h.load_day(output,entry)
        if doc.get('date')!=day or doc.get('feed')!=feed or any(r.get('date')!=day for r in doc['rows']):
            raise ValueError('Period archive identity mismatch: '+feed+' '+day)
        used.setdefault(feed,{})[day]=entry['sha256'];cache[key]=doc['rows']
        return doc['rows']
    def select(feed,day,**criteria):
        return [r for r in rows(feed,day) if all(r.get(k)==v for k,v in criteria.items())]
    def value(feed,day,field,**criteria):
        found=select(feed,day,**criteria)
        return found[0].get(field) if len(found)==1 else None
    def quote(day,contract,month):
        return value('futures',day,'last',contract=contract,month=month,session='一般')
    def trading_volume(day,contract):
        return total([r for r in rows('futures',day) if r['contract']==contract and '/' not in r['month']], 'volume')
    futures=[]
    for contract,label in [('TX','大台'),('MTX','小台'),('TMF','微台'),('TE','電子'),('TF','金融')]:
        candidates=[r for r in select('futures',end,contract=contract,session='一般') if '/' not in r['month'] and finite(r.get('last')) and (r.get('volume') or 0)>0]
        if not candidates:continue
        current=max(candidates,key=lambda r:r['volume']);month=current['month']
        points=[{'date':d,'value':quote(d,contract,month)} for d in days]
        volumes=[{'date':d,'value':trading_volume(d,contract)} for d in days]
        known=[x for x in volumes if finite(x['value'])]
        futures.append({'contract':contract,'label':label,'month':month,**price_stats(points,quote(base,contract,month)),
            'oi':current.get('oi'),'baseOi':value('futures',base,'oi',contract=contract,month=month,session='一般'),
            'volumeDays':len(known),'totalVolume':sum(x['value'] for x in known) if known else None,
            'dailyVolumeMean':sum(x['value'] for x in known)/len(known) if known else None,
            'volumePeak':max(known,key=lambda x:x['value']) if known else None,'prices':points,'volumes':volumes})
    institutions=[]
    for feed,contract in [('futures-institutional','臺股期貨'),('options-institutional','臺指選擇權')]:
        for row in select(feed,end,contract=contract):
            criteria={'contract':contract,'investor':row['investor'],'cp':row.get('cp')}
            points=[{'date':d,'value':value(feed,d,'netOi',**criteria)} for d in days]
            vals=[p for p in points if finite(p['value'])]
            first=value(feed,base,'netOi',**criteria)
            institutions.append({'feed':feed,'investor':row['investor'],'cp':row.get('cp'),
                'base':first,'last':row['netOi'],'change':change(row['netOi'],first),
                'min':min(vals,key=lambda p:p['value']) if vals else None,'max':max(vals,key=lambda p:p['value']) if vals else None,
                'validDays':len(vals),'points':points})
    daily=[]
    tx=next((r for r in futures if r['contract']=='TX'),None)
    for d in days:
        p=select('pcr',d);p=p[0] if len(p)==1 else {}
        daily.append({'date':d,'txClose':quote(d,'TX',tx['month']) if tx else None,
            'txVolume':trading_volume(d,'TX'),
            'foreignNetOi':value('futures-institutional',d,'netOi',contract='臺股期貨',investor='外資及陸資',cp=None),
            'trustNetOi':value('futures-institutional',d,'netOi',contract='臺股期貨',investor='投信',cp=None),
            **{k:p.get(k) for k in ['callVolume','putVolume','volumeRatio','callOi','putOi','oiRatio']}})
    base_p=select('pcr',base);base_p=base_p[0] if len(base_p)==1 else {}
    calls=total(daily,'callVolume');puts=total(daily,'putVolume')
    option={'callVolume':calls,'putVolume':puts,'weightedVolumePC':ratio(puts,calls),
            'base':base_p,'last':daily[-1] if daily else {},
            'volumePCRange':[min((r['volumeRatio'] for r in daily if finite(r['volumeRatio'])),default=None),max((r['volumeRatio'] for r in daily if finite(r['volumeRatio'])),default=None)],
            'oiPCRange':[min((r['oiRatio'] for r in daily if finite(r['oiRatio'])),default=None),max((r['oiRatio'] for r in daily if finite(r['oiRatio'])),default=None)]}
    groups=defaultdict(list)
    for r in daily:
        d=date.fromisoformat(r['date']);groups[(d-timedelta(days=d.weekday())).isoformat()].append(r)
    weeks=[];prior={'txClose':tx['baseline'] if tx else None,'foreignNetOi':value('futures-institutional',base,'netOi',contract='臺股期貨',investor='外資及陸資',cp=None)}
    for group in groups.values():
        last=group[-1];v=total(group,'txVolume')
        weeks.append({'start':group[0]['date'],'end':last['date'],'days':len(group),'txClose':last['txClose'],
            'txChangePct':pct(last['txClose'],prior['txClose']),'txVolumeMean':v/len(group) if finite(v) else None,
            'foreignNetOi':last['foreignNetOi'],'foreignChange':change(last['foreignNetOi'],prior['foreignNetOi']),
            'volumePC':ratio(total(group,'putVolume'),total(group,'callVolume')),'oiPC':last['oiRatio']})
        prior=last
    fcm=[]
    for feed in ['futures-fcm','options-fcm']:
        products=defaultdict(lambda:defaultdict(float));names={};available=0
        for d in days:
            data=rows(feed,d)
            if data:available+=1
            for r in data:products[r['contract']][r['code']]+=r['volume'];names[r['code']]=r['name']
        for product,vols in products.items():
            ranked=sorted(vols.items(),key=lambda x:(-x[1],x[0]));v=sum(vols.values())
            fcm.append({'feed':feed,'contract':product,'days':available,'expectedDays':len(days),'volume':v,
                'codes':len(vols),'activeCodes':sum(x>0 for x in vols.values()),'top5Share':ratio(sum(x[1] for x in ranked[:5]),v),
                'leaders':[{'code':c,'name':names[c],'volume':v,'share':ratio(v,sum(vols.values()))} for c,v in ranked[:5]]})
    fcm.sort(key=lambda r:(r['feed'],-r['volume'],r['contract']))
    sections=[]
    def section(title,note,headers,body):sections.append({'title':title,'note':note,'headers':headers,'rows':body})
    section('主要期貨：同到期契約價格與交易活動',
        '每商品固定採期末一般時段最活躍到期契約，與 '+base+' 同契約成交收盤比較，避免近月換月價差。這是期末選樣的描述統計，不是交易回測；缺成交日的高低點與回撤僅依可觀測收盤，可能低估波動。同契約未平倉成長包含換月移入，不全算新資金。期間量為該商品所有到期別、一般＋盤後單邊口數，排除已含在各月份的跨月價差列；不同規格口數不當作相同曝險。',
        ['商品／到期','基準→期末收盤','期間漲跌','收盤高／低（日期）','價格最大回撤','價／量有效日','期間單邊成交／日均','同契約 OI 基準→期末'],
        [[r['label']+' '+r['contract']+'/'+r['month'],n(r['baseline'],2)+' → '+n(r['last'],2),signed(r['returnPct'],2)+'%',
          (n(r['high']['value'],2)+' ('+r['high']['date']+') / '+n(r['low']['value'],2)+' ('+r['low']['date']+')') if r['high'] else '缺資料',
          n(r['maxPriceDrawdownPct'],2)+'%',str(r['pricedDays'])+'/'+str(r['volumeDays'])+'（應有 '+str(len(days))+'）',
          n(r['totalVolume'])+' / '+n(r['dailyVolumeMean']),n(r['baseOi'])+' → '+n(r['oi'])] for r in futures])
    section('逐週轉折（第一週只含本期間日期）','期末與前週期末比較；第一週對 '+base+'。P/C 用每週賣權成交量合計 ÷ 買權成交量合計，不平均每日比率。',
        ['期間／日數','大台固定契約收盤／週變化','大台日均量','外資淨部位／週變化','成交 P/C','期末 OI P/C'],
        [[r['start']+'～'+r['end']+' / '+str(r['days']),n(r['txClose'])+' / '+signed(r['txChangePct'],2)+'%',n(r['txVolumeMean']),
          signed(r['foreignNetOi'])+' / '+signed(r['foreignChange']),n(r['volumePC'],2)+'%',n(r['oiPC'],2)+'%'] for r in weeks])
    section('法人淨部位：期末、變化與期間極值','大台與臺指選擇權各權別分開；淨部位是未平倉存量，不能將每日淨未平倉相加為資金流，權利金／Delta 未加權，無法辨識避險或組合策略。',
        ['商品／法人','基準淨部位','期末淨部位','淨變化','最低（日期）','最高（日期）','有效日'],
        [[('大台' if r['feed'].startswith('futures') else '臺指 '+r['cp'])+' / '+r['investor'],signed(r['base']),signed(r['last']),signed(r['change']),
          signed(r['min']['value'])+' ('+r['min']['date']+')' if r['min'] else '缺資料',signed(r['max']['value'])+' ('+r['max']['date']+')' if r['max'] else '缺資料',str(r['validDays'])+'/'+str(len(days))] for r in institutions])
    section('臺指選擇權：成交結構與未平倉','全期成交可以加總；未平倉只比較時點，不跨日加總。跨到期日與大量新契約，OI 比率變動不能單獨解讀為情緒或預測。',
        ['指標','數值'],[['買權／賣權累計成交（口）',n(calls)+' / '+n(puts)],['全期加權成交 P/C',n(option['weightedVolumePC'],2)+'%'],
        ['每日成交 P/C 最低～最高','～'.join(n(v,2)+'%' for v in option['volumePCRange'])],
        ['未平倉 P/C 基準→期末',n(base_p.get('oiRatio'),2)+'% → '+n(option['last'].get('oiRatio'),2)+'%'],
        ['買權未平倉 基準→期末',n(base_p.get('callOi'))+' → '+n(option['last'].get('callOi'))],
        ['賣權未平倉 基準→期末',n(base_p.get('putOi'))+' → '+n(option['last'].get('putOi'))]])
    section('期貨商完整日表：全期各商品成交與集中度','官方雙邊口數，含經紀與自營代碼但分列，不合併金控。列出所有官方商品分類；STF、ETF 等是合併類別。前五名占比按全期量排名，不是買賣方向、分點或大戶持倉。',
        ['市場／商品','覆蓋日','期間雙邊量','有成交／收錄代碼','前五名占比','前三名代碼／名稱／占比'],
        [[('期貨' if r['feed'].startswith('futures') else '選擇權')+'/'+r['contract'],str(r['days'])+'/'+str(r['expectedDays']),n(r['volume']),str(r['activeCodes'])+'/'+str(r['codes']),n(r['top5Share'],2)+'%',
          '；'.join(x['code']+' '+x['name']+' '+n(x['share'],2)+'%' for x in r['leaders'][:3])] for r in fcm])
    section(str(len(days))+' 日逐日追蹤','大台為固定到期成交收盤；價格與交易量口徑同上。缺值保留，未平倉不跨日加總。',
        ['日期','大台收盤','大台成交量','外資淨部位','投信淨部位','買權量','賣權量','成交 P/C','OI P/C'],
        [[r['date']]+[n(r[k],2 if 'Ratio' in k else 0) for k in ['txClose','txVolume','foreignNetOi','trustNetOi','callVolume','putVolume','volumeRatio','oiRatio']] for r in daily])
    us=[]
    us_days=h.expected_days(output,'us-options',start,end) or []
    for category,label in [('SUM OF ALL PRODUCTS','Cboe 全類別'),('INDEX OPTIONS','Cboe 指數'),('EQUITY OPTIONS','Cboe 個股')]:
        records=[r for d in us_days for r in select('us-options',d,contract=category)]
        c,p=total(records,'callVolume'),total(records,'putVolume')
        us.append({'category':category,'label':label,'days':len(records),'expected':len(us_days),'calls':c,'puts':p,
                   'weightedPC':p/c if finite(c) and c>0 and finite(p) else None})
    section('美國市場：可取得範圍的期間成交結構','美國依本地市場日曆，共 '+str(len(us_days))+' 個交易日。P/C 單位為倍，與台灣的百分比不同；全類別與子類別不可再相加。Cboe 範圍依原公開統計，不涵蓋完整可成交選擇權鏈。',
        ['分類','覆蓋日','買權累計量','賣權累計量','全期成交 P/C（倍）'],
        [[r['label'],str(r['days'])+'/'+str(r['expected']),n(r['calls']),n(r['puts']),n(r['weightedPC'],3)] for r in us])
    conclusions=[]
    if tx:
        conclusions.append({'topic':'期間價格與風險路徑',
            'fact':'大台 '+tx['month']+' 契約從 '+n(tx['baseline'])+' 至 '+n(tx['last'])+'，期間 '+signed(tx['returnPct'],2)+'%；以每日成交收盤計算的最大價格回撤 '+n(tx['maxPriceDrawdownPct'],2)+'%。',
            'reading':'固定到期契約避免換月跳點；回撤只是價格路徑，沒有保證金、部位大小與費用，不能當作交易帳戶績效。'})
    if len(weeks)>1:
        recent,previous=weeks[-1],weeks[-2]
        conclusions.append({'topic':'最近一週價量變化',
            'fact':recent['start']+'～'+recent['end']+' 大台同契約 '+signed(recent['txChangePct'],2)+'%，全到期別日均成交 '+n(recent['txVolumeMean'])+' 口；較前一週日均量 '+signed(pct(recent['txVolumeMean'],previous['txVolumeMean']),2)+'%。',
            'reading':'價格下跌且成交升溫時，表示交易活動在下跌過程中增加；仍無法只由價量區分追空、停損、平多或避險。這裡不推定跌勢必然延續或已落底。'})
    foreign=next((r for r in institutions if r['feed']=='futures-institutional' and r['investor']=='外資及陸資'),None)
    trust=next((r for r in institutions if r['feed']=='futures-institutional' and r['investor']=='投信'),None)
    if foreign and trust:
        conclusions.append({'topic':'法人配置分歧',
            'fact':'外資大台淨部位 '+signed(foreign['base'])+' → '+signed(foreign['last'])+'（變化 '+signed(foreign['change'])+'）；投信 '+signed(trust['base'])+' → '+signed(trust['last'])+'（變化 '+signed(trust['change'])+'）。',
            'reading':'這反映參與者的部位配置不同，可能涉及現貨避險或價差交易；資料無法確認同一客戶、現貨配對或交易動機。不能只看外資淨空就判定後市下跌。'})
    conclusions.append({'topic':'選擇權買賣權結構',
        'fact':'全期加權成交 P/C '+n(option['weightedVolumePC'],2)+'%；未平倉 P/C 從 '+n(base_p.get('oiRatio'),2)+'% 至 '+n(option['last'].get('oiRatio'),2)+'%。',
        'reading':'成交結構與未平倉是不同指標。每筆買權／賣權成交都有買賣雙方；未取得方向、權利金曝險與策略配對，不能說資金一定在做多或做空。'})
    conclusions.append({'topic':'未平倉比率的分子與分母',
        'fact':'買權未平倉 '+n(base_p.get('callOi'))+' → '+n(option['last'].get('callOi'))+'（'+signed(pct(option['last'].get('callOi'),base_p.get('callOi')),2)+'%）；賣權 '+n(base_p.get('putOi'))+' → '+n(option['last'].get('putOi'))+'（'+signed(pct(option['last'].get('putOi'),base_p.get('putOi')),2)+'%）。',
        'reading':'應拆看分子、分母及到期結構，避免把 P/C 下降直接翻譯為看多。跨到期別的 OI 變動包含契約到期與新增，無法還原持倉成本或買賣方動機。'})
    for feed,label in [('futures-large','大台換算'),('options-large','臺指選擇權')]:
        for row in report.get('panels',{}).get(feed,{}).get('rows',[]):
            conclusions.append({'topic':label+(' '+row['cp'] if row.get('cp') else '')+' 大額部位集中',
                'fact':'前五大多方占比 '+n(row.get('longShare'),2)+'%、空方 '+n(row.get('shortShare'),2)+'%；較基準各變動 '+signed(row.get('changes',{}).get('longShare'),2)+'、'+signed(row.get('changes',{}).get('shortShare'),2)+' 個百分點。',
                'reading':'這是各自多方／空方排行的集中度，兩邊不必是同一群人；台指期貨為交易所換算口徑，不與大台法人數字直接相減。'})
    review=report.get('events',{}).get('period',[])
    coverage=[r['coveragePct'] for r in review if finite(r['coveragePct'])]
    if coverage:
        conclusions.append({'topic':'訊號回測能支持到哪裡',
            'fact':'各規則可評估比例為 '+n(min(coverage),2)+'%～'+n(max(coverage),2)+'%；不同有效訊號日最多 '+str(max(r['distinctSignalDates'] for r in review))+' 日。',
            'reading':'缺進場、缺退出與尚未到觀察日的樣本很多；同日多履約價亦相關。即使賣權組的歷史報酬較高，也不能判定策略已有效，或以此反向挑選最佳策略。到期未結算事件不能冒充完整到期損益。'})
    cfe=report.get('panels',{}).get('us-futures',{})
    compared=[r for r in cfe.get('rows',[]) if finite(r.get('changes',{}).get('price')) and finite(r.get('price'))]
    if compared:
        row=compared[0];prior=row['price']-row['changes']['price']
        conclusions.append({'topic':'美國波動率與部位的補充觀察',
            'fact':'CFE '+row['symbol']+'（'+row['expiry']+' 到期）結算價 '+n(prior,4)+' → '+n(row['price'],4)+'，變化 '+signed(pct(row['price'],prior),2)+'%。CFTC 部位日為 '+str(report.get('panels',{}).get('us-cot-financial',{}).get('date'))+'。',
            'reading':'這是 VIX 期貨同契約價格，並非 VIX 現貨或台灣隱含波動率。CFTC 各類參與者可能做跨市場避險；NASDAQ MINI 與 Consolidated 範圍重疊，不相加。美國與台灣資訊不能直接推定因果。'})
    conclusions.append({'topic':'下一期的核對事項',
        'fact':'繼續追蹤同契約價格與量、外資及投信淨部位變化、買賣權各自 OI 與大額集中度。期貨商成交量只用來追蹤交易熱度。',
        'reading':'履約價分布按到期別分開，先排除已到期週契約再觀察下一交易日；不把集中履約價當確定支撐壓力。個股選擇權同步三邊報價不足時，不確認可成交套利。'})
    charts=[chart('大台固定到期成交收盤',tx['prices'],'指數點') if tx else None,
            chart('外資大台淨未平倉',[{'date':r['date'],'value':r['foreignNetOi']} for r in daily],'口'),
            chart('臺指選擇權未平倉 P/C',[{'date':r['date'],'value':r['oiRatio']} for r in daily],'%')]
    return {'version':VERSION,'start':start,'end':end,'base':base,'days':len(days),'futures':futures,
            'institutions':institutions,'options':option,'weeks':weeks,'fcm':fcm,'daily':daily,'usOptions':us,'sources':used,
            'sections':sections,'conclusions':conclusions,'charts':[c for c in charts if c],
            'scope':'以台灣資料作完整期間分析；美國另附既有 Cboe／CFE／CFTC 範圍，並非 CME 全行情或美股完整選擇權鏈。'}
