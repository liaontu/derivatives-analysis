"""Official capital reductions, denomination changes and dated suspensions."""
import json
import re
from urllib.parse import urlencode

if __package__:
    from . import supplement_sources as s
else:
    import supplement_sources as s


def table(raw, required):
    doc=json.loads(raw)
    if str(doc.get('stat')).lower()!='ok':raise ValueError('Official action status not OK')
    candidates=[t for t in doc.get('tables',[doc]) if required<=set(t.get('fields',[]))]
    if len(candidates)!=1:raise ValueError('Action schema missing/ambiguous')
    t=candidates[0]
    if any(len(r)!=len(t['fields']) for r in t.get('data',[])):raise ValueError('Action width mismatch')
    return doc,[dict(zip(t['fields'],r)) for r in t.get('data',[])]


def detail(raw,code):
    _,rows=table(raw,{'股票代號：','停止買賣日期：','每壹仟股換發新股票：','每股退還股款：'})
    if len(rows)!=1 or rows[0]['股票代號：'].strip()!=code:raise ValueError('Reduction detail identity mismatch')
    r=rows[0]
    return [{'code':code,'market':'TWSE','stopDate':s.iso(r['停止買賣日期：']),
        'newShareRatio':s.numeric(r['每壹仟股換發新股票：'].split()[0])/1000,
        'cashReturned':s.numeric(r['每股退還股款：'].split()[0]),
        'cashDividend':s.numeric(r.get('原股每股配發現金股利：','0').split()[0])}]


def capital(raw,market,start,end,kind='reduction'):
    doc=json.loads(raw)
    if market=='TWSE':
        codes=('股票代號','名稱');prices=('停止買賣前收盤價格','恢復買賣參考價')
        params=doc.get('params',{})
        if (doc.get('strDate',params.get('startDate')),doc.get('endDate',params.get('endDate')))!=(start.replace('-',''),end.replace('-','')):
            raise ValueError('TWSE action range mismatch')
    else:
        codes=('股票代號','名稱') if kind=='reduction' else ('證券代號','證券名稱')
        prices=('最後交易日之收盤價格','減資恢復買賣開始日參考價格' if kind=='reduction' else '恢復買賣開始參考價')
        if doc.get('date')!=start.replace('-','')+'~'+end.replace('-',''):raise ValueError('TPEx action range mismatch')
    _,rows=table(raw,{'恢復買賣日期',*codes,*prices,'詳細資料'})
    result=[]
    for r in rows:
        day=s.iso(r['恢復買賣日期'])
        if not start<=day<=end:raise ValueError('Action outside requested range')
        before,ref=(s.numeric(r[k],True) for k in prices)
        if any(v is not None and v<=0 for v in (before,ref)):raise ValueError('Invalid corporate action price')
        row={'date':day,'market':market,'code':r[codes[0]].strip(),'name':r[codes[1]],'before':before,'reference':ref,
            'factor':ref/before if before and ref else None,'action':'減資：'+r.get('減資原因','') if kind=='reduction' else '變更面額',
            'kind':kind,'basis':'恢復交易日相對官方參考價；不是含息總報酬'}
        if market=='TWSE':
            m=re.fullmatch(r'([0-9A-Z]+)\s*,(\d{8})',r['詳細資料'])
            if not m or m[1]!=row['code']:raise ValueError('Bad action detail reference')
            row['detailDate']=m[2]
        else:
            p=s.Tables();p.feed(r['詳細資料']);d={k.rstrip(':：'):v for t in p.tables for k,v in t if len((k,v))==2}
            if '停止買賣日期' in d:row['stopDate']=s.iso(d['停止買賣日期'])
            for key,target,scale in [('每壹仟股換發新股票','newShareRatio',.001),('每股退還股款','cashReturned',1)]:
                if key in d:
                    m=re.match(r'^([0-9.]+)',d[key])
                    if not m:raise ValueError('Missing capital action value')
                    row[target]=s.numeric(m[1])*scale
        result.append(row)
    return result


