"""Validated free reference data. Preserve raw responses and failed attempts."""
import argparse
import csv
from datetime import date, datetime, timezone
from html.parser import HTMLParser
import io
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlencode

try:
    from . import market_history as h
except ImportError:
    import market_history as h

ROOT = 'market-supplement'
VERSION = 'market-supplement-v1'
API = 'https://openapi.taifex.com.tw/v1/'


def decode(raw):
    try: return raw.decode('utf-8-sig')
    except UnicodeDecodeError: return raw.decode('cp950')


def records(raw, required):
    text=decode(raw)
    rows=json.loads(text) if text.lstrip().startswith(('[','{')) else list(csv.DictReader(io.StringIO(text)))
    if not isinstance(rows,list) or not rows: raise ValueError('Expected a nonempty official table')
    if any(not isinstance(r,dict) or not required<=set(r) for r in rows): raise ValueError('Official table schema changed')
    return rows


def numeric(value, nullable=False):
    s=str(value).replace(',','').replace('\u3000','').strip()
    if nullable and s in ('','--','---','-','None'): return None
    if not re.fullmatch(r'-?\d+(?:\.\d+)?',s): raise ValueError('Invalid numeric value: '+s[:60])
    return float(s)


def iso(value):
    s=str(value).strip()
    m=re.fullmatch(r'(\d{3,4})[年/-]?(\d{2})[月/-]?(\d{2})日?',s)
    if not m: raise ValueError('Invalid official date: '+s)
    y,month,day=map(int,m.groups())
    return date(y+1911 if y<1911 else y,month,day).isoformat()


class Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.stack=[];self.tables=[]
    def handle_starttag(self,tag,attrs):
        if tag=='table': self.stack.append({'rows':[],'row':None,'cell':None})
        if not self.stack: return
        t=self.stack[-1]
        if tag=='tr': t['row']=[]
        if tag in ('th','td'): t['cell']=''
    def handle_data(self,data):
        if self.stack and self.stack[-1]['cell'] is not None:self.stack[-1]['cell']+=data
    def handle_endtag(self,tag):
        if not self.stack:return
        t=self.stack[-1]
        if tag in ('th','td') and t['cell'] is not None:
            if t['row'] is not None:t['row'].append(re.sub(r'\s+|<br\s*/?>','',t['cell']))
            t['cell']=None
        if tag=='tr' and t['row'] is not None:
            if t['row']:t['rows'].append(t['row'])
            t['row']=None
        if tag=='table':self.tables.append(self.stack.pop()['rows'])


def parse_settlements(raw, kind, start, end):
    parser=Tables();parser.feed(decode(raw));result=[]
    if kind in ('SSF','SSO'):
        matches=[t for t in parser.tables if t and {'商品代號','到期日','契約月份','最後結算價','標的證券代號'}<=set(t[0])]
        if len(matches)!=1:raise ValueError('Stock settlement table missing/ambiguous')
        table=matches[0];fields=table[0]
        for row in table[1:]:
            if len(row)==1 and ('無' in row[0] or '查無' in row[0]):continue
            if len(row)!=len(fields):raise ValueError('Settlement row width mismatch')
            r=dict(zip(fields,row));day=iso(r['到期日'])
            if not start<=day<=end:raise ValueError('Settlement outside requested month')
            result.append({'date':day,'contract':r['商品代號'],'code':r['標的證券代號'],'name':r['商品名稱'],
                'month':r['契約月份'],'price':numeric(r['最後結算價']),
                'underlyingValue':numeric(r['約定標的物價值'],True),'kind':kind})
    else:
        matches=[t for t in parser.tables if t and any('TXO' in v for v in t[0]) and any('結算日' in v for v in t[0])]
        if len(matches)!=1:raise ValueError('Index option settlement table missing/ambiguous')
        table=matches[0];fields=table[0]
        contracts={i:re.search(r'（([A-Z]+)）|\(([A-Z]+)\)',f) for i,f in enumerate(fields)}
        for row in table[1:]:
            if len(row)!=len(fields):raise ValueError('Index settlement row width mismatch')
            day=iso(row[0])
            if not start<=day<=end:raise ValueError('Index settlement outside requested range')
            for i,match in contracts.items():
                if not match:continue
                price=numeric(row[i],True)
                if price is None:continue
                result.append({'date':day,'contract':match.group(1) or match.group(2),'month':row[1],
                    'price':price,'kind':'INDEX_OPTIONS'})
    keys=[(r['contract'],r['month']) for r in result]
    if len(keys)!=len(set(keys)) or any(r['price']<=0 for r in result):raise ValueError('Invalid or duplicate final settlement')
    return sorted(result,key=lambda r:(r['date'],r['contract'],r['month']))


