"""Official free monthly FCM tables; never substitute monthly totals for missing days."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re

if __package__:
    from .market_history import encode, read, save, write_gzip, load_day
else:
    from market_history import encode, read, save, write_gzip, load_day

URLS = {kind: 'https://openapi.taifex.com.tw/v1/Monthly_'+suffix
        for kind, suffix in [('futures','FUT'),('options','OPT')]}
NOTE = '官方整月成交量，含經紀與自營列；買賣雙邊口數不代表多空方向。月表不補成逐日日表，亦不據此推算缺日成交量。'


def parse(raw):
    records = json.loads(raw)
    if not isinstance(records, list) or not records:
        raise ValueError('Empty or invalid monthly FCM response')
    months, seen = defaultdict(list), set()
    for r in records:
        if not isinstance(r, dict) or not {'YYYYMM','FCMCode','FCMName','Contract','Volume'} <= r.keys():
            raise ValueError('Monthly FCM columns changed')
        month = str(r['YYYYMM'])
        if not re.fullmatch(r'\d{6}', month):
            raise ValueError('Invalid monthly date')
        month = date.fromisoformat(month[:4]+'-'+month[4:]+'-01').isoformat()[:7]
        if any(not isinstance(r[k],str) or not r[k].strip() for k in ('FCMCode','FCMName','Contract')):
            raise ValueError('Missing monthly FCM identity')
        code, name, contract = (str(r[k]).strip() for k in ('FCMCode','FCMName','Contract'))
        volume = str(r['Volume'])
        key = month, code, contract
        if not code or not name or not contract or key in seen or not re.fullmatch(r'\d+', volume):
            raise ValueError('Duplicate identity or invalid monthly FCM row')
        seen.add(key)
        months[month].append({'month':month,'code':code,'name':name,'contract':contract,'volume':int(volume)})
    # Official files form a complete code/product grid, including zero rows.
    # Missing cells are rejected, never generated as zero.
    for rows in months.values():
        codes, contracts = {r['code'] for r in rows}, {r['contract'] for r in rows}
        if len(rows) != len(codes)*len(contracts):
            raise ValueError('Incomplete monthly FCM code/product grid')
    return months


def update(output, client, start, end):
    index = read(output/'fcm-monthly.json', {'months':{},'revisions':[]})
    # Retain the prior month as a comparison baseline, never the unfinished month.
    first = (date.fromisoformat(start).replace(day=1)-timedelta(days=1)).isoformat()[:7]
    last = (date.fromisoformat(end).replace(day=1)-timedelta(days=1)).isoformat()[:7]
    now = datetime.now(timezone.utc)
    freshness = (now-timedelta(days=1)).isoformat(timespec='seconds')
    index['checkedAt'] = now.isoformat(timespec='seconds')
    index['changedMonths'] = []
    try:
        for kind, url in URLS.items():
            raw, evidence = client.get(url, fresh_after=freshness)
            parsed = parse(raw)
            if last not in parsed:
                raise ValueError(kind+': closed month not yet published: '+last)
            for month, rows in sorted(parsed.items()):
                if not first <= month <= last:
                    continue
                payload = {'schemaVersion':1,'frequency':'monthly','market':kind,'month':month,
                           'rows':sorted(rows,key=lambda r:(r['contract'],r['code']))}
                packed = encode(payload); sha = hashlib.sha256(packed).hexdigest()
                old = index['months'].get(month,{}).get(kind)
                if old and old['sha256']==sha:
                    continue
                if month not in index['changedMonths']:
                    index['changedMonths'].append(month)
                path = f'market-history/fcm-monthly/{kind}-{month}-{sha[:12]}.json.gz'
                write_gzip(output/path, packed)
                if old:
                    index['revisions'].append({'month':month,'market':kind,**old})
                index['months'].setdefault(month,{})[kind] = {
                    'path':path,'sha256':sha,'rows':len(rows),'codes':len({r['code'] for r in rows}),
                    'contracts':len({r['contract'] for r in rows}),'evidence':evidence}
            client.accept(evidence)
        index.update(status='ok',error=None,note=NOTE)
    except Exception as exc:
        index.update(status='retained',error=str(exc)[:500],note=NOTE)
        save(output/'fcm-monthly.json',index)
        raise
    save(output/'fcm-monthly.json',index)
    return index


def panel(output, month):
    index = read(output/'fcm-monthly.json', {})
    entries = index.get('months',{}).get(month,{})
    if not entries:
        return None
    products, sources = [], []
    for kind, entry in sorted(entries.items()):
        doc = load_day(output,entry)
        if doc.get('frequency')!='monthly' or doc.get('market')!=kind or doc.get('month')!=month:
            raise ValueError('Monthly FCM archive identity mismatch')
        grouped = defaultdict(list)
        for row in doc['rows']:
            grouped[row['contract']].append(row)
        for contract, rows in sorted(grouped.items()):
            ranked = sorted(rows,key=lambda r:(-r['volume'],r['code']))
            total = sum(r['volume'] for r in rows)
            products.append({'market':kind,'contract':contract,'total':total,'codes':len(rows),
                             'activeCodes':sum(r['volume']>0 for r in rows),
                             'top5Share':round(100*sum(r['volume'] for r in ranked[:5])/total,4) if total else None,
                             'leaders':[{'code':r['code'],'name':r['name'],'volume':r['volume']} for r in ranked[:3] if r['volume']>0]})
        sources.append({'market':kind,**entry})
    priority = {'TXF':0,'TXO':1,'MXF':2,'TMF':3}
    products.sort(key=lambda r:(priority.get(r['contract'],4),r['market'],-r['total'],r['contract']))
    return {'month':month,'status':'available' if len(entries)==2 else 'partial',
            'frequency':'monthly','note':NOTE,'products':products,'sources':sources,
            'collectionStatus':index.get('status'),'error':index.get('error')}
