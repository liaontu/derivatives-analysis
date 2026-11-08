"""Rebuild dated warrant volume reports from free dated quotes, with a coverage ledger."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlencode

if __package__:
    from . import collect as c
    from .backfill_warrants import CachedSource, fetch_public, month_start
    from .daily_reports import archive, baseline_days, build_volume_report, read, update_coverage
    from .trading_calendar import load_calendar
    from .warrant_directory import MASTER
    from .warrant_history import trade_price
else:
    import collect as c
    from backfill_warrants import CachedSource, fetch_public, month_start
    from daily_reports import archive, baseline_days, build_volume_report, read, update_coverage
    from trading_calendar import load_calendar
    from warrant_directory import MASTER
    from warrant_history import trade_price

LISTED = ('twse-warrants','twse-volume','twse-volume-put')
OTC_URL = 'https://www.tpex.org.tw/www/zh-tw/warrant/wntQuts?'


def write_cache(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(data,ensure_ascii=False,allow_nan=False).encode(),mtime=0))


def read_cache(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def digest(data):
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def dated_all_quotes(output,day):
    """Price/volume baseline only. Never infer old warrant terms from later terms."""
    index=c.read(output/'warrant-extra-history.json') or {}
    evidence=index.get('days',{}).get(day,{}).get('source')
    if not evidence:return None,None
    raw=gzip.decompress((output/evidence['path']).read_bytes())
    if hashlib.sha256(raw).hexdigest()!=evidence['sha256']:raise ValueError('Baseline source hash mismatch')
    doc=json.loads(raw)
    if doc.get('stat')!='OK' or c.iso_date(doc.get('date'))!=day:raise ValueError('Baseline official date/status mismatch')
    tables=[t for t in doc.get('tables',[]) if {'證券代號','證券名稱','成交股數','收盤價'}<=set(t.get('fields',[]))]
    if len(tables)!=1:raise ValueError('Baseline quote schema missing')
    rows={};table=tables[0]
    for values in table['data']:
        if len(values)!=len(table['fields']):raise ValueError('Baseline quote width mismatch')
        r=dict(zip(table['fields'],values));volume=c.number(r['成交股數']);code=r['證券代號']
        if volume is None or volume<0 or code in rows:raise ValueError('Baseline volume invalid')
        rows[code]={'name':r['證券名稱'],'volume':volume/1000,'price':trade_price(c.number(r['收盤價']),volume)}
    return rows,{**evidence,'id':'twse-all-price-volume-baseline','date':day,'basis':'同日同代號同名稱價量，未回填歷史履約條款'}


def seed_quote_baseline(row,baselines,bank,spots):
    if row['market']!='TWSE':return
    identity=tuple(row.get(k) for k in ('market','code','name','underlying','cp'))
    for day,quotes in baselines.items():
        if day>=row['date']:continue
        q=quotes.get(row['code'])
        if not q or q['name']!=row['name']:continue
        bank.setdefault(identity,{}).setdefault(day,[day,q['price'],q['volume']])
        spot=quotes.get(row['underlying'])
        if spot:spots[row['underlying']].setdefault(day,spot['price'])


def seed_git(repo, cache, first, last):
    """Reuse normalized official snapshots. Keep the newest revision for each date."""
    commits = subprocess.check_output(['git','-C',str(repo),'log','--max-count=70','--format=%H','--',
                                      'data/derivatives/manifest.json'],text=True).split()
    for rev in commits:
        manifest = json.loads(subprocess.check_output(['git','-C',str(repo),'show',rev+':data/derivatives/manifest.json']))
        for source in manifest['sources']:
            name, day = source['id'], source.get('date')
            if name not in LISTED or source.get('status') != 'ok' or not day or not first <= day <= last:
                continue
            path = cache / (name+'-'+day+'.json.gz')
            if path.exists():
                continue
            payload = json.loads(subprocess.check_output(['git','-C',str(repo),'show',rev+':data/derivatives/'+name+'.json']))
            rows = payload['rows']
            if payload['date'] != day or not rows or any(r['date'] != day for r in rows):
                continue
            audit = {k:source.get(k) for k in ('id','date','url','fetchedAt')}
            audit.update(sha256=digest(rows), hashBasis='normalized-source-rows', archiveCommit=rev)
            write_cache(path, {'rows':rows,'audit':audit})


def membership(cache):
    path = cache / 'companies.json.gz'
    if path.exists():
        return read_cache(path)
    names, dates, sources = {}, {}, []
    fields = [('listedCompanies','公司代號','公司簡稱','出表日期'),
              ('otcCompanies','SecuritiesCompanyCode','CompanyAbbreviation','Date')]
    for key, code_key, name_key, date_key in fields:
        rows = c.fetch(MASTER[key])
        days = {c.iso_date(r[date_key]) for r in rows}
        if len(days) != 1 or None in days:
            raise ValueError('Company directory dates differ')
        dates[key] = days.pop()
        sources.append({'id':key,'date':dates[key],'url':MASTER[key],'sha256':digest(rows)})
        for row in rows:
            code = str(row[code_key]).strip()
            if re.fullmatch(r'\d{4}',code):
                names[code] = {'code':code,'name':row[name_key]}
    result = {'names':names,'dates':dates,'sources':sources}
    write_cache(path,result)
    return result


def otc_quotes(raw, day):
    if raw.get('date') != day.strftime('%Y%m%d') or str(raw.get('stat')).lower() != 'ok':
        raise ValueError('OTC report date/status mismatch')
    expected = ['代號','名稱','開市價','最高價','最低價','收市價','漲跌','成交量(股)',
                '筆數','成交金額','標的代號','標的名稱','標的/指數收盤','標的/指數漲跌']
    tables = [t for t in raw.get('tables',[]) if t.get('fields') == expected]
    if len(tables) != 1 or not tables[0]['data']:
        raise ValueError('OTC daily price columns unavailable')
    rows = []
    for values in tables[0]['data']:
        r = dict(zip(expected,values))
        code = r['代號']
        cp = 'C' if re.fullmatch(r'\d{6}',code) else 'P' if re.fullmatch(r'\d{5}[PUT]',code) else None
        shares = c.number(r['成交量(股)'])
        rows.append({'date':day.isoformat(),'code':code,'name':r['名稱'],'market':'TPEX','cp':cp,
                     'price':c.number(r['收市價']),'volume':shares/1000 if shares is not None else None,
                     'underlying':r['標的代號'],'underlyingName':r['標的名稱'],
                     'underlyingPrice':c.number(r['標的/指數收盤'])})
    if len({r['code'] for r in rows}) != len(rows):
        raise ValueError('OTC warrant codes duplicated')
    return rows


class Sources:
    def __init__(self,cache,old_cache,extra_output=None):
        self.cache = cache
        self.old = CachedSource(old_cache)
        self.denied = set()
        self.extra_output=extra_output

    def listed(self,day):
        feeds, audits = {}, []
        for name in LISTED:
            path = self.cache / (name+'-'+str(day)+'.json.gz')
            if path.exists():
                data = read_cache(path)
                rows, audit = data['rows'], data['audit']
            else:
                rows, old_audit = self.old.get(name,day)
                if rows is None:
                    raise ValueError('TWSE dated report unavailable: '+name)
                audit = {**old_audit,'id':name,'date':str(day),'fetchedAt':old_audit['retrievedAt']}
                write_cache(path,{'rows':rows,'audit':audit})
            if not rows or any(r['date'] != str(day) for r in rows):
                raise ValueError('TWSE cached source date mismatch')
            feeds[name] = {'date':str(day),'status':'ok','rows':rows}
            audits.append(audit)
        if self.extra_output:
            index=c.read(self.extra_output/'warrant-extra-history.json') or {}
            extra=index.get('days',{}).get(str(day),{}).get('source')
            if extra:
                raw=gzip.decompress((self.extra_output/extra['path']).read_bytes())
                if hashlib.sha256(raw).hexdigest()!=extra['sha256']:raise ValueError('Extra volume raw hash mismatch')
                doc=json.loads(raw)
                if c.iso_date(doc.get('date'))!=str(day):raise ValueError('Extra volume date mismatch')
                feeds['twse-volume-extra']={'date':str(day),'status':'ok','rows':c.normalize('twse-volume-extra',doc)}
                audits.append({**extra,'id':'twse-volume-extra','date':str(day)})
        return c.combine_warrants(feeds),audits

    def otc(self,day):
        path = self.cache / ('tpex-quotes-'+str(day)+'.json.gz')
        url = OTC_URL + urlencode({'date':day.strftime('%Y/%m/%d'),'type':'Daily','response':'json'})
        if path.exists():
            data=read_cache(path)
            if data['url'] != url:
                raise ValueError('OTC cached URL mismatch')
        else:
            time.sleep(2)
            data={'url':url,'raw':fetch_public(url),'retrievedAt':datetime.now(timezone.utc).isoformat(timespec='seconds')}
            otc_quotes(data['raw'],day)
            write_cache(path,data)
        return otc_quotes(data['raw'],day),[{'id':'tpex-history-quotes','date':str(day),'url':url,
                'fetchedAt':data['retrievedAt'],'sha256':digest(data['raw']),'hashBasis':'raw-official-report'}]

    def get(self,market,day):
        if market in self.denied:
            raise ValueError('Source access denied or rate limited earlier in this run')
        try:
            return self.listed(day) if market=='TWSE' else self.otc(day)
        except HTTPError as exc:
            if exc.code in (401,403,429):
                self.denied.add(market)
            raise


def reconstruct(output,cache,old_cache,repo,start,end):
    calendar = load_calendar(output)
    first = date.fromisoformat(baseline_days(start.isoformat(),calendar)[0])
    seed_git(repo,cache,str(first),str(end))
    members = membership(cache)
    source = Sources(cache,old_cache,output)
    bank, spots = {}, defaultdict(dict)
    market_days = defaultdict(set)
    baselines={};baseline_sources=[]
    for day in baseline_days(start.isoformat(),calendar):
        rows,evidence=dated_all_quotes(output,day)
        if rows is not None:
            baselines[day]=rows;baseline_sources.append(evidence);market_days['TWSE'].add(day)
    coverage = {}
    cursor = first
    while cursor <= end:
        day=str(cursor)
        if not calendar.is_open(cursor):
            cursor+=timedelta(days=1)
            continue
        rows, audits, failures, available = [], [], [], []
        for market in ('TWSE','TPEX'):
            try:
                if market=='TWSE' and cursor<start and day in baselines:
                    # Only a volume/price baseline is needed before the report period.
                    # Older warrantStock terms queries are officially unavailable.
                    available.append(market);continue
                quotes, references = source.get(market,cursor)
                available.append(market)
                market_days[market].add(day)
                rows.extend(quotes)
                audits.extend(references)
            except Exception as exc:
                failures.append({'market':market,'reason':type(exc).__name__+': '+str(exc)[:160]})
        rows=[r for r in rows if r.get('underlying') in members['names']]
        current_spots=defaultdict(set)
        for row in rows:
            seed_quote_baseline(row,baselines,bank,spots)
            row['price']=trade_price(row.get('price'),row.get('volume'))
            identity=tuple(row.get(k) for k in ('market','code','name','underlying','cp'))
            bank.setdefault(identity,{})[day]=[day,row['price'],row.get('volume')]
            current_spots[row['underlying']].add(row.get('underlyingPrice'))
        for code,values in current_spots.items():
            spots[code][day]=next(iter(values)) if len(values)==1 else None
        if cursor >= start:
            baseline=baseline_days(day,calendar)
            missing_base={market:[d for d in baseline if d not in market_days[market]] for market in available}
            notes=['歷史個股分類採 '+ '、'.join(sorted(set(members['dates'].values())))+' 官方公司名錄回查，先前已退出名錄的標的涵蓋未驗證。']
            for market, missing in missing_base.items():
                if missing:
                    notes.append(market+' 前五交易日缺資料 '+ '、'.join(missing))
            status='missing' if not rows else 'partial' if failures or any(missing_base.values()) else 'available'
            meta={'status':status,'markets':available,'membership':{'dates':members['dates'],'basis':'later-company-directory'},
                  'missingSources':failures,'notes':notes}
            coverage[day]=meta
            previous_path=output/'daily-reports/warrant-volume'/(day+'.json')
            if status != 'available' and previous_path.exists():
                previous=read(previous_path)
                old=previous.get('coverage',{'status':'available','markets':['TWSE','TPEX']})
                if old['status']=='available':
                    stocks=previous['stocks']
                    coverage[day]={**old,'total':sum(s['total'] for s in stocks),
                                   'eligible':sum(s['eligible'] for s in stocks),'surges':sum(s['call']+s['put'] for s in stocks),
                                   'notes':old.get('notes',[])+['此次補抓未齊備，保留先前已核對版本。']}
                    print(day,'retained verified report',flush=True)
                    cursor+=timedelta(days=1)
                    continue
            if rows:
                names={r['underlying']:{'name':r.get('underlyingName') or members['names'][r['underlying']]['name']} for r in rows}
                histories={code:{'quotes':{},'underlyingPrices':sorted(spots[code].items())} for code in names}
                for r in rows:
                    identity=tuple(r.get(k) for k in ('market','code','name','underlying','cp'))
                    histories[r['underlying']]['quotes'][r['code']]={**{k:r.get(k) for k in ('name','market','cp')},
                                                                   'rows':list(bank[identity].values())}
                report=build_volume_report(day,rows,histories,names,audits+members['sources']+baseline_sources,baseline)
                report['coverage']=meta
                report['reconstructed']=True
                archive(output,'warrant-volume',report)
                coverage[day].update(total=sum(s['total'] for s in report['stocks']),eligible=sum(s['eligible'] for s in report['stocks']),
                                     surges=sum(s['call']+s['put'] for s in report['stocks']))
            print(day,status,coverage[day].get('eligible'),flush=True)
        cursor+=timedelta(days=1)
    update_coverage(output,coverage,start,end)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--data',type=Path,required=True)
    p.add_argument('--cache',type=Path,required=True)
    p.add_argument('--old-cache',type=Path,required=True)
    p.add_argument('--repo',type=Path,required=True)
    p.add_argument('--start',type=date.fromisoformat,required=True)
    p.add_argument('--end',type=date.fromisoformat,required=True)
    args=p.parse_args()
    today=datetime.now(timezone(timedelta(hours=8))).date()
    if not month_start(today)<=args.start<=args.end<today:
        raise ValueError('Requested dates must be within the previous month through yesterday')
    reconstruct(args.data,args.cache,args.old_cache,args.repo,args.start,args.end)


if __name__=='__main__':
    main()