def roster(raw):
    rows=records(raw,{'Contract','StockCode','StockName','Type'});result=[];seen=set()
    for r in rows:
        symbol,code=r['Contract'].strip(),r['StockCode'].strip()
        if not re.fullmatch(r'[A-Z0-9]{2}F',symbol) or not re.fullmatch(r'\d{4,6}[A-Z]?',code) or symbol in seen:
            raise ValueError('Invalid/duplicate stock futures identity')
        if r['Type'] not in ('','上市普通股標的證券','上櫃普通股標的證券','上市ETF標的證券','上櫃ETF標的證券'):
            raise ValueError('Unknown stock futures category')
        seen.add(symbol);result.append({'contract':symbol,'code':code,'name':r['StockName'],'type':r['Type'] or '來源未分類'})
    return result


def adjustments(raw):
    rows=records(raw,{'Contact','StockId','StockName','CorporateAction','AdjustmentDate','CashDividend(CapitalRepayment)NTD/share','StockDividend'})
    result=[]
    for r in rows:
        result.append({'date':iso(r['AdjustmentDate']),'prefix':r['Contact'].strip(),'code':r['StockId'].strip(),
            'name':r['StockName'],'action':r['CorporateAction'],
            'cash':numeric(r['CashDividend(CapitalRepayment)NTD/share']),'shares':numeric(r['StockDividend'])})
    return result


def adjusted_contracts(raw):
    rows=records(raw,{'Date','Contract','StockCode','ContractName','Futures/Options','UnderlyingSecurityShares','DeliveryMonths',
        'UnderlyingSecurityDistributionofCashDividends','UnderlyingSecurityFairValueOfPreemptiveRightsToParticipateInCashCapitalIncrease'})
    return [{'date':iso(r['Date']),'contract':r['Contract'].strip(),'code':r['StockCode'].strip(),'name':r['ContractName'],
        'kind':r['Futures/Options'],'shares':numeric(r['UnderlyingSecurityShares']),
        'cash':numeric(r['UnderlyingSecurityDistributionofCashDividends'],True),
        'rights':numeric(r['UnderlyingSecurityFairValueOfPreemptiveRightsToParticipateInCashCapitalIncrease'],True),
        'months':r['DeliveryMonths']} for r in rows]


def fees(raw):
    rows=records(raw,{'商品','交易經手費','結算手續費','交割手續費','期貨交易稅比率'})
    # Preserve text such as product-specific rules; never guess a numeric rate.
    return [{k:str(v).strip() for k,v in r.items()} for r in rows]


def companies(raw,market):
    code_key,name_key,short_key=('公司代號','公司名稱','公司簡稱') if market=='TWSE' else ('SecuritiesCompanyCode','CompanyName','CompanyAbbreviation')
    rows=records(raw,{code_key,name_key})
    result=[];seen=set()
    for r in rows:
        code=r[code_key].strip()
        if not re.fullmatch(r'[0-9A-Z]{4,6}',code):raise ValueError('Invalid company membership')
        if len(code)!=4:continue  # TDRs are retained in raw evidence, outside ordinary-stock scope.
        if code in seen:raise ValueError('Duplicate company membership')
        seen.add(code);result.append({'code':code,'name':r.get(short_key) or r[name_key],'market':market})
    return result


def otc_prices(raw,day):
    doc=json.loads(raw)
    if doc.get('date')!=day.replace('-','') or str(doc.get('stat')).lower()!='ok':raise ValueError('TPEx cash date/status mismatch')
    required={'代號','名稱','收盤','成交股數','成交金額(元)'}
    tables=[t for t in doc.get('tables',[]) if t.get('title')=='上櫃股票行情' and required<=set(t.get('fields',[]))]
    if len(tables)!=1:raise ValueError('TPEx cash schema changed: '+str([t.get('fields') for t in doc.get('tables',[])]))
    result=[];seen=set();table=tables[0]
    for line in table['data']:
        if len(line)!=len(table['fields']):raise ValueError('TPEx cash width mismatch')
        r=dict(zip(table['fields'],line));code=r['代號'].strip();volume=numeric(r['成交股數'])
        if not re.fullmatch(r'\d{4}',code):continue
        if code in seen or volume<0:raise ValueError('Duplicate TPEx quote/negative volume')
        seen.add(code);close=numeric(r['收盤'],True) if volume>0 else None
        if close is not None and close<=0:raise ValueError('Invalid TPEx close')
        result.append({'date':day,'code':code,'name':r['名稱'],'close':close,'volume':volume,'amount':numeric(r['成交金額(元)'])})
    return result