def suspension(raw,market,checked):
    if market=='TWSE':
        doc,rows=table(raw,{'證券代號','證券名稱','停止買賣原因','停止買賣開始日期'})
        title=next(t['title'] for t in doc['tables'] if '停止買賣' in t.get('title',''))
        asof=s.iso(title.split()[0])
        if asof>checked:raise ValueError('Future suspension table')
        return [{'market':market,'code':r['證券代號'].strip(),'name':r['證券名稱'],'stopDate':s.iso(r['停止買賣開始日期']),
                 'asOf':asof,'reason':r['停止買賣原因'],'kind':'official-suspension'} for r in rows]
    rows=s.records(raw,{'Date','SecuritiesCompanyCode','CompanyName','SuspensionOfTrading'})
    dates={s.iso(r['Date']) for r in rows}
    if len(dates)!=1 or max(dates)>checked:raise ValueError('TPEx mode date invalid')
    return [{'market':market,'code':r['SecuritiesCompanyCode'].strip(),'name':r['CompanyName'],'asOf':s.iso(r['Date']),
             'reason':'官方當日交易方式：停止交易（未提供歷史起日）','kind':'official-suspension'}
            for r in rows if r['SuspensionOfTrading'].strip() in ('Ｙ','Y')]


def notices(raw):
    doc,rows=table(raw,{'代號','名稱','停止買賣日期','恢復買賣日期','減資換股率','每股退還股款(元)'})
    asof=s.iso(doc['date'])
    return [{'market':'TPEX','code':r['代號'].strip(),'name':r['名稱'],'stopDate':s.iso(r['停止買賣日期']),
        'date':s.iso(r['恢復買賣日期']),'asOf':asof,'action':'減資：'+r['減資原因'],'kind':'reduction',
        'newShareRatio':s.numeric(r['減資換股率']),'cashReturned':s.numeric(r['每股退還股款(元)']),
        'reference':s.numeric(r['恢復買賣參考價'],True) if not str(r['恢復買賣參考價']).startswith('param') else None,
        'before':None,'factor':None,'basis':'官方預告；未生效不當作當日報酬'} for r in rows]


def collect_all(collect,documents,output,start,end,checked):
    from datetime import date
    import calendar
    # Include announced later resumptions to explain suspensions before end.
    last=date.fromisoformat(end);last=last.replace(day=calendar.monthrange(last.year,last.month)[1]).isoformat()
    for market,url in [('TWSE','https://www.twse.com.tw/rwd/zh/violation/stop?response=json'),('TPEX','https://www.tpex.org.tw/openapi/v1/tpex_cmode')]:
        collect('suspension-'+market,url,lambda raw,m=market:suspension(raw,m,checked))
    collect('capital-notice-TPEX','https://www.tpex.org.tw/www/zh-tw/bulletin/decap?response=json',notices)
    for market,route in [('TWSE','https://www.twse.com.tw/rwd/zh/reducation/TWTAUU'),('TPEX','https://www.tpex.org.tw/www/zh-tw/bulletin/revivt')]:
        for first,stop in s.month_ranges(start,last):
            a=first.replace('-','' if market=='TWSE' else '/');b=stop.replace('-','' if market=='TWSE' else '/')
            collect('capital-'+market+'-'+first[:7],route+'?'+urlencode({'startDate':a,'endDate':b,'response':'json'}),
                    lambda raw,m=market,a=first,b=stop:capital(raw,m,a,b),refresh=stop>=end)
    for market,route in [('TWSE','https://www.twse.com.tw/rwd/zh/change/TWTB8U'),('TPEX','https://www.tpex.org.tw/www/zh-tw/bulletin/pvChgRslt')]:
        a=start.replace('-','' if market=='TWSE' else '/');b=end.replace('-','' if market=='TWSE' else '/')
        collect('denomination-'+market+'-'+end,route+'?'+urlencode({'startDate':a,'endDate':b,'response':'json'}),
                lambda raw,m=market:capital(raw,m,start,end,'denomination'),refresh=False)
    for key,entry in list(documents.items()):
        if not key.startswith('capital-TWSE-'):continue
        for r in s.h.read(output/entry['path'],{}).get('rows',[]):
            code=r['code'];fileday=r['detailDate']
            collect('capital-detail-'+code+'-'+fileday,'https://www.twse.com.tw/rwd/zh/reducation/TWTAVUDetail?'+urlencode({'STK_NO':code,'FILE_DATE':fileday,'response':'json'}),
                    lambda raw,c=code:detail(raw,c),refresh=False)


def state_for(rows,market,code,day):
    for r in rows:
        if (r.get('market'),r.get('code'))!=(market,code):continue
        if r.get('stopDate') and r['stopDate']<=day<(r.get('date') or '9999-12-31') and day<=r.get('asOf','9999-12-31'):return r
        if not r.get('stopDate') and r.get('asOf')==day:return r
    return None
