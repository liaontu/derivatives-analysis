"""One-month TWSE backfill, using dated official terms and both volume reports.

Run against a separate copy of the published data, review, then publish. Raw
responses stay in a local resumable cache; no current snapshot is overwritten.
"""
import argparse
import calendar
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
import time
from urllib.parse import urlencode
import urllib.request

try:
    from . import collect as c
    from .warrant_spreads import exclusion
except ImportError:
    import collect as c
    from warrant_spreads import exclusion

SOURCES = ('twse-warrants', 'twse-volume', 'twse-volume-put')
NO_DATA = {'很抱歉，沒有符合條件的資料!', '很抱歉，沒有符合條件的資料！', '查無資料'}


def month_start(today):
    last = today.replace(day=1) - timedelta(days=1)
    return date(last.year, last.month, min(today.day, calendar.monthrange(last.year, last.month)[1]))


def requested_days(start, end, today):
    if not month_start(today) <= start <= end < today:
        raise ValueError('Only the previous calendar month through yesterday may be requested')
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def source_url(name, day):
    params = {'response': 'json', 'date': day.strftime('%Y%m%d')}
    if name == 'twse-warrants':
        path = 'stock/warrantStock'
    else:
        path = 'afterTrading/MI_INDEX'
        params['type'] = '0999' if name == 'twse-volume' else '0999P'
    return 'https://www.twse.com.tw/rwd/zh/' + path + '?' + urlencode(params)


def validate_report(name, day, raw):
    if not isinstance(raw, dict):
        raise ValueError('Expected official JSON report')
    if str(raw.get('stat', '')).strip() in NO_DATA:
        return None
    if raw.get('stat') != 'OK':
        raise ValueError('Unexpected source status: ' + str(raw.get('stat'))[:140])
    if c.iso_date(raw.get('date')) != day.isoformat():
        raise ValueError('Returned report date differs from requested date')
    rows = c.normalize(name, raw)
    if not rows and name in ('twse-volume', 'twse-volume-put'):
        # normalize already checked the dated, named table's schema. An empty
        # official table is unavailable, not a day of fabricated zero volume.
        return None
    if not rows or any(r.get('date') != day.isoformat() or not r.get('code') for r in rows):
        raise ValueError('Empty report or missing record date/code')
    if len({r['code'] for r in rows}) != len(rows):
        raise ValueError('Duplicate security codes in source report')
    field = 'price' if name == 'twse-warrants' else 'volume'
    if not any(r.get(field) is not None for r in rows):
        if name == 'twse-warrants':
            return None  # Holiday-like full terms tables can have no prices at all.
        raise ValueError('All prices/volumes are missing')
    return rows


def fetch_public(url):
    # One attempt per run. Errors can be retried from the cache without
    # re-downloading successful reports or repeatedly hitting denied requests.
    request = urllib.request.Request(url, headers={'User-Agent': 'DerivativeInsight/2.1 (public history research)',
                                                   'Accept-Encoding': 'gzip'})
    with urllib.request.urlopen(request, timeout=40) as response:
        stream = gzip.GzipFile(fileobj=response) if response.headers.get('Content-Encoding') == 'gzip' else response
        raw = stream.read(40_000_001)
    if len(raw) > 40_000_000:
        raise ValueError('Response exceeds size limit')
    return c.decode(raw)


class CachedSource:
    def __init__(self, directory, fetcher=fetch_public, sleeper=time.sleep):
        self.directory, self.fetcher, self.sleeper = directory, fetcher, sleeper

    def get(self, name, day):
        path = self.directory / f'{name}-{day.isoformat()}.json.gz'
        url = source_url(name, day)
        if path.exists():
            document = json.loads(gzip.decompress(path.read_bytes()))
            if document.get('url') != url:
                raise ValueError('Cache source URL differs')
            raw = document['raw']
        else:
            self.sleeper(2)
            raw = self.fetcher(url)
            validate_report(name, day, raw)
            document = {'url': url, 'retrievedAt': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'raw': raw}
            self.directory.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(json.dumps(document, ensure_ascii=False).encode(), mtime=0))
        rows = validate_report(name, day, raw)
        digest = sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return rows, {'source': name, 'url': url, 'retrievedAt': document['retrievedAt'], 'sha256': digest,
                      'rows': len(rows or []), 'status': 'ok' if rows is not None else 'no_data',
                      'reason': ('官方報表存在，但全部權證收盤價缺值；無有效行情，未認定為休市' if name == 'twse-warrants'
                                 else '官方成交報表為空；未推定成交為零或認定為休市') if rows is None and raw.get('stat') == 'OK' else None}


