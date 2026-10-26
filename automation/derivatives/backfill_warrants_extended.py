"""Bounded free history from dated MOPS adjustments, verified against saved TWSE terms.

Only effective-dated evidence is used: never extend today's terms backwards.
Requires the verified raw cache from the original one-month import. Import into
a seeded review copy. Public prices are fetched once per date/side, 2s apart.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import gzip
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from threading import Lock
from urllib.parse import urlencode
import urllib.request

try:
    from . import collect as c
    from .backfill_warrants import CachedSource, validate_report
    from .warrant_spreads import exclusion, positive, style, valid_day
except ImportError:
    import collect as c
    from backfill_warrants import CachedSource, validate_report
    from warrant_spreads import exclusion, positive, style, valid_day

METHOD = 'effective-announcements-v1'
REGULAR = re.compile(r'(?:\d{6}|\d{5}[PUT])')
HEADERS = ['權證代號', '權證名稱', '履約方式', '權證類型', '標的證券代號', '標的證券名稱',
           '最新履約價格/履約指數', '最新行使比例', '調整生效日期', '權證到期日']


class TableRows(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows = []; self.row = None; self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr': self.row = []
        if tag in ('th', 'td'): self.cell = []

    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('th', 'td') and self.cell is not None:
            if self.row is not None: self.row.append(''.join(self.cell).strip())
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row); self.row = None


def adjustment_rows(raw, start, end):
    parser = TableRows(); parser.feed(raw)
    if HEADERS not in parser.rows:
        raise ValueError('Missing regular-warrant adjustment table; not an empty-success report')
    rows, seen = [], {}
    for r in parser.rows:
        if not r or not REGULAR.fullmatch(r[0]): continue
        if len(r) != 10: raise ValueError('Unexpected regular-warrant adjustment columns')
        day, expiry = c.iso_date(r[8]), c.iso_date(r[9])
        if not valid_day(day) or not start.isoformat() <= day <= end.isoformat() or not valid_day(expiry):
            raise ValueError('Adjustment report dates outside requested range')
        record = dict(code=r[0], name=r[1], style=style(r[2]), cp=c.cp(r[3]), underlying=r[4],
                      underlyingName=r[5], strike=c.number(r[6]), ratio=c.number(r[7]), effective=day, expiry=expiry)
        if not record['style'] or record['cp'] not in ('C', 'P') or not all(positive(record[k]) for k in ('strike', 'ratio')):
            raise ValueError('Invalid adjustment terms')
        key = (r[0], expiry, day)
        if key in seen and seen[key] != record:
            raise ValueError('Conflicting effective-dated adjustment records')
        if key not in seen: rows.append(record)
        seen[key] = record
    return rows


def reset_codes(raw):
    parser = TableRows(); parser.feed(raw)
    # Fail closed on errors/login/challenges instead of treating them as no resets.
    if not any('權證代號' in r and any('生效' in cell for cell in r) for r in parser.rows):
        raise ValueError('Missing reset report header')
    return {r[0] for r in parser.rows if r and REGULAR.fullmatch(r[0])}


def announcement(cache, name, start, end, now):
    params = {'TYPEK': '', 'colorchg': '', 'step': '1', 'co_id': '', 'warrant_id': '',
              'effective_date_1': f'{start.year - 1911:03d}{start:%m%d}',
              'effective_date_2': f'{end.year - 1911:03d}{end:%m%d}'}
    url = 'https://mopsov.twse.com.tw/mops/web/ajax_' + name
    path = cache / f'{name}-{start}-{end}.json.gz'
    if path.exists():
        doc = json.loads(gzip.decompress(path.read_bytes()))
        if doc.get('url') != url or doc.get('params') != params: raise ValueError('Announcement cache query mismatch')
    else:
        time.sleep(2)
        request = urllib.request.Request(url, data=urlencode(params).encode(),
                                         headers={'User-Agent': 'DerivativeInsight/2.1 (public history research)'})
        with urllib.request.urlopen(request, timeout=40) as response: body = response.read(40_000_001)
        if len(body) > 40_000_000: raise ValueError('Announcement exceeds size limit')
        doc = {'url': url, 'params': params, 'retrievedAt': now, 'raw': body.decode('utf-8')}
    records = adjustment_rows(doc['raw'], start, end) if name == 't95sb02' else reset_codes(doc['raw'])
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps(doc, ensure_ascii=False).encode(), mtime=0))
    audit = {'source': name, 'url': 'https://mopsov.twse.com.tw/mops/web/' + name,
             'requestUrl': url, 'method': 'POST', 'params': params, 'retrievedAt': doc['retrievedAt'],
             'sha256': sha256(doc['raw'].encode()).hexdigest(), 'hashBasis': 'utf8-response', 'rows': len(records)}
    return records, audit


def saved_terms(cache):
    snapshots, receipts = [], []
    for path in sorted(cache.glob('twse-warrants-????-??-??.json.gz')):
        day = date.fromisoformat(path.name[len('twse-warrants-'):len('twse-warrants-') + 10])
        rows, audit = CachedSource(cache).get('twse-warrants', day)  # verified cache only: path exists
        if rows is None: continue
        raw = json.loads(gzip.decompress(path.read_bytes()))['raw']
        listings = {str(r[0]).strip(): c.iso_date(r[10]) for r in raw['data']}
        snapshots.extend({**r, 'listed': listings[r['code']]} for r in rows)
        receipts.append(audit)
    if not snapshots: raise ValueError('Verified dated TWSE terms cache is required')
    return snapshots, receipts


def matched_plans(adjustments, snapshots, resets):
    groups, anchors = defaultdict(list), defaultdict(list)
    for r in adjustments: groups[(r['code'], r['expiry'])].append(r)
    for r in snapshots:
        if REGULAR.fullmatch(r['code']) and valid_day(r.get('listed')) and valid_day(r.get('lastTrade')):
            anchors[(r['code'], r['expiry'])].append(r)
    plans, excluded = [], Counter()
    keys = ('underlying', 'cp', 'style', 'strike', 'ratio', 'expiry')
    for key, changes in sorted(groups.items()):
        if key[0] in resets:
            excluded['區間內有重設公告'] += 1; continue
        checks = sorted(anchors.get(key, []), key=lambda r: r['date'])
        if not checks:
            excluded['缺少已保存官方條款可交叉核對'] += 1; continue
        changes = sorted(changes, key=lambda r: r['effective'])
        if any(r[k] != (style(checks[0][k]) if k == 'style' else checks[0][k])
               for r in changes for k in ('underlying', 'cp', 'style', 'expiry')):
            excluded['發行批次固定條件不一致'] += 1; continue
        compared = 0
        for snap in checks:
            available = [r for r in changes if r['effective'] <= snap['date']]
            if available:
                compared += 1
                if any(available[-1][k] != (style(snap[k]) if k == 'style' else snap[k]) for k in keys): break
            if any(snap[k] != checks[0][k] for k in ('listed', 'lastTrade', 'underlying', 'cp', 'expiry')): break
        else:
            if compared:
                anchor = checks[0]
                if any(r['effective'] < anchor['listed'] for r in changes):
                    excluded['生效日期早於同次發行上市日'] += 1; continue
                plans.append({'anchor': anchor, 'changes': changes, 'checkedSnapshots': compared})
                continue
        excluded['公告與已保存條款不符或無交集'] += 1
    return plans, dict(excluded)


def trade_rows(raw, day, source):
    normalized = validate_report(source, day, raw)
    if normalized is None: return None
    tables = [t for t in raw.get('tables', []) if {'證券代號', '成交股數', '成交金額', '收盤價', '證券名稱'} <= set(t.get('fields', []))]
    if len(tables) != 1: raise ValueError('Historical closing-price fields changed')
    table = tables[0]
    raw_rows = {str(r[table['fields'].index('證券代號')]).strip(): dict(zip(table['fields'], r)) for r in table['data']}
    return [{**r, 'price': c.number(raw_rows[r['code']]['收盤價']), 'name': raw_rows[r['code']]['證券名稱'].strip(),
             'underlying': str(raw_rows[r['code']].get('標的代號', '')).strip(),
             'underlyingPrice': c.number(raw_rows[r['code']].get('標的收盤價/指數'))}
            for r in normalized]


def quotes_for_day(day, trades, plans):
    by_code = {r['code']: r for r in trades}
    if len(by_code) != len(trades): raise ValueError('Duplicate call/put codes')
    result = []
    for plan in plans:
        anchor = plan['anchor']
        if not anchor['listed'] <= day <= anchor['lastTrade']: continue
        available = [r for r in plan['changes'] if r['effective'] <= day]
        evidence = available[-1] if available else None
        trade = by_code.get(anchor['code']) or {}
        # Code reuse/name changes are not inferred. No matching quote stays missing.
        same_name = (any(trade.get('name') == r['name'] for r in plan['changes']) and
                     (not trade.get('underlying') or trade['underlying'] == anchor['underlying']))
        row = {k: anchor.get(k) for k in ('code', 'name', 'underlying', 'underlyingName', 'cp', 'style', 'expiry', 'lastTrade')}
        row.update(date=day, market='TWSE', termsDate=day if evidence else None,
                   ratio=evidence['ratio'] if evidence else None, strike=evidence['strike'] if evidence else None,
                   price=trade.get('price') if same_name else None, volume=trade.get('volume') if same_name else None)
        if evidence:
            row['termEvidence'] = {'basis': METHOD, 'effective': evidence['effective'],
                                   'verifiedFrom': anchor['date'], 'source': 't95sb02'}
        if same_name and trade.get('underlying') == anchor['underlying']:
            row['underlyingPrice'] = trade.get('underlyingPrice')
        result.append(row)
    if len({r['code'] for r in result}) != len(result): raise ValueError('Overlapping warrant issuance identities')
    return result


def historical_market(source, cache, day):
    def side(name):
        _, audit = source.get(name, day)
        raw = json.loads(gzip.decompress((cache / f'{name}-{day}.json.gz').read_bytes()))['raw']
        return trade_rows(raw, day, name), audit
    # Two independent public reports; start times are paced by the shared source.
    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(side, ('twse-volume', 'twse-volume-put')))


def backfill_extended(output, cache, start, end, today, now):
    if not today - timedelta(days=120) <= start <= end < today:
        raise ValueError('Extended import is bounded to the past 120 calendar days')
    current = c.read_feeds(output)
    prior = c.read(output / 'warrant-backfill.json') or {}
    if not prior.get('importedDates') or not current.get('twse-warrants', {}).get('rows'):
        raise ValueError('Seed published daily feeds and original backfill first')
    daily_dates = [d['date'] for d in prior['days'] if d['status'] == 'imported' and d.get('termsMethod') != METHOD]
    if not daily_dates or end.isoformat() >= min(daily_dates):
        raise ValueError('Extension must precede the verified daily-report import')
    snapshots, anchors = saved_terms(cache)
    anchor_end = date.fromisoformat(max(s['date'] for s in snapshots))
    adjustments, adjustment_audit = announcement(cache, 't95sb02', start, anchor_end, now)
    resets, reset_audit = announcement(cache, 't95sb03', start, anchor_end, now)
    plans, excluded = matched_plans(adjustments, snapshots, resets)
    if not plans: raise ValueError('No historical terms passed cross-checks')
    memo, pending = {}, set()
    def read(path):
        if path not in memo: memo[path] = c.read(path)
        return memo[path]
    def save(path, payload): memo[path] = payload; pending.add(path)
    results = {d['date']: d for d in prior['days']}
    source = CachedSource(cache)
    request_lock, last_start = Lock(), [0.0]
    def pace(seconds):
        with request_lock:
            time.sleep(max(0, seconds - (time.monotonic() - last_start[0])))
            last_start[0] = time.monotonic()
    source.sleeper = pace
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        entry = {'date': day.isoformat(), 'sources': [], 'termsMethod': METHOD}
        try:
            market = historical_market(source, cache, day)
            sides = [rows for rows, _ in market]
            entry['sources'] = [audit for _, audit in market]
            if sides == [None, None]: entry.update(status='no_data', reason='官方認購及認售報表均無資料；不補零')
            elif any(side is None for side in sides): raise ValueError('Only one side of the market report is available')
            else:
                quotes = quotes_for_day(day.isoformat(), sides[0] + sides[1], plans)
                reasons = Counter(exclusion(r) for r in quotes)
                entry.update(status='imported', warrants=len(quotes), eligible=reasons.pop(None, 0), excluded=dict(reasons),
                             reason='僅納入有生效公告及已保存條款可核對的權證；不是當日全市場條款快照')
        except Exception as exc: entry.update(status='error', reason=str(exc)[:220])
        if entry['status'] == 'imported':
            feeds = {key: {'status': 'ok', 'date': day.isoformat()} for key in ('twse-warrants', 'twse-volume', 'twse-volume-put')}
            c.archive(output, quotes, feeds, now, today, read, save)
            c.collect_spreads(output, quotes, feeds, now, today, read, save)
        results[day.isoformat()] = entry
        print(json.dumps({k: v for k, v in entry.items() if k not in ('sources', 'excluded')}, ensure_ascii=False), flush=True)
    days = [results[d] for d in sorted(results)]
    report = {**prior, 'start': days[0]['date'], 'end': days[-1]['date'], 'checkedAt': now, 'days': days,
              'status': 'partial' if any(d['status'] == 'error' for d in days) else 'complete',
              'importedDates': [d['date'] for d in days if d['status'] == 'imported'],
              'termsBasis': '最近一個月採各日官方條款；較早區段僅採 MOPS 調整公告，自生效日起至下一次調整前套用，並與已保存條款交叉核對',
              'extension': {'method': METHOD, 'start': start.isoformat(), 'end': end.isoformat(), 'checkedAt': now,
                            'matchedIssuances': len(plans), 'excluded': excluded,
                            'sources': [adjustment_audit, reset_audit], 'anchors': anchors,
                            'limitations': ['僅覆蓋有調整公告且在已保存條款出現的發行批次，存在存續樣本選擇偏差，非全市場歷史',
                                            '公告可事後修訂；生效日期不等於當時可得時間，不屬即時樣本外績效',
                                            '不向首次生效公告之前回填；重設、條款不符、名稱不符或無成交均不推估',
                                            '標的價格僅採同日報表明列且代碼吻合者；上櫃權證及券商分點歷史未補入']}}
    save(output / 'warrant-backfill.json', report)
    latest = c.combine_warrants(current)
    c.archive(output, latest, current, now, today, read, save)
    c.collect_spreads(output, latest, current, now, today, read, save)
    c.collect_focus(output, latest, current, now, today, read, save)
    for path in sorted(pending): c.save(path, memo[path])
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--end', type=date.fromisoformat, required=True)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    today = now.astimezone(timezone(timedelta(hours=8))).date()
    result = backfill_extended(args.output, args.cache, args.start, args.end, today, now.isoformat(timespec='seconds'))
    print(json.dumps({'status': result['status'], 'importedDays': len(result['importedDates'])}), flush=True)
    raise SystemExit(1 if result['status'] != 'complete' else 0)
