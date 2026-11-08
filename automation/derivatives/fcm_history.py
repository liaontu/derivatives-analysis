"""Archive the official current/previous-period DAILY CSVs, including all dates.

The previous-period daily file can contain a full prior month. Inspect its dates;
never interpret 'previous period' as 'previous day', or derive days from a month.
"""
import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import csv
import io
import re
from urllib.error import HTTPError

if __package__:
    from . import market_history as h
else:
    import market_history as h

CATALOGS = {'futures-fcm':'https://data.gov.tw/dataset/11356',
            'options-fcm':'https://data.gov.tw/dataset/11360'}
LICENSE = {'name':'政府資料開放授權條款－第1版','url':'https://data.gov.tw/license',
           'provider':'金融監督管理委員會證券期貨局／臺灣期貨交易所',
           'attribution':'臺灣期貨交易所期貨商交易量日報表；依政府資料開放授權條款第 1 版釋出。本站依原資料整理，未經資料提供機關認可或推薦。'}


def sources(name):
    suffix = 'FUT' if name=='futures-fcm' else 'OPT'
    return [('current','https://www.taifex.com.tw/cht/7/getFCMFileOpen?filename=Daily_'+suffix+'_OpenData.csv'),
            ('previous','https://www.taifex.com.tw/cht/7/getFCMFilePreOpen?filename=Daily_'+suffix+'_OpenData.csv')]


def parse(raw):
    try: text=raw.decode('utf-8-sig')
    except UnicodeDecodeError: text=raw.decode('cp950')
    reader=csv.DictReader(io.StringIO(text))
    required={'期貨商代號','期貨商名稱','日期','商品','交易量'}
    if not required <= set(reader.fieldnames or []):
        raise ValueError('Daily FCM CSV columns changed: '+str(reader.fieldnames))
    rows, seen, grouped = [], set(), defaultdict(list)
    for r in reader:
        if None in r or any(not isinstance(r.get(k),str) or not r[k].strip() for k in required):
            raise ValueError('Missing or malformed daily FCM fields')
        if not re.fullmatch(r'\d{8}',r['日期'].strip()):
            raise ValueError('Invalid daily FCM date')
        d=r['日期'].strip(); day=date.fromisoformat(d[:4]+'-'+d[4:6]+'-'+d[6:]).isoformat()
        if not re.fullmatch(r'\d+',r['交易量'].strip()):
            raise ValueError('Invalid daily FCM volume')
        row={'date':day,'code':r['期貨商代號'].strip(),'name':r['期貨商名稱'].strip(),
             'contract':r['商品'].strip(),'volume':float(r['交易量'])}
        key=day,row['code'],row['contract']
        if key in seen: raise ValueError('Duplicate daily FCM identity')
        seen.add(key);rows.append(row);grouped[day].append(row)
    if not rows: raise ValueError('Empty daily FCM response')
    for day, values in grouped.items():
        codes={r['code'] for r in values};contracts={r['contract'] for r in values}
        if len(values)!=len(codes)*len(contracts):
            raise ValueError('Incomplete daily FCM code/product grid: '+day)
    return rows


def validate(output,index,name,rows):
    grouped=defaultdict(list)
    for r in rows: grouped[r['date']].append(r)
    dates=sorted(grouped)
    expected=h.expected_days(output,name,dates[0],dates[-1])
    if dates!=expected: raise ValueError('Daily FCM dates differ from verified trading calendar')
    kind=name.removesuffix('-fcm');contract='TXF' if kind=='futures' else 'TXO'
    quote_contract='TX' if kind=='futures' else 'TXO'
    checked_quotes=[]
    for day, values in grouped.items():
        entry=index['feeds'].get(kind,{}).get(day)
        if not entry or not all(entry.get('sessions',{}).get(s) for s in ['一般','盤後']):continue
        quotes=h.load_day(output,entry)['rows']
        # Calendar-spread volume is already included in outright rows.
        quoted=[r for r in quotes if r['contract']==quote_contract and '/' not in r['month']]
        if not quoted:continue
        actual=sum(r['volume'] for r in values if r['contract']==contract)
        if actual!=2*sum(r['volume'] for r in quoted):
            raise ValueError('Daily FCM differs from official two-sided quote volume: '+day+' '+contract)
        checked_quotes.append(day)
    monthly=h.read(output/'fcm-monthly.json',{});month_checks=[]
    for month in sorted({d[:7] for d in dates}):
        entry=monthly.get('months',{}).get(month,{}).get(kind)
        end=month+'-'+str(calendar.monthrange(int(month[:4]),int(month[5:]))[1])
        full_dates=h.expected_days(output,name,month+'-01',end)
        if not entry or [d for d in dates if d.startswith(month)]!=full_dates:continue
        totals=defaultdict(float)
        for d in full_dates:
            for r in grouped[d]:totals[(r['code'],r['contract'])]+=r['volume']
        declared={(r['code'],r['contract']):r['volume'] for r in h.load_day(output,entry)['rows']}
        # Monthly files can include retired codes with a full month of zeros.
        # This comparison does not create missing daily zero rows.
        if any(totals.get(k,0)!=declared.get(k,0) for k in set(totals)|set(declared)):
            raise ValueError('Daily FCM sum differs from official monthly table: '+month)
        month_checks.append({'month':month,'comparedCells':len(set(totals)|set(declared)),
                             'monthlySha256':entry['sha256'],'status':'matched'})
    return {'sourceDates':dates,'rows':len(rows),'grid':'complete-for-source-codes-and-products',
            'quoteComparisonContract':contract,'quoteMatchedDates':sorted(checked_quotes),
            'monthlyChecks':month_checks}


def update(output,index,client,start,end):
    now=datetime.now(timezone.utc);checks=[];errors=[];blocked=None
    for name in CATALOGS:
        for period,url in sources(name):
            try:
                # Daily checks preserve source corrections; all runs reuse validated
                # responses for at most six hours. Previous-period file dates decide coverage.
                raw,evidence=client.get(url,fresh_after=(now-timedelta(hours=6)).isoformat(timespec='seconds'))
                rows=parse(raw); proof=validate(output,index,name,rows)
                evidence={**evidence,'format':'official-fcm-daily-csv','period':period,
                          'catalog':CATALOGS[name],'license':LICENSE,'validation':proof}
                selected=[r for r in rows if start<=r['date']<=end]
                h.archive(output,index,name,selected,evidence)
                # Identical data keeps its immutable hash, but gains the verified
                # full-CSV provenance and priority over a later API projection.
                for day in {r['date'] for r in selected}:
                    index['feeds'][name][day]['evidence']=evidence
                client.accept(evidence)
                checks.append({'feed':name,'period':period,'status':'ok','url':url,
                               'sha256':evidence['sha256'],'fetchedAt':evidence['fetchedAt'],
                               'importedDates':sorted({r['date'] for r in selected}),**proof})
            except Exception as exc:
                errors.append({'feed':name,'period':period,'url':url,'error':str(exc)[:600]})
                if isinstance(exc,HTTPError) and exc.code in (401,403,429):
                    blocked=exc;break
        if blocked:break
    result={'schemaVersion':1,'checkedAt':now.isoformat(timespec='seconds'),
            'status':'retained' if errors else 'ok','checks':checks,'errors':errors,'license':LICENSE}
    h.save(output/'fcm-history-check.json',result)
    if blocked:raise blocked
    if errors:raise RuntimeError('FCM daily CSV: '+'; '.join(x['error'] for x in errors))
    return result