def latest_settlement(raw,kind):
    text=decode(raw);rows=json.loads(text) if text.lstrip().startswith('[') else list(csv.DictReader(io.StringIO(text)))
    result=[]
    for r in rows:
        if {'到期日','商品代號','契約月份','最後結算價'}<=set(r):
            result.append({'date':iso(r['到期日']),'contract':r['商品代號'].strip(),'month':r['契約月份'].strip(),'price':numeric(r['最後結算價']),'kind':kind})
        elif {'TheFinalSettlementDay','Contract','ContractDeliveryMonth','TheFinalSettlementPrice'}<=set(r):
            result.append({'date':iso(r['TheFinalSettlementDay']),'contract':r['Contract'].strip(),'month':r['ContractDeliveryMonth'].strip(),'price':numeric(r['TheFinalSettlementPrice']),'kind':kind})
        else:raise ValueError('Latest settlement schema changed')
    if not result:raise ValueError('Latest settlement is empty')
    return result


def exrights(raw,market,start,end):
    doc=json.loads(raw)
    if str(doc.get('stat')).lower()!='ok':raise ValueError('Ex-rights response is not OK')
    if market=='TWSE':
        if doc.get('strDate')!=start.replace('-','') or doc.get('endDate')!=end.replace('-',''):
            raise ValueError('Ex-rights query date mismatch')
        fields,data=doc['fields'],doc['data']
        names=['資料日期','股票代號','股票名稱','除權息前收盤價','除權息參考價','權/息']
    else:
        table=doc['tables'][0];fields,data=table['fields'],table['data']
        names=['除權息日期','代號','名稱','除權息前收盤價','除權息參考價','權/息']
    if not set(names)<=set(fields):raise ValueError('Ex-rights schema changed: '+str(fields))
    result=[]
    for row in data:
        if len(row)!=len(fields):raise ValueError('Ex-rights row width mismatch')
        r=dict(zip(fields,row));day=iso(r[names[0]])
        if not start<=day<=end:raise ValueError('Ex-rights date out of bounds')
        before,reference=numeric(r[names[3]]),numeric(r[names[4]])
        if before<=0 or reference<=0:raise ValueError('Invalid ex-right reference')
        result.append({'date':day,'market':market,'code':r[names[1]].strip(),'name':r[names[2]],
            'before':before,'reference':reference,'factor':reference/before,'action':r[names[5]],
            'basis':'除權息參考價連續化；不是含息總報酬'})
    return result


def month_ranges(start,end):
    import calendar
    cursor=date.fromisoformat(start).replace(day=1)
    while cursor.isoformat()<=end:
        last=date(cursor.year,cursor.month,calendar.monthrange(cursor.year,cursor.month)[1])
        yield cursor.isoformat(),last.isoformat()
        cursor=date(cursor.year+int(cursor.month==12),cursor.month%12+1,1)


