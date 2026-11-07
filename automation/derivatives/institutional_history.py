"""Bounded, cached official institutional history; no backdated forward signals."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import argparse,gzip,hashlib,json,time
try:
    from . import stock_institutional as inst
    from .daily_reports import read,save
    from .trading_calendar import load_calendar
except ImportError:
    import stock_institutional as inst
    from daily_reports import read,save
    from trading_calendar import load_calendar


def sessions(calendar,start,end):
    cursor=date.fromisoformat(start);last=date.fromisoformat(end);result=[]
    while cursor<=last:
        if calendar.is_open(cursor):result.append(cursor.isoformat())
        cursor+=timedelta(days=1)
    return result


def requested_dates(calendar,start,end,baseline=5):
    result=sessions(calendar,start,end);before=[];cursor=date.fromisoformat(start)
    while len(before)<baseline:
        cursor-=timedelta(days=1)
        if calendar.is_open(cursor):before.append(cursor.isoformat())
    return sorted(before)+result


def verified_snapshot(output,stored):
    if stored.get('status')!='ok' or stored.get('schemaVersion',0)<2 or {s.get('market') for s in stored.get('sources',[])}!={'TWSE','TPEX'}:return False
    for source in stored['sources']:
        try:
            path=(output/source['path']).resolve()
            if not path.is_relative_to((output/'stock-institutional-sources').resolve()):return False
            if hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest()!=source['sha256']:return False
        except (OSError,KeyError,ValueError,EOFError):return False
    return True


def run(output,start=None,end=None,cache=None,max_days=2,fetcher=inst.fetch_public,sleeper=time.sleep,now=None):
    now=now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    feeds={s['id']:s for s in read(output/'manifest.json')['sources']}
    end=end or min(feeds[k]['date'] for k in ('twse-warrants','tpex-warrants'))
    if not start:
        first=date.fromisoformat(end).replace(day=1)-timedelta(days=1)
        start=first.replace(day=1).isoformat()
    if start>end:raise ValueError('Historical date range is reversed')
    calendar=load_calendar(output);dates=requested_dates(calendar,start,end)
    if len(dates)>65:raise ValueError('At most 65 trading sessions per historical request')
    path=output/'stock-institutional-backfill.json';old=read(path) if path.exists() else {}
    prior={r['date']:r for r in old.get('days',[])};rows=[];attempted=0;blocked=False
    directory=read(output/'stock-warrant-directory.json')
    def limited(url):sleeper(2);return fetcher(url)
    for day in dates:
        file=output/'stock-institutional'/(day+'.json')
        stored=read(file) if file.exists() else {}
        complete=verified_snapshot(output,stored)
        item={'date':day,'role':'baseline' if day<start else 'analysis'}
        previous=prior.get(day,{})
        cooling=previous.get('status')=='unavailable' and previous.get('attemptedAt','')[:10]==now[:10]
        if not complete and attempted<max_days and not blocked and not cooling:
            attempted+=1
            report=inst.snapshot(output,day,directory,cache,limited,now,mode='historical-backfill')
            if report['status']=='ok':
                inst.store_snapshot(output,report);stored=report;complete=True
            else:
                item.update(status='unavailable',error=report['error'],sources=report['sources'],attemptedAt=now)
                blocked=report.get('blocked',False)
        if complete:
            item.update(status='ok',rows=len(stored['rows']),coverage=stored['coverage'],sources=stored['sources'])
        elif 'status' not in item:
            item.update(status=previous.get('status','pending'),error=previous.get('error'),attemptedAt=previous.get('attemptedAt'))
        rows.append(item)
        if attempted and not complete:print(json.dumps({'date':day,'status':item['status'],'error':item.get('error')},ensure_ascii=False),flush=True)
        elif complete:print(json.dumps({'date':day,'status':'ok','rows':item['rows']},ensure_ascii=False),flush=True)
    missing=[r['date'] for r in rows if r['status']!='ok']
    result={'schemaVersion':1,'status':'ok' if not missing else 'partial','range':{'start':start,'end':end},
            'baselineDays':5,'expectedDays':len(rows),'completeDays':len(rows)-len(missing),'missingDates':missing,'days':rows,
            'basis':'官方歷史日報事後補抓；目前權證標的名錄篩選，有存續者偏差。基準日不列入分析期間。',
            'dailyRepair':'每日主排程最多補查兩個缺日，來源錯誤同日不重試；401/403/429 停止本輪。'}
    save(path,result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=Path('data/derivatives'));p.add_argument('--cache',type=Path)
    p.add_argument('--start');p.add_argument('--end');p.add_argument('--max-days',type=int,default=2)
    a=p.parse_args();r=run(a.data,a.start,a.end,a.cache,max(0,min(a.max_days,65)))
    print(json.dumps({k:r[k] for k in ('status','range','expectedDays','completeDays','missingDates')},ensure_ascii=False))
    if r['status']!='ok':raise SystemExit(1)
