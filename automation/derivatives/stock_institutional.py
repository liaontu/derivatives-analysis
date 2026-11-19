"""Free dated TWSE/TPEx institutional flows for warrant-linked ordinary stocks."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import argparse
import gzip
import hashlib
import json
import re
try:
    from .backfill_warrants import fetch_public
    from .daily_reports import read, save
    from .trading_calendar import load_calendar
except ImportError:
    from backfill_warrants import fetch_public
    from daily_reports import read, save
    from trading_calendar import load_calendar

URLS = {
    'TWSE':'https://www.twse.com.tw/rwd/zh/fund/T86?date={day}&selectType=ALLBUT0999&response=json',
    'TPEX':'https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?date={slash}&type=Daily&response=json'}


def verified_membership(output, now):
    """Membership does not require the optional, large warrant issuance file."""
    try:
        from . import collect as quotes, supplement_sources as supplement
    except ImportError:
        import collect as quotes, supplement_sources as supplement
    index=read(output/'market-supplement.json')
    current=datetime.fromisoformat(now).astimezone(timezone.utc)
    today=current.astimezone(timezone(timedelta(hours=8))).date()
    urls={'TWSE':'https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
          'TPEX':'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O'}
    members={};evidence=[]
    def checked_file(entry,compressed=False):
        path=(output/entry['path']).resolve()
        if not path.is_relative_to(output.resolve()):raise ValueError('Unsafe membership evidence path')
        raw=path.read_bytes();raw=gzip.decompress(raw) if compressed else raw
        if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('Membership evidence hash mismatch')
        return raw if compressed else json.loads(raw)
    for market,url in urls.items():
        key='companies-'+market;entry=index.get('documents',{}).get(key,{})
        if entry.get('status')!='ok':raise ValueError('Verified company roster missing: '+market)
        doc=checked_file(entry);proof=entry['source']
        if doc.get('id')!=key or doc.get('evidence')!=proof or proof.get('url')!=url or proof.get('httpStatus')!=200 or proof.get('validated') is not True:
            raise ValueError('Company roster provenance mismatch')
        age=(current-datetime.fromisoformat(proof['fetchedAt']).astimezone(timezone.utc)).total_seconds()
        if not 0<=age<=86400:raise ValueError('Company roster check must be within 24 hours')
        raw=checked_file(proof,True)
        dates={quotes.iso_date(r.get('出表日期' if market=='TWSE' else 'Date')) for r in json.loads(raw)}
        if len(dates)!=1 or None in dates:raise ValueError('Company roster date missing or ambiguous')
        reported=dates.pop()
        if not 0<=(today-date.fromisoformat(reported)).days<=14:raise ValueError('Company roster date stale or future')
        rows=supplement.companies(raw,market)
        if rows!=doc.get('rows'):raise ValueError('Company roster does not match its raw evidence')
        for row in rows:
            if row['code'] in members:raise ValueError('Ambiguous cross-market company identity')
            members[row['code']]=row
        evidence.append({'market':market,'reportedDate':reported,'normalized':entry,'raw':proof})
    feeds={r['id']:r for r in read(output/'manifest.json')['sources']}
    required=[feeds[k] for k in ('twse-warrants','tpex-warrants')]
    if any(r.get('status')!='ok' for r in required) or len({r['date'] for r in required})!=1:
        raise ValueError('Current warrant quote dates are not verified and aligned')
    quote_day=required[0]['date']
    if not load_calendar(output).fresh(quote_day,today):raise ValueError('Warrant quote membership is stale')
    catalog=quotes.read(output/'warrants.json')['rows']
    linked={r.get('underlying') for r in catalog if r.get('date')==quote_day
            and r.get('market') in urls and r.get('cp') in ('C','P') and re.fullmatch(r'\d{5}[0-9A-Z]',r.get('code',''))}
    items=[members[code] for code in sorted(linked & members.keys())]
    if not items:raise ValueError('No verified ordinary-stock warrant underlyings')
    return {'status':'ok','checkedDay':today.isoformat(),'items':items,
            'membershipBasis':'verified-company-rosters-and-current-warrant-quotes',
            'membershipSources':evidence,'membershipQuoteDate':quote_day}


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
    stocks={r['code']:r for r in volume.get('stocks',[])} if volume.get('date')==day else {}
    calendar=load_calendar(output);previous=date.fromisoformat(day)-timedelta(days=1)
    while not calendar.is_open(previous):previous-=timedelta(days=1)
    previous=previous.isoformat()
    for row in rows:
        path=output/'warrant-history'/(row['code']+'.json')
        history=read(path) if path.exists() else {}
        spot=dict(history.get('underlyingPrices',[]))
        price=spot.get(day);base=spot.get(previous)
        row.update(spot=price,spotBaseDate=previous,
                   stockPct=(price/base-1)*100 if isinstance(price,(int,float)) and isinstance(base,(int,float)) and price>0 and base>0 else None)
        s=stocks.get(row['code'],{})
        row.update(volumeEligible=s.get('eligible'),callVolumeSignals=s.get('call'),putVolumeSignals=s.get('put'))
    return rows


def snapshot(output, day, directory, cache=None, fetcher=fetch_public, now=None, mode='daily'):
    now=now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    if directory.get('status')!='ok':directory=verified_membership(output,now)
    members={r['code'] for r in directory['items']}
    all_rows=[];sources=[];errors=[];blocked=False
    old_path=output/'stock-institutional'/(day+'.json')
    old=read(old_path) if old_path.exists() else {}
    old_sources={s['market']:s for s in old.get('sources',[])}
    for market in ('TWSE','TPEX'):
        url=URLS[market].format(day=day.replace('-',''),slash=day.replace('-','%2F'))
        try:
            cached=cache/(market+'-'+day+'.json') if cache else None
            if cached and not cached.exists():
                legacy=cache/('t86-' + day + '.json' if market=='TWSE' else 'tpex-inst-'+day+'.json')
                if legacy.exists():cached=legacy
            raw=read(cached) if cached and cached.exists() else fetcher(url)
            rows=normalize(raw,market,day,members)
            encoded=json.dumps(raw,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
            digest=hashlib.sha256(encoded).hexdigest()
            archive=output/'stock-institutional-sources'/day[:7]/(market+'-'+day+'-'+digest+'.json.gz')
            archive.parent.mkdir(parents=True,exist_ok=True)
            if archive.exists():
                if gzip.decompress(archive.read_bytes())!=encoded:raise ValueError('Source archive integrity mismatch')
            else:archive.write_bytes(gzip.compress(encoded,mtime=0))
            previous=old_sources.get(market,{})
            sources.append({'id':market,'market':market,'date':day,'url':url,'sha256':digest,'status':'ok','rows':len(rows),
                            'path':archive.relative_to(output).as_posix(),'retrievedAt':previous.get('retrievedAt',now) if previous.get('sha256')==digest else now})
            all_rows.extend(enrich(output,rows,day))
        except Exception as exc:
            errors.append(market+': '+str(exc)[:180]);sources.append({'id':market,'market':market,'date':day,'url':url,'status':'unavailable','error':str(exc)[:180]})
            blocked=getattr(exc,'code',None) in (401,403,429)
            if blocked:break
    if errors:return {'date':day,'status':'unavailable','sources':sources,'error':'; '.join(errors),'blocked':blocked}
    report={'schemaVersion':2,'date':day,'status':'ok','unit':'股','sources':sources,'rows':all_rows,
            'firstRecordedAt':old.get('firstRecordedAt') or now,'acquisitionMode':old.get('acquisitionMode') or mode,
            'membership':{'date':directory.get('checkedDay'),'basis':directory.get('membershipBasis','current-warrant-underlying-directory'),
                          'sha256':hashlib.sha256(json.dumps(sorted(members)).encode()).hexdigest(),
                          **({'sources':directory['membershipSources'],'quoteDate':directory['membershipQuoteDate']}
                             if directory.get('membershipSources') else {})},
            'coverage':{'matchedStocks':len(all_rows),'directoryStocks':len(members),'missingStocks':sorted(members-{r['code'] for r in all_rows})},
            'limitations':['僅普通股權證標的，按本次名錄核對；上市與上櫃交易統計範圍依原報表，跨市場不直接合併排名。',
                           '外資不含外資自營商；自營商總額已含自行買賣與避險，分項不再重複加總。單位為股。',
                           '權證放量是同日並列觀察，無法識別同一投資人或證明因果、避險與交易意圖。',
                           '歷史補抓採目前權證標的名錄，已退出名錄者可能未涵蓋，具有選樣偏差；保存時間不等於當時可得時間。',
                           '現股漲跌要求前一交易日價格，不跨缺日補值；價格未還原，缺資料不當零。']}
    return report


def store_snapshot(output, report):
    if report.get('status')!='ok':raise ValueError('Cannot publish unavailable institutional snapshot')
    day=report['date'];path=output/'stock-institutional.json'
    old=read(path) if path.exists() else {'dates':[]}
    previous=read(output/'stock-institutional'/(day+'.json')) if day in old['dates'] else {}
    if previous and previous!=report:
        digest=hashlib.sha256(json.dumps(previous,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        archive=output/'stock-institutional/revisions'/(day+'-'+digest+'.json')
        if archive.exists() and read(archive)!=previous:raise ValueError('Institutional revision was modified')
        save(archive,previous)
    save(output/'stock-institutional'/(day+'.json'),report)
    dates=sorted(set(old.get('dates',[]))|{day});latest=dates[-1]
    current=report if day==latest else read(output/'stock-institutional'/(latest+'.json'))
    result={'schemaVersion':2,'date':latest,'latest':latest,'dates':dates,
            'status':'ok','error':None,'sources':current['sources'],'coverage':current['coverage']}
    if day<latest:
        for key in ('status','error','checkedAt','attemptSources'):
            if key in old:result[key]=old[key]
    save(path,result);return result


def collect(output, cache=None, fetcher=fetch_public, now=None):
    now=now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    feeds={s['id']:s for s in read(output/'manifest.json')['sources']}
    dates={feeds[k]['date'] for k in ('twse-warrants','tpex-warrants')}
    try:
        report=snapshot(output,next(iter(dates)),read(output/'stock-warrant-directory.json'),cache,fetcher,now) if len(dates)==1 else {'status':'unavailable','error':'Two market dates differ; no mixed-day report','sources':[]}
    except (ValueError,KeyError,OSError) as exc:
        report={'status':'unavailable','error':str(exc)[:200],'sources':[]}
    if report['status']!='ok':
        path=output/'stock-institutional.json';old=read(path) if path.exists() else {'dates':[]}
        result={**old,'status':'retained' if old.get('dates') else 'unavailable','checkedAt':now,
                'attemptSources':report['sources'],'error':report['error']}
        save(path,result);return result
    return store_snapshot(output,report)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('data/derivatives'));p.add_argument('--cache',type=Path)
    a=p.parse_args();r=collect(a.data,a.cache);print(json.dumps({k:r.get(k) for k in ('date','status','coverage','error')},ensure_ascii=False))
    if r['status']!='ok':raise SystemExit(1)
