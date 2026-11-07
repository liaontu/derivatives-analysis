"""Free dated TWSE/TPEx institutional flows for warrant-linked ordinary stocks."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import re
try:
    from .backfill_warrants import fetch_public
    from .daily_reports import read, save
except ImportError:
    from backfill_warrants import fetch_public
    from daily_reports import read, save

URLS = {
    'TWSE':'https://www.twse.com.tw/rwd/zh/fund/T86?date={day}&selectType=ALLBUT0999&response=json',
    'TPEX':'https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?date={slash}&type=Daily&response=json'}


def integer(value):
    text = str(value).replace(',','').strip()
    if not re.fullmatch(r'-?\d+',text):raise ValueError('Missing or invalid institutional volume')
    return int(text)


def normalize(raw, market, day, members):
    if str(raw.get('stat','')).lower()!='ok' or raw.get('date')!=day.replace('-',''):
        raise ValueError('Official institutional report date/status mismatch')
    if market=='TWSE':
        fields, data = raw['fields'],raw['data']
        names = ['證券代號','證券名稱','外陸資買賣超股數(不含外資自營商)','投信買賣超股數','自營商買賣超股數',
                 '自營商買賣超股數(自行買賣)','自營商買賣超股數(避險)','三大法人買賣超股數']
        indices=[fields.index(n) for n in names]
    else:
        table=raw['tables'][0];fields,data=table['fields'],table['data']
        expected=['代號','名稱']+['買進股數','賣出股數','買賣超股數']*7+['三大法人買賣超股數合計']
        if fields!=expected or table.get('title')!='三大法人買賣明細資訊':raise ValueError('TPEx institutional schema changed')
        # Official grouped headers: foreign excluding dealer, foreign dealer,
        # foreign subtotal, trust, dealer proprietary, dealer hedge, dealer subtotal.
        indices=[0,1,4,13,22,16,19,23]
    rows=[];seen=set()
    for line in data:
        if len(line)!=len(fields):raise ValueError('Institutional column count changed')
        values=[line[i] for i in indices];code=values[0].strip()
        if code not in members:continue
        if code in seen:raise ValueError('Duplicate institutional security')
        seen.add(code)
        foreign,trust,dealer,prop,hedge,total=map(integer,values[2:])
        if prop+hedge!=dealer or foreign+trust+dealer!=total:
            raise ValueError('Institutional subtotals disagree: '+code)
        rows.append({'code':code,'name':values[1].strip(),'market':market,'date':day,
                     'foreign':foreign,'trust':trust,'dealer':dealer,'dealerOwn':prop,'dealerHedge':hedge,'total':total})
    if not rows:raise ValueError('No matched ordinary-stock institutional rows')
    return sorted(rows,key=lambda r:r['code'])


def enrich(output, rows, day):
    volume_path=output/'daily-reports/warrant-volume'/(day+'.json')
    volume=read(volume_path) if volume_path.exists() else {}
    stocks={r['code']:r for r in volume.get('stocks',[])}
    for row in rows:
        path=output/'warrant-history'/(row['code']+'.json')
        history=read(path) if path.exists() else {}
        spot=dict(history.get('underlyingPrices',[]));previous=max((d for d in spot if d<day),default=None)
        price=spot.get(day);base=spot.get(previous)
        row.update(spot=price,spotBaseDate=previous,
                   stockPct=(price/base-1)*100 if isinstance(price,(int,float)) and isinstance(base,(int,float)) and price>0 and base>0 else None)
        s=stocks.get(row['code'],{})
        row.update(volumeEligible=s.get('eligible'),callVolumeSignals=s.get('call'),putVolumeSignals=s.get('put'))
    return rows


def collect(output, cache=None, fetcher=fetch_public, now=None):
    now=now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    manifest=read(output/'manifest.json');feeds={s['id']:s for s in manifest['sources']}
    directory=read(output/'stock-warrant-directory.json')
    if directory.get('status')!='ok':raise ValueError('Current ordinary-stock directory is not verified')
    members={r['code'] for r in directory['items']}
    all_rows=[];sources=[];errors=[];dates=[]
    for market,feed in [('TWSE','twse-warrants'),('TPEX','tpex-warrants')]:
        day=feeds[feed]['date'];dates.append(day)
        url=URLS[market].format(day=day.replace('-',''),slash=day.replace('-','%2F'))
        try:
            cached=cache/('t86-' + day + '.json' if market=='TWSE' else 'tpex-inst-'+day+'.json') if cache else None
            raw=read(cached) if cached and cached.exists() else fetcher(url)
            rows=normalize(raw,market,day,members)
            digest=hashlib.sha256(json.dumps(raw,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            sources.append({'market':market,'date':day,'url':url,'sha256':digest,'status':'ok','rows':len(rows)})
            all_rows.extend(enrich(output,rows,day))
        except Exception as exc:
            errors.append(market+': '+str(exc)[:180]);sources.append({'market':market,'date':day,'url':url,'status':'unavailable','error':str(exc)[:180]})
    path=output/'stock-institutional.json';old=read(path) if path.exists() else {'dates':[]}
    if errors or len(set(dates))!=1:
        result={**old,'status':'retained' if old.get('dates') else 'unavailable','checkedAt':now,'attemptSources':sources,
                'error':'; '.join(errors) or 'Two market dates differ; no mixed-day report'}
        save(path,result);return result
    day=dates[0]
    report={'schemaVersion':1,'date':day,'status':'ok','unit':'股','sources':sources,'rows':all_rows,
            'coverage':{'matchedStocks':len(all_rows),'directoryStocks':len(members),'missingStocks':sorted(members-{r['code'] for r in all_rows})},
            'limitations':['僅普通股權證標的，按本次名錄核對；上市與上櫃交易統計範圍依原報表，跨市場不直接合併排名。',
                           '外資不含外資自營商；自營商總額已含自行買賣與避險，分項不再重複加總。單位為股。',
                           '權證放量是同日並列觀察，無法識別同一投資人或證明因果、避險與交易意圖。',
                           '歷史自串接後累積；缺資料不當零，樣本不足不計算預測或成本後交易績效。']}
    save(output/'stock-institutional'/(day+'.json'),report)
    result={'schemaVersion':1,'date':day,'latest':day,'dates':sorted(set(old.get('dates',[]))|{day})[-60:],
            'status':'ok','error':None,'sources':sources,'coverage':report['coverage']}
    save(path,result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('data/derivatives'));p.add_argument('--cache',type=Path)
    a=p.parse_args();r=collect(a.data,a.cache);print(json.dumps({k:r.get(k) for k in ('date','status','coverage','error')},ensure_ascii=False))
    if r['status']!='ok':raise SystemExit(1)
