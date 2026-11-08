"""Validated, compressed daily archives of free derivatives reports.

Quotes retain both sessions. Research keeps its existing regular-session rules.
Never infer missing broker-volume history from a top-five summary.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

if __package__:
    from .collect import number, iso_date, cp, FEEDS
    from .us_sources import US_FEEDS, COT_DATASETS, options_document, normalize_us
    from .publication import save_text
    from .trading_calendar import load_calendar
else:
    from collect import number, iso_date, cp, FEEDS
    from us_sources import US_FEEDS, COT_DATASETS, options_document, normalize_us
    from publication import save_text
    from trading_calendar import load_calendar

ROOT = 'market-history'
VERSION = 'derivatives-history-v1'
DOWNLOADS = {
    'futures': ('futDataDown', {'down_type':'1','commodity_id':'all','commodity_id2':''}),
    'options': ('dlOptDataDown', {'down_type':'1','commodity_id':'all','commodity_id2':''}),
    'futures-institutional': ('futContractsDateDown', {'commodityId':''}),
    'options-institutional': ('callsAndPutsDateDown', {'commodityId':''}),
    'futures-large': ('largeTraderFutDown', {}),
    'options-large': ('largeTraderOptDown', {}),
}
NAMES = {'futures':'台灣期貨行情（日／夜盤）', 'options':'台灣選擇權行情（日／夜盤）',
         'futures-institutional':'期貨三大法人（全商品）', 'options-institutional':'選擇權三大法人（全商品）',
         'futures-large':'期貨大額交易人（全商品／到期別）', 'options-large':'選擇權大額交易人（全商品／到期別）',
         'futures-fcm':'期貨商成交量－期貨全表', 'options-fcm':'期貨商成交量－選擇權全表',
         'pcr':'臺指選擇權 P/C', **{n:v[0] for n,v in US_FEEDS.items()}}
FCM_NOTE = '官方當期／前一期 CSV 按檔內日期保存完整日表；前一期可涵蓋整個上月。雙邊成交量不是多空方向，也不是分點部位；缺日不以排行或月量補值。'
FCM_SOURCE = 'https://www.taifex.com.tw/cht/7/dailyFCM'
US_CALENDAR = 'https://www.cboe.com/about/hours/us-options'
# Verified exchange closures, not federal/bank holidays. Unknown years stay unverified.
US_CLOSED = {2026: {'2026-'+d for d in ('01-01','01-19','02-16','04-03','05-25','06-19','07-03','09-07','11-26','12-25')}}


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def read(path, default=None):
    return json.loads(path.read_bytes()) if path.exists() else default


def save(path, value):
    save_text(path, encode(value).decode())


def days(start, end):
    cursor = date.fromisoformat(start)
    while cursor.isoformat() <= end:
        yield cursor.isoformat()
        cursor += timedelta(days=1)


def expected_days(output, name, start, end):
    if name in COT_DATASETS:
        return [d for d in days(start,end) if date.fromisoformat(d).weekday()==1]
    if name.startswith('us-'):
        if any(date.fromisoformat(d).year not in US_CLOSED for d in (start,end)):
            return None
        return [d for d in days(start,end) if date.fromisoformat(d).weekday()<5 and d not in US_CLOSED[date.fromisoformat(d).year]]
    cal = load_calendar(output)
    return [d for d in days(start,end) if cal.is_open(date.fromisoformat(d))]


def positive(x):
    v = number(x)
    return v if v is not None and v > 0 else None


def parse_csv(name, raw, start, end):
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp950')
    reader = csv.DictReader(io.StringIO(text))
    if name in ('futures','options'):
        required = {'交易日期','契約','到期月份(週別)','交易時段','收盤價','成交量','結算價','未沖銷契約數','最後最佳買價','最後最佳賣價'}
        if name == 'options': required |= {'履約價','買賣權'}
    elif name.endswith('institutional'):
        required = {'日期','商品名稱','身份別'} | ({'多方交易口數','空方交易口數','多空交易口數淨額','多方未平倉口數','空方未平倉口數','多空未平倉口數淨額'} if name.startswith('futures') else {'買賣權別','買方交易口數','賣方交易口數','交易口數買賣淨額','買方未平倉口數','賣方未平倉口數','未平倉口數買賣淨額'})
    else:
        required = {'日期','商品(契約)','商品名稱(契約名稱)','到期月份(週別)','交易人類別','前五大交易人買方','前五大交易人賣方','前十大交易人買方','前十大交易人賣方','全市場未沖銷部位數'}
        if name.startswith('options'): required.add('買賣權')
    if not required <= set(reader.fieldnames or []):
        raise ValueError(name+': CSV columns changed: '+str(reader.fieldnames))
    result, seen = [], set()
    for raw_row in reader:
        # Official futures CSV has a trailing empty column; reject actual overflow.
        if any(str(v).strip() for v in raw_row.get(None, [])):
            raise ValueError('CSV row width mismatch')
        r = {k:(v or '').strip() for k,v in raw_row.items() if k is not None}
        if not any(r.values()): continue
        if name.endswith('large') and not r.get('商品(契約)') and r.get('日期','').startswith(('月份類別格式:', '交易人類別格式：', '-表當日', '備註一：', '備註二：', '備註三：')):
            continue
        day = iso_date(r.get('交易日期', r.get('日期')))
        if not day or not start <= day <= end: raise ValueError('Source date outside requested range')
        out = {'date':day}
        if name in ('futures','options'):
            volume = number(r['成交量'])
            if volume is None or volume < 0: raise ValueError('Invalid quote volume')
            session = r['交易時段']
            if session not in ('一般','盤後'): raise ValueError('Unknown trading session')
            out.update(contract=r['契約'],month=r['到期月份(週別)'],session=session,
                       last=number(r['收盤價']) if volume > 0 else None, volume=volume,
                       price=number(r['結算價']),oi=number(r['未沖銷契約數']),
                       bid=number(r['最後最佳買價']),ask=number(r['最後最佳賣價']),
                       open=number(r.get('開盤價')),high=number(r.get('最高價')),low=number(r.get('最低價')))
            if name=='options': out.update(cp=cp(r['買賣權']),strike=number(r['履約價']))
            else: out['changePct']=number(r.get('漲跌%','').replace('%',''))
            key = (day,out['contract'],out['month'],session,out.get('cp'),out.get('strike'))
        elif name.endswith('institutional'):
            out.update(contract=r['商品名稱'],investor=r['身份別'],cp=cp(r.get('買賣權別')))
            columns = ['多方交易口數','空方交易口數','多空交易口數淨額','多方未平倉口數','空方未平倉口數','多空未平倉口數淨額'] if name.startswith('futures') else ['買方交易口數','賣方交易口數','交易口數買賣淨額','買方未平倉口數','賣方未平倉口數','未平倉口數買賣淨額']
            out.update({k:number(r[v]) for k,v in zip(['buy','sell','net','longOi','shortOi','netOi'],columns)})
            if any(out[k] is None for k in ('buy','sell','net','longOi','shortOi','netOi')): raise ValueError('Missing institutional totals')
            if out['buy']-out['sell'] != out['net'] or out['longOi']-out['shortOi'] != out['netOi']: raise ValueError('Institutional totals do not balance')
            key = (day,out['contract'],out['investor'],out['cp'])
        else:
            out.update(contract=r['商品(契約)'],name=r['商品名稱(契約名稱)'],month=r['到期月份(週別)'],traderType=r['交易人類別'],cp=cp(r.get('買賣權')))
            if out['month']=='999999':
                out.update(month='999912',sourceMonth='999999')
            out.update({k:number(r[v]) for k,v in zip(['top5Buy','top5Sell','top10Buy','top10Sell','oi'],['前五大交易人買方','前五大交易人賣方','前十大交易人買方','前十大交易人賣方','全市場未沖銷部位數'])})
            if any(out[k] is not None and out[k] < 0 for k in ('top5Buy','top5Sell','top10Buy','top10Sell','oi')): raise ValueError('Invalid large trader totals')
            key = (day,out['contract'],out['month'],out['traderType'],out['cp'])
        if not out['contract'] or key in seen: raise ValueError('Missing or duplicate source identity')
        seen.add(key); result.append(out)
    if not result: raise ValueError('Empty historical response')
    return result


def write_gzip(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    packed = gzip.compress(raw, mtime=0)
    if path.exists() and path.read_bytes()==packed: return
    tmp = path.with_suffix('.tmp'); tmp.write_bytes(packed); tmp.replace(path)


def archive(output, index, name, rows, evidence):
    grouped = defaultdict(list)
    for row in rows:
        date.fromisoformat(row['date']); grouped[row['date']].append(row)
    for day, values in grouped.items():
        values = sorted(values, key=lambda r:encode(r))
        payload = {'schemaVersion':1,'feed':name,'date':day,'rows':values}
        raw = encode(payload); sha = hashlib.sha256(raw).hexdigest()
        old = index['feeds'].setdefault(name,{}).get(day)
        if old and old['sha256']==sha:
            continue
        index.setdefault('changedDates',[]).append(day)
        relative = f'{ROOT}/{name}/{day}-{sha[:12]}.json.gz'
        write_gzip(output/relative, raw)
        entry = {'path':relative,'sha256':sha,'bytes':(output/relative).stat().st_size,'rows':len(values),
                 'contracts':len({r.get('contract') for r in values}),
                 'sessions':dict(Counter(r.get('session') for r in values if r.get('session'))), 'evidence':evidence}
        if old:
            index.setdefault('revisions',[]).append({'feed':name,'date':day,**old})
        index['feeds'][name][day] = entry


def load_day(output, entry):
    raw = gzip.decompress((output/entry['path']).read_bytes())
    if hashlib.sha256(raw).hexdigest()!=entry['sha256']: raise ValueError('Archive hash mismatch')
    return json.loads(raw)


def ingest_saved(output, index):
    manifest = read(output/'manifest.json',{})
    sources = {s['id']:s for s in manifest.get('sources',[])}
    for name in NAMES:
        feed = read(output/(name+'.json'),{})
        source = sources.get(name,{})
        for record in feed.get('history',[]) + ([feed] if source.get('status')=='ok' and source.get('date')==feed.get('date') else []):
            day = record.get('date')
            if not day or not record.get('rows') or any(r.get('date')!=day for r in record['rows']): continue
            # A downloaded all-session CSV is richer than the current API projection.
            previous=index['feeds'].get(name,{}).get(day)
            if previous:
                if name.endswith('fcm') and previous.get('evidence',{}).get('format')=='official-fcm-daily-csv':
                    continue  # Refresh through the validated full CSV, never a filtered live projection.
                # Downloaded CSV snapshots are richer; refresh them separately when
                # the live source has been checked later. Other live feeds can revise.
                if name in DOWNLOADS or record is not feed: continue
                if (feed.get('fetchedAt') or '') <= (previous.get('evidence',{}).get('fetchedAt') or ''): continue
            values = record['rows']
            if name in ('futures','options'):
                values = [{**r,'last':r.get('last') if (r.get('volume') or 0)>0 else None} for r in values]
            archive(output,index,name,values,{'url':source.get('url'),'basis':'本站已保存且日期一致的官方資料','fetchedAt':feed.get('fetchedAt')})


def ingest_git(output, index, repo):
    for name in ('futures-fcm','options-fcm'):
        relative = 'data/derivatives/'+name+'.json'
        commits = subprocess.check_output(['git','log','--format=%H','--',relative],cwd=repo,text=True).splitlines()
        for commit in commits:
            try: feed = json.loads(subprocess.check_output(['git','show',commit+':'+relative],cwd=repo,stderr=subprocess.DEVNULL))
            except (subprocess.CalledProcessError,ValueError): continue
            day = feed.get('date')
            if day in index['feeds'].get(name,{}) or not feed.get('rows'): continue
            if any(r.get('date')!=day for r in feed['rows']): raise ValueError('Saved git source date mismatch')
            archive(output,index,name,feed['rows'],{'url':FCM_SOURCE,'basis':'本站官方完整表格版本紀錄','commit':commit,'fetchedAt':feed.get('fetchedAt')})


class Client:
    def __init__(self, output): self.output=output; self.last=0
    def get(self, url, fields=None, fresh_after=''):
        key = hashlib.sha256(encode([url,fields])).hexdigest()
        pointer = self.output/ROOT/'requests'/(key+'.json')
        old = read(pointer)
        if old and old.get('validated') and old.get('fetchedAt','') >= fresh_after:
            raw=gzip.decompress((self.output/old['path']).read_bytes())
            if hashlib.sha256(raw).hexdigest()!=old['sha256']: raise ValueError('Cached response hash mismatch')
            return raw,old
        time.sleep(max(0,1.1-(time.monotonic()-self.last)))
        self.last=time.monotonic()
        req=Request(url,data=urlencode(fields).encode() if fields is not None else None,
                    headers={'User-Agent':'Mozilla/5.0 (public derivatives history research)','Accept':'*/*'})
        with urlopen(req,timeout=75) as response:
            raw=response.read(60_000_001)
            if len(raw)>60_000_000: raise ValueError('Historical response too large')
            metadata={'url':url,'parameters':fields,'httpStatus':response.status,'contentType':response.headers.get('Content-Type'),
                      'fetchedAt':datetime.now(timezone.utc).isoformat(timespec='seconds'),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
        metadata['path']=f"{ROOT}/sources/{metadata['sha256']}.raw.gz"
        write_gzip(self.output/metadata['path'],raw)
        # Cache only transport success; consumers still validate schema/date on every use.
        save(pointer,metadata)
        return raw,metadata

    def accept(self, metadata):
        key = hashlib.sha256(encode([metadata['url'],metadata.get('parameters')])).hexdigest()
        save(self.output/ROOT/'requests'/(key+'.json'),{**metadata,'validated':True})


def download_tw(output, index, client, start, end, max_windows=None):
    windows=0
    for name,(route,extra) in DOWNLOADS.items():
        expected=expected_days(output,name,start,end)
        missing=[d for d in expected if d not in index['feeds'].get(name,{}) or (name in ('futures','options') and not index['feeds'][name][d].get('sessions',{}).get('盤後'))]
        live=read(output/(name+'.json'),{})
        latest=live.get('date')
        previous=index['feeds'].get(name,{}).get(latest,{})
        if latest and start<=latest<=end and (live.get('fetchedAt') or '') > (previous.get('evidence',{}).get('fetchedAt') or ''):
            missing=sorted(set(missing)|{latest})
        while missing and (max_windows is None or windows < max_windows):
            first=missing[0]; last=min(end,(date.fromisoformat(first)+timedelta(days=13)).isoformat())
            url='https://www.taifex.com.tw/cht/3/'+route
            fields={**extra,'queryStartDate':first.replace('-','/'),'queryEndDate':last.replace('-','/')}
            raw,evidence=client.get(url,fields,(live.get('fetchedAt') or '') if first<=str(latest)<=last else '')
            archive(output,index,name,parse_csv(name,raw,first,last),evidence)
            client.accept(evidence)
            save(output/(ROOT+'.json'),index)
            print(json.dumps({'source':name,'start':first,'end':last,'bytes':len(raw)}),flush=True)
            missing=[d for d in missing if d>last]; windows+=1


def download_us(output,index,client,start,end,max_days=None):
    count=0
    for day in expected_days(output,'us-options',start,end) or []:
        needed=[n for n in ('us-options','us-futures') if day not in index['feeds'].get(n,{})]
        if not needed: continue
        if max_days is not None and count>=max_days: break
        for name in needed:
            url = US_FEEDS[name][1]+'?dt='+day if name=='us-options' else 'https://www-api.cboe.com/us/futures/market_statistics/settlement/csv?dt='+day
            raw,evidence=client.get(url)
            doc=options_document(raw) if name=='us-options' else {'date':day,'rows':list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))}
            rows=normalize_us(name,doc,number,iso_date)
            if not rows or any(r['date']!=day for r in rows): raise ValueError('US historical date mismatch')
            archive(output,index,name,rows,evidence)
            client.accept(evidence)
            save(output/(ROOT+'.json'),index)
        print(json.dumps({'US':day,'sources':needed}),flush=True); count+=1


def backfill_stock(output,index,client,start,end,max_days=None):
    if __package__: from . import stock_options as stock
    else: import stock_options as stock
    target=read(output/'stock-option-watch.json',{})
    missing=[d for d in expected_days(output,'options',start,end) if d in index['feeds'].get('options',{}) and
             read(output/'stock-option-watch'/(d+'.json'),{}).get('optionsHash') != index['feeds']['options'][d]['sha256']]
    if not missing: return
    current_contracts,current_roster=None,None
    for day in missing[:max_days] if max_days is not None else missing:
        previous=read(output/'stock-option-watch'/(day+'.json'),{})
        if previous.get('stocks'):
            contracts={symbol:(r['code'],r['name']) for symbol,r in previous['stocks'].items()}
            roster=previous.get('roster') or {'url':stock.LIST_URL,'historicalScope':'沿用原觀察檔已保存名錄；未驗證歷史全市場名錄'}
        else:
            if current_contracts is None:
                current_contracts,current_roster=stock.fetch_contracts(output)
                current_roster={**current_roster,'historicalScope':'以補抓時官方名錄回查，非歷史完整名錄，存在存活偏差'}
            contracts,roster=current_contracts,current_roster
        options=load_day(output,index['feeds']['options'][day])
        options['provenance']={'sha256':index['feeds']['options'][day]['sha256']}
        raw,evidence=client.get(stock.TWSE_DAILY+day.replace('-',''))
        quotes=stock.stock_market_quotes(json.loads(raw),day,{code for code,_ in contracts.values()})
        snapshot=stock.build(output,options,contracts,quotes)
        client.accept(evidence)
        snapshot.update(roster=roster,spotEvidence=evidence)
        if previous:
            save(output/'stock-option-watch/revisions'/(day+'-'+hashlib.sha256(encode(previous)).hexdigest()[:12]+'.json'),previous)
        save(output/'stock-option-watch'/(day+'.json'),snapshot)
        target['dates']=sorted(set(target.get('dates',[]))|{day}); target['latest']=target['dates'][-1]
        target.update(status='ok',historicalScope='歷史名錄範圍依各日說明；目前名錄回查者存在存活偏差')
        index.setdefault('changedDates',[]).append(day)
        save(output/'stock-option-watch.json',target)
        print(json.dumps({'stock-options':day,'stocks':len(snapshot['stocks'])}),flush=True)


def rebuild_position_history(output, index, start, end, overwrite=False):
    if __package__: from . import daily_reports as reports
    else: import daily_reports as reports
    for day in expected_days(output,'futures',start,end):
        if not overwrite and day not in index.get('changedDates',[]) and (output/'daily-reports/positioning'/(day+'.json')).exists(): continue
        feeds,sources,missing={},{},[]
        for name in reports.POSITION_SOURCES:
            entry=index['feeds'].get(name,{}).get(day)
            if entry:
                feeds[name]=load_day(output,entry)['rows']
                sources[name]={'id':name,'date':day,'status':'ok','url':entry['evidence'].get('url'),'sha256':entry['sha256']}
            else:
                missing.append(name);feeds[name]=[]
                sources[name]={'id':name,'date':None,'status':'unavailable','url':FCM_SOURCE,'reason':FCM_NOTE}
        if any(not n.endswith('fcm') for n in missing): continue
        report=reports.build_position_report(day,list(sources.values()),feeds)
        report['coverage']={'status':'partial' if missing else 'available','missingSources':missing,
                            'note':FCM_NOTE if missing else '全表日期一致，依事後補抓資料重建'}
        reports.archive(output,'positioning',report)


def sync_small_histories(output,index):
    """Existing Taiwan/US charts also benefit; large quote feeds stay sharded."""
    for name in ('futures-institutional','options-institutional','pcr','us-options','us-futures'):
        path=output/(name+'.json');feed=read(path,{})
        if not feed.get('date'): continue
        entries=index['feeds'].get(name,{})
        selected=sorted(d for d in entries if d<=feed['date'])[-60:]
        feed['history']=[{'date':d,'rows':load_day(output,entries[d])['rows']} for d in selected]
        feed['historySource']='market-history.json'
        save(path,feed)


def coverage(output,index,start,end):
    rows=[]
    for name,label in NAMES.items():
        expected=expected_days(output,name,start,end)
        available=sorted(d for d in index['feeds'].get(name,{}) if start<=d<=end)
        missing=[d for d in expected or [] if d not in available]
        rows.append({'id':name,'label':label,'dates':available,'expected':len(expected) if expected is not None else None,
                     'missingDates':missing,'status':'unverified-calendar' if expected is None else 'partial' if missing else 'complete',
                     'note':FCM_NOTE if name.endswith('fcm') else 'CFTC 週二部位；通常週五公布，非每日籌碼' if name in COT_DATASETS else
                     'CFE 公開結算價；不含 CME 全行情' if name=='us-futures' else 'Cboe 公開統計；不含美股完整選擇權鏈' if name=='us-options' else
                     '日、夜盤分開保存；期交所交易日期歸屬，不以時鐘日期相加。缺報價不代表零價格。' if name in ('futures','options') else '',
                     'url':FCM_SOURCE if name.endswith('fcm') else US_FEEDS.get(name,('',None))[1] if name.startswith('us-') else 'https://www.taifex.com.tw/cht/3/'+DOWNLOADS[name][0] if name in DOWNLOADS else 'https://www.taifex.com.tw/cht/3/pcRatio'})
    index.update(version=VERSION,start=start,end=end,coverage=rows,
                 status='partial' if any(r['status']!='complete' for r in rows) else 'ok',
                 calendar={'taiwan':'trading-calendar.json','us':US_CALENDAR,'usVerifiedYears':sorted(US_CLOSED)},
                 limitations=['完整度指已取得來源報表日期，不保證每個契約都有成交價或買賣報價。',FCM_NOTE,
                              '美國免費範圍為 Cboe 統計、CFE 結算與 CFTC 週報，不含 CME 全市場行情或美股完整選擇權鏈。',
                              '歷史回補是事後重建；原始來源、雜湊與舊修訂保留，不能當成當時即時取得。'])
    audit = read(Path(__file__).with_name('fcm-source-audit.json'))
    if audit:
        index['fcmSourceAudit'] = audit
    save(output/(ROOT+'.json'),index)


def run(output,start,end,full=False,git_repo=None,max_windows=6):
    index=read(output/(ROOT+'.json'),{'feeds':{},'revisions':[]})
    index['changedDates']=[]
    client=Client(output); errors=[]
    ingest_saved(output,index)
    if git_repo: ingest_git(output,index,git_repo)
    baseline=(date.fromisoformat(start)-timedelta(days=10)).isoformat()
    if __package__:
        from .fcm_monthly import update as update_fcm_monthly
        from .fcm_history import update as update_fcm_history
    else:
        from fcm_monthly import update as update_fcm_monthly
        from fcm_history import update as update_fcm_history
    for operation in (lambda:update_fcm_monthly(output,client,start,end),
                      lambda:update_fcm_history(output,index,client,start,end),
                      lambda:download_tw(output,index,client,baseline,end,None if full else max_windows),
                      lambda:download_us(output,index,client,baseline,end,None if full else 2),
                      lambda:backfill_stock(output,index,client,baseline,end,None if full else 2)):
        try: operation()
        except Exception as exc:
            detail={'checkedAt':datetime.now(timezone.utc).isoformat(timespec='seconds'),'type':type(exc).__name__,'error':str(exc)[:800]}
            errors.append(detail); print(json.dumps(detail,ensure_ascii=False),flush=True)
            if isinstance(exc,HTTPError) and exc.code in (401,403,429): break
    index['checkedAt']=datetime.now(timezone.utc).isoformat(timespec='seconds')
    index['errors']=errors
    index['changedDates']=sorted(set(index['changedDates']))
    coverage(output,index,start,end)
    sync_small_histories(output,index)
    rebuild_position_history(output,index,start,end,overwrite=full)
    if errors:
        save(output/ROOT/'failures'/(index['checkedAt'].replace(':','')+'.json'),errors)
        index['status']='retained'; save(output/(ROOT+'.json'),index)
    return index


def main():
    p=argparse.ArgumentParser(); p.add_argument('--data',type=Path,default=Path('data/derivatives'))
    p.add_argument('--start',default='2026-10-01');p.add_argument('--end');p.add_argument('--full',action='store_true')
    p.add_argument('--git-repo',type=Path);p.add_argument('--max-windows',type=int,default=6)
    args=p.parse_args()
    end=args.end or read(args.data/'futures.json',{}).get('date')
    if not end: raise SystemExit('No validated market cutoff')
    result=run(args.data,args.start,end,args.full,args.git_repo,args.max_windows)
    print(json.dumps({'start':result['start'],'end':result['end'],'status':result['status'],'errors':result['errors']},ensure_ascii=False))
    if result['errors']: raise SystemExit(1)


if __name__=='__main__': main()
