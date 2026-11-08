"""Repair previously omitted listed bull/bear volume with dated official rows."""
import argparse,hashlib,json,re
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode
if __package__:
    from . import collect as c,market_history as h
    from .warrant_history import trade_price
else:
    import collect as c,market_history as h
    from warrant_history import trade_price


def official_rows(raw,day,include_all=False):
    doc=json.loads(raw)
    if doc.get('date')!=day.replace('-','') or doc.get('stat')!='OK':raise ValueError('Extra volume date/status mismatch')
    tables=[t for t in doc.get('tables',[]) if {'證券代號','證券名稱','成交股數','成交金額','收盤價'}<=set(t.get('fields',[]))]
    if len(tables)!=1:raise ValueError('Extra volume table missing/ambiguous')
    result={};t=tables[0]
    for values in t['data']:
        if len(values)!=len(t['fields']):raise ValueError('Extra volume row width mismatch')
        r=dict(zip(t['fields'],values));code=r['證券代號']
        if not re.fullmatch(r'\d{5}[0-9A-Z]' if include_all else r'\d{5}[BCXY]',code):continue
        volume=c.number(r['成交股數'])
        if volume is None or volume<0 or code in result:raise ValueError('Extra volume invalid/duplicated')
        result[code]={'date':day,'code':code,'name':r['證券名稱'],'volume':volume/1000,'value':c.number(r['成交金額']),
            'price':trade_price(c.number(r['收盤價']),volume)}
    return result


def run(output,start='2026-10-01',client=None,max_days=None):
    client=client or h.Client(output);now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    index=h.read(output/'warrant-extra-history.json',{'days':{}});index.setdefault('days',{})
    files={};needed=defaultdict(list)
    for path in sorted((output/'warrant-history').glob('*.json')):
        doc=h.read(path);changed=False
        for code,q in doc['quotes'].items():
            if q['market']!='TWSE':continue
            for row in q['rows']:
                if row[0]>=start and (row[2] is None or (row[2]>0 and row[1] is None)):needed[row[0]].append((path,code,row))
        files[path]=doc
    errors=[];fixed=0;changed=set()
    if max_days is None:
        end=h.read(output/'twse-warrants.json')['date']
        for day in h.expected_days(output,'futures',start,end):
            if day not in index['days']:needed.setdefault(day,[])
    selected=sorted(needed,reverse=True)
    if max_days is not None:selected=selected[:max_days]
    for day in selected:
        url='https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?'+urlencode({'date':day.replace('-',''),'type':'ALL','response':'json'})
        evidence={}
        try:
            raw,evidence=client.get(url);rows=official_rows(raw,day,True);client.accept(evidence);count=0;unmatched=[]
            for path,code,target in needed[day]:
                r=rows.get(code);q=files[path]['quotes'][code]
                if not r or r['name']!=q['name']:unmatched.append(code);continue
                if target[2] is not None and target[2]!=r['volume']:
                    unmatched.append(code);continue
                replacement=[r['price'],r['volume']]
                if target[1:]!=replacement:target[1:]=replacement;changed.add(path);count+=1
            previous=index['days'].get(day,{})
            index['days'][day]={'date':day,'source':evidence,'fixed':previous.get('fixed',0)+count,'unmatched':unmatched,'status':'ok'};fixed+=count
            print(json.dumps({'date':day,'fixed':count,'unmatched':len(unmatched)}),flush=True)
        except Exception as exc:
            errors.append({'date':day,'error':str(exc),'source':evidence})
            if getattr(exc,'code',None) in (401,403,429):break
    for path in changed:c.save(path,files[path])
    index.update(version='warrant-missing-quotes-v2',checkedAt=now,status='partial' if errors else 'ok',errors=errors,
        remainingDays=[day for day in sorted(needed) if day not in selected],
        note='僅補官方同日、同代號、同名稱的上市權證缺價量；未改寫原有有效數值或假造無列成交量。原始回應可重算，特殊結構不加入一般認購售套利模型。')
    h.save(output/'warrant-extra-history.json',index)
    return index


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('data/derivatives'));p.add_argument('--start',default='2026-10-01');p.add_argument('--max-days',type=int)
    args=p.parse_args();r=run(args.data,args.start,max_days=args.max_days)
    if r['errors']:raise SystemExit(1)