def run(output,start='2026-10-01',end=None,client=None,now=None):
    now=now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    history=h.read(output/'market-history.json',{});end=end or history['end']
    client=client or h.Client(output)
    old=h.read(output/(ROOT+'.json'),{});documents=dict(old.get('documents',{}));sources=[];errors=[];blocked=False
    def collect(key,url,parse,params=None,refresh=True):
        nonlocal blocked
        evidence={};previous=documents.get(key,{})
        try:
            if blocked:raise ValueError('來源限制後停止本輪網路請求')
            raw,evidence=client.get(url,params,now[:10] if refresh else '')
            rows=parse(raw)
            client.accept(evidence)
            payload={'schemaVersion':1,'id':key,'rows':rows,'evidence':evidence}
            digest=hashlib.sha256(h.encode(payload)).hexdigest()
            path=ROOT+'/snapshots/'+key+'-'+digest[:12]+'.json'
            h.save(output/path,payload)
            documents[key]={'path':path,'sha256':hashlib.sha256(h.encode(payload)).hexdigest(),'rows':len(rows),
                'source':evidence,'status':'ok'}
            sources.append({'id':key,'status':'ok','url':url,'rows':len(rows),'path':path,'sha256':documents[key]['sha256']})
            print(json.dumps({'source':key,'rows':len(rows),'status':'ok'}),flush=True)
        except Exception as exc:
            blocked=getattr(exc,'code',None) in (401,403,429) or blocked
            failure={'id':key,'url':url,'parameters':params,'checkedAt':now,'error':str(exc)[:300],'evidence':evidence}
            failure_path=ROOT+'/failures/'+key+'-'+now.replace(':','').replace('+','-')+'.json'
            h.save(output/failure_path,failure);errors.append(failure)
            if previous:documents[key]={**previous,'status':'retained','error':failure['error']}
            sources.append({'id':key,'status':'retained' if previous else 'unavailable','url':url,'error':failure['error'],'evidence':failure_path})
            print(json.dumps({'source':key,'error':str(exc)}),flush=True)
    for key,endpoint,parser in [('stock-futures','SSFLists',roster),('contract-adjustments','ContractAdj',adjustments),
        ('adjusted-contracts','SSFAdjustedInfo',adjusted_contracts),('official-fees','FuturesAndOptionsFeeSchedule',fees)]:
        collect(key,API+endpoint,parser)
    collect('companies-TWSE','https://openapi.twse.com.tw/v1/opendata/t187ap03_L',lambda raw:companies(raw,'TWSE'))
    collect('companies-TPEX','https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O',lambda raw:companies(raw,'TPEX'))
    for kind,endpoint in [('SSF','FinalSettlementPriceSSF'),('SSO','FinalSettlementPriceSSO'),('INDEX_OPTIONS','FinalSettlementPriceIndexOptions')]:
        collect('latest-settlement-'+kind,API+endpoint,lambda raw,k=kind:latest_settlement(raw,k))
    # Existing option history includes contracts expired before October; provide
    # final settlement history for those fixed events too, without changing v1.
    option_start=h.read(output/'option-history.json',{}).get('start',start)
    for first,last in month_ranges(option_start,end):
        key='index-settlement-'+first[:7]
        params={'start_year':first[:4],'start_month':first[5:7],'end_year':first[:4],'end_month':first[5:7],'all':'all'}
        collect(key,'https://www.taifex.com.tw/cht/12/optIndxFSP',lambda raw,a=first,b=last:parse_settlements(raw,'INDEX',a,b),params,refresh=last>=end)
    for first,last in month_ranges(start,end):
        for kind,route in [('SSF','sSFFSP'),('SSO','sSOFSP')]:
            params={'down_type':'1','queryYear':first[:4],'queryMonth':first[5:7],'contractId':'all','contractMonth':'all'}
            collect(kind.lower()+'-settlement-'+first[:7],'https://www.taifex.com.tw/cht/12/'+route,
                lambda raw,k=kind,a=first,b=last:parse_settlements(raw,k,a,b),params,refresh=last>=end)
    for first,last in month_ranges(start,end):
        stop=min(last,end)
        for market,url in [('TWSE','https://www.twse.com.tw/rwd/zh/exRight/TWT49U?'+urlencode({'startDate':first.replace('-',''),'endDate':stop.replace('-',''),'response':'json'})),
            ('TPEX','https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?'+urlencode({'startDate':first.replace('-','/'),'endDate':stop.replace('-','/'),'response':'json'}))]:
            collect('exrights-'+market+'-'+first[:7],url,lambda raw,m=market,a=first,b=stop:exrights(raw,m,a,b),refresh=last>=end)
    calendar=h.load_calendar(output)
    from datetime import timedelta
    base=date.fromisoformat(start)-timedelta(days=1)
    while not calendar.is_open(base):base-=timedelta(days=1)
    for day in h.expected_days(output,'futures',base.isoformat(),end):
        url='https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?'+urlencode({'date':day.replace('-','/'),'response':'json'})
        collect('cash-TPEX-'+day,url,lambda raw,d=day:otc_prices(raw,d),refresh=False)
    result={'schemaVersion':1,'version':VERSION,'start':start,'end':end,'checkedAt':now,'status':'partial' if errors else 'ok',
        'documents':documents,'sources':sources,'errors':errors,'error':'; '.join(e['id']+': '+e['error'] for e in errors) or None,
        'limitations':['原始回應與內容雜湊保留；歷史回補不表示當時已取得。','目前名錄不能證明歷史上不存在其他標的；調整契約依有效日期核對，不倒套最新條款。',
            '無成交價格、同步深度、私人帳戶部位及實際借券供給不在免費公開日報範圍。']}
    h.save(output/(ROOT+'.json'),result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,default=Path('data/derivatives'))
    parser.add_argument('--start',default='2026-10-01');parser.add_argument('--end')
    args=parser.parse_args();r=run(args.data,args.start,args.end)
    print(json.dumps({'status':r['status'],'sources':len(r['sources']),'errors':len(r['errors'])}))
    if r['errors']:raise SystemExit(1)