def backfill(output, cache, start, end, today, now, source=None):
    days = requested_days(start, end, today)
    source = source or CachedSource(cache)
    # Keep these snapshots untouched, and replay their latest dates at the end.
    current = c.read_feeds(output)
    if not current.get('twse-warrants', {}).get('rows'):
        raise ValueError('Seed output with the existing published data first')
    # Only flush derived files after the entire pass and latest-snapshot replay.
    # This avoids rewriting hundreds of gzip files for each historical day.
    memo, pending = {}, set()
    def read(path):
        if path not in memo:
            memo[path] = c.read(path)
        return memo[path]
    def save(path, payload):
        memo[path] = payload
        pending.add(path)
    results = []
    for day in days:
        result = {'date': day.isoformat(), 'sources': []}
        try:
            feeds = {}
            for name in SOURCES:
                rows, audit = source.get(name, day)
                result['sources'].append(audit)
                if rows is None:
                    if name == 'twse-warrants':
                        result.update(status='no_data', reason=audit.get('reason') or '官方無權證報表，可能為休市日；未推定成交為零')
                        break
                    raise ValueError('Terms available but volume report missing: ' + name)
                feeds[name] = {'date': day.isoformat(), 'status': 'ok', 'rows': rows}
            else:
                quotes = c.combine_warrants(feeds)
                reasons = Counter(exclusion(r) for r in quotes)
                result.update(status='imported', warrants=len(quotes), eligible=reasons.pop(None, 0), excluded=dict(reasons))
                # Historical source-date validation is deliberately separate
                # from the daily collector's 14-day freshness gate.
        except Exception as exc:
            result.update(status='error', reason=str(exc)[:220])
        if result['status'] == 'imported':
            # A computation failure aborts the staged import; never label a
            # partially applied day successful or flush its intermediate state.
            c.archive(output, quotes, feeds, now, today, read, save)
            c.collect_spreads(output, quotes, feeds, now, today, read, save)
        results.append(result)
        print(json.dumps({k: v for k, v in result.items() if k not in ('sources', 'excluded')}, ensure_ascii=True), flush=True)
    previous = read(output / 'warrant-backfill.json') or {}
    by_day = {r['date']: r for r in previous.get('days', [])}
    by_day.update({r['date']: r for r in results})
    results = [by_day[d] for d in sorted(by_day)]
    imported = [r['date'] for r in results if r['status'] == 'imported']
    report = {**previous, 'schemaVersion': 1, 'market': 'TWSE', 'start': results[0]['date'], 'end': results[-1]['date'],
              'checkedAt': now, 'status': 'partial' if any(r['status'] == 'error' for r in results) else 'complete',
              'importedDates': imported, 'days': results,
              'termsBasis': previous.get('termsBasis') if previous.get('extension') else
                            '各歷史報表所列條款；官方註明基本資訊由發行人於前一營業日申報，未另驗證盤中異動',
              'scope': '上市認購／認售權證；上櫃與分點排行未補抓；無資料不補零'}
    save(output / 'warrant-backfill.json', report)
    latest = c.combine_warrants(current)
    c.archive(output, latest, current, now, today, read, save)
    c.collect_spreads(output, latest, current, now, today, read, save)
    c.collect_focus(output, latest, current, now, today, read, save)
    for path in sorted(pending):
        c.save(path, memo[path])
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Separate seeded review directory')
    parser.add_argument('--cache', type=Path, required=True, help='Local cache; do not publish raw responses')
    parser.add_argument('--start', type=date.fromisoformat)
    parser.add_argument('--end', type=date.fromisoformat)
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    today = now.astimezone(timezone(timedelta(hours=8))).date()
    result = backfill(args.output, args.cache, args.start or month_start(today), args.end or today - timedelta(days=1), today, now.isoformat(timespec='seconds'))
    print(json.dumps({'status': result['status'], 'importedDates': result['importedDates']}), flush=True)
    raise SystemExit(1 if result['status'] != 'complete' else 0)
