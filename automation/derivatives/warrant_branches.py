"""Bounded public warrant rankings. Never implies full branch coverage."""
from __future__ import annotations
import argparse
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import json
import hashlib
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

try:
    from .collect import read, save, stamp, WRITTEN
    from .trading_calendar import default_calendar, calendar_for_run, fetch_json, CalendarUnavailable
except ImportError:
    from collect import read, save, stamp, WRITTEN
    from trading_calendar import default_calendar, calendar_for_run, fetch_json, CalendarUnavailable

ORIGIN = 'https://fubon-ebrokerdj.fbs.com.tw'
AGENT = 'DerivativesAnalysis-WarrantRankings/1.0'
CODE = re.compile(r'\d{5}[0-9A-Z]')
SCHEDULE = '預定台北 20:13、22:13、23:13 檢查；可能延遲或漏跑'


def source_url(code):
    if not CODE.fullmatch(code):
        raise ValueError('Invalid warrant code')
    return f'{ORIGIN}/z/zc/zco/zco_{code}_1.djhtm'


def fetch(url):
    if not url.startswith(ORIGIN + '/'):
        raise ValueError('Unexpected source host')
    with urlopen(Request(url, headers={'User-Agent': AGENT}), timeout=15) as response:
        if urlparse(response.url).hostname != urlparse(ORIGIN).hostname:
            raise ValueError('Unexpected redirect')
        blob = response.read(2_000_001)
        if len(blob) > 2_000_000:
            raise ValueError('Response too large')
        return blob.decode(response.headers.get_content_charset() or 'cp950', errors='strict')


def fetch_with_retry(url, fetcher, sleeper, interval):
    for attempt in range(2):
        try:
            return fetcher(url)
        except (URLError, TimeoutError) as exc:
            if isinstance(exc, HTTPError) and exc.code not in (408, 500, 502, 503, 504):
                raise
            if attempt:
                raise
            sleeper(max(3, interval))


class Table(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.text, self.row, self.cell, self.skip = [], [], None, None, 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        if self.skip:
            return
        if tag == 'tr':
            self.row = []
        elif tag in ('td', 'th'):
            self.cell = {'text': '', 'href': None}
        elif tag == 'a' and self.cell is not None:
            self.cell['href'] = dict(attrs).get('href')

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)
            if self.cell is not None:
                self.cell['text'] += data

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip - 1)
        if self.skip:
            return
        if tag in ('td', 'th') and self.cell is not None:
            if self.row is not None:
                self.row.append(self.cell)
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def numeric(text):
    try:
        value = Decimal(text.strip().replace(',', '').removesuffix('%'))
    except InvalidOperation:
        raise ValueError('Missing or invalid quantity') from None
    if not value.is_finite():
        raise ValueError('Non-finite quantity')
    return value


def parse_page(html, code):
    source_url(code)
    parser = Table()
    parser.feed(html)
    plain = ' '.join(parser.text)
    if f'({code})' not in plain or not all(t in plain for t in ('券商分點', '買超券商', '賣超券商')):
        raise ValueError('Wrong warrant or changed source page')
    if not re.search(r'單位[：:]\s*張', plain):
        raise ValueError('Unknown source unit')
    match = re.search(r'最後更新日[：:]\s*(\d{4}/\d{2}/\d{2})', plain)
    if not match:
        raise ValueError('Missing source date')
    day = date.fromisoformat(match[1].replace('/', '-')).isoformat()
    rows, counts = [], {'buy': 0, 'sell': 0}
    for cells in parser.rows:
        if not any('zco0.djhtm?' in (c['href'] or '') for c in cells):
            continue
        if len(cells) != 10:
            raise ValueError('Changed ranking columns')
        for offset, side in ((0, 'buy'), (5, 'sell')):
            cell = cells[offset]
            if not cell['href']:
                if any(c['text'].strip() for c in cells[offset:offset + 5]):
                    raise ValueError('Missing branch link')
                continue
            params = parse_qs(urlparse(cell['href']).query)
            if params.get('a') != [code]:
                raise ValueError('Mismatched warrant link')
            branch = params.get('b', [''])[0]
            if re.fullmatch(r'(?:[0-9a-fA-F]{4}){4}', branch):
                branch = bytes.fromhex(branch).decode('utf-16-be')
            name = cell['text'].strip()
            if not re.fullmatch(r'[A-Za-z0-9]{4}', branch) or not name:
                raise ValueError('Invalid branch identity')
            buy, sell, magnitude, share = [numeric(c['text']) for c in cells[offset + 1:offset + 5]]
            net = magnitude if side == 'buy' else -magnitude
            difference = buy - sell - net
            tolerance = Decimal(1) if all(v == v.to_integral_value() for v in (buy, sell, magnitude)) else Decimal('0.001')
            if min(buy, sell, magnitude, share) < 0 or abs(difference) > tolerance:
                raise ValueError('Inconsistent ranking quantities')
            counts[side] += 1
            rows.append({'branchCode': branch, 'branchName': name, 'side': side, 'rank': counts[side],
                         'buy': float(buy), 'sell': float(sell), 'net': float(net), 'share': float(share),
                         'displayRoundingDifference': float(difference)})
    if not rows or max(counts.values()) > 15 or len({r['branchCode'] for r in rows}) != len(rows):
        raise ValueError('Empty, duplicate or unexpected rankings')
    for side in counts:
        magnitudes = [abs(r['net']) for r in rows if r['side'] == side]
        if magnitudes != sorted(magnitudes, reverse=True):
            raise ValueError('Unexpected ranking order')
    return {'date': day, 'rows': rows, 'completeBranchCoverage': False, 'missingBranchMeansZero': False}


class CatalogUnavailable(ValueError):
    def __init__(self, latest, age):
        super().__init__('Warrant catalog unavailable or stale')
        self.latest = latest
        self.age = age
        self.stale = age is not None and age > 4


def select_warrants(catalog, config, today, calendar=None):
    calendar = calendar or default_calendar()
    calendar.is_open(today)  # A missing year must never fall back to weekdays.
    limit, watch = config['topCount'], config['watchlist']
    if type(limit) is not int or not 0 <= limit <= 300 or not isinstance(watch, list) or len(watch) > 20 or len(set(watch)) != len(watch) or any(not isinstance(c, str) or not CODE.fullmatch(c) for c in watch):
        raise ValueError('Invalid bounded warrant configuration')
    by_code = {}
    for r in catalog:
        code = r.get('code', '')
        if CODE.fullmatch(code) and r.get('market') in ('TWSE', 'TPEX') and r.get('cp') in ('C', 'P'):
            if code in by_code:
                raise ValueError('Duplicate warrant catalog code')
            by_code[code] = r
    eligible = []
    for r in by_code.values():
        day = date.fromisoformat(r['date'])
        last = r.get('lastTrade') or r.get('expiry')
        if calendar.fresh(day, today) and (not last or last >= today.isoformat()) and (r.get('volume') or 0) > 0:
            eligible.append(r)
    if not eligible:
        latest = max((r['date'] for r in by_code.values()), default=None)
        raise CatalogUnavailable(latest, calendar.age(latest, today) if latest else None)
    selected = sorted(eligible, key=lambda r: (-r['volume'], r['code']))[:limit]
    codes = {r['code'] for r in selected}
    issues = []
    for code in watch:
        r = by_code.get(code)
        last = (r.get('lastTrade') or r.get('expiry')) if r else None
        if not r or (last and last < today.isoformat()):
            issues.append({'code': code, 'reason': '權證已到期、停止交易或不在官方權證清單'})
        elif not calendar.fresh(r.get('date'), today):
            issues.append({'code': code, 'reason': '固定清單資料超過四個交易日、日期無效或日曆未驗證'})
        elif code not in codes:
            selected.append(r)
            codes.add(code)
    return selected, issues


def update_warrant(quote, old, html, now, today, calendar=None):
    current = parse_page(html, quote['code'])
    day = date.fromisoformat(current['date'])
    if not (calendar or default_calendar()).fresh(day, today) or (old and old.get('date') and current['date'] < old['date']):
        raise ValueError('Invalid or regressed source date')
    buy = sum(Decimal(str(r['buy'])) for r in current['rows'])
    sell = sum(Decimal(str(r['sell'])) for r in current['rows'])
    volume = quote.get('volume') if quote['date'] == current['date'] else None
    current.update({'buyTotal': float(buy), 'sellTotal': float(sell), 'quoteDate': quote['date'], 'quoteVolume': volume,
                    'buyGap': float(Decimal(str(volume)) - buy) if volume is not None else None,
                    'sellGap': float(Decimal(str(volume)) - sell) if volume is not None else None})
    history = {d['date']: d for d in (old or {}).get('history', [])}
    history[current['date']] = current
    payload = {'code': quote['code'], 'date': current['date'], 'sourceUrl': source_url(quote['code']),
               'unit': '張', 'completeBranchCoverage': False, 'missingBranchMeansZero': False,
               'status': 'ok', 'fetchedAt': now, 'history': [history[d] for d in sorted(history, reverse=True)[:30]]}
    # Identical rankings keep their first fetch time so the published file stays byte-stable.
    if old and all(old.get(k) == payload[k] for k in ('code', 'date', 'history')):
        payload['fetchedAt'] = old.get('fetchedAt') or now
    return payload


def continuation_plan(catalog, config, today, prior, now, calendar=None):
    calendar = calendar or default_calendar()
    current, issues = select_warrants(catalog, config, today, calendar)
    by_code = {q['code']: q for q in catalog if CODE.fullmatch(q.get('code', '')) and q.get('market') in ('TWSE', 'TPEX') and q.get('cp') in ('C', 'P')}
    pending = []
    for code in prior.get('remaining', []):
        q = by_code.get(code)
        if q and calendar.fresh(q.get('date'), today) and (q.get('lastTrade') or q.get('expiry') or '9999') >= today.isoformat():
            pending.append(code)
    # Carry-over uses at most half the normal quota; today's hot list still gets a turn.
    selected = [q for q in current if q['code'] in config['watchlist']]
    capacity = len(current)
    carry_limit = min(max(1, config['topCount']//2), max(0, capacity-len(selected))) if config['topCount'] else 0
    carry = [by_code[c] for c in pending if prior.get('failures', {}).get(c, {}).get('retryAfter', '') <= now]
    for q in carry[:carry_limit] + current:
        if len(selected) < capacity and all(x['code'] != q['code'] for x in selected):
            selected.append(q)
    return selected, issues, pending if capacity else []


def collect(output, config, fetcher=fetch, now=None, delay=2, sleeper=time.sleep, budget_seconds=900, clock=time.monotonic, calendar_fetcher=None):
    now = now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    now = datetime.fromisoformat(now).astimezone(timezone.utc).isoformat(timespec='seconds')
    today = datetime.fromisoformat(now).astimezone(timezone(timedelta(hours=8))).date()
    started = clock()
    WRITTEN.clear()
    calendar, calendar_status = calendar_for_run(output, today.year, save, calendar_fetcher)
    index_path = output / 'warrant-branches.json'
    old_index = read(index_path) or {'items': []}
    errors = []
    try:
        catalog = (read(output / 'warrants.json') or {})['rows']
        prior = old_index.get('progress') or {}
        selected, issues, pending = continuation_plan(catalog, config, today, prior, now, calendar)
        robots = RobotFileParser()
        try:
            robots.parse(fetcher(ORIGIN + '/robots.txt').splitlines())
        except HTTPError as exc:
            if exc.code != 404:
                raise
            robots.parse([])
        interval = max(delay, robots.crawl_delay(AGENT) or 0)
        if interval > 30:
            raise ValueError('Source crawl delay exceeds job budget')
    except Exception as exc:
        gate = ({'reason': 'catalog_stale' if exc.stale else 'catalog_unavailable', 'catalogDate': exc.latest, 'maxAgeTradingDays': 4}
                if isinstance(exc, CatalogUnavailable) else {'reason': 'calendar_unavailable' if isinstance(exc, CalendarUnavailable) else 'catalog_or_policy_unavailable'})
        message = (f"官方權證清單資料日 {exc.latest} 已超過 4 個交易日；保留舊資料與續跑進度，等待新行情"
                   if isinstance(exc, CatalogUnavailable) and exc.stale else '來源政策或官方權證清單暫不可用；保留前次資料')
        if isinstance(exc, CalendarUnavailable):
            message = '交易日曆未驗證或年份未涵蓋；保留舊資料與續跑進度，暫停抓取'
        print(json.dumps({'collectionGate': gate}, ensure_ascii=False), flush=True)
        index = {**{k: v for k, v in old_index.items() if k not in ('updatedAt', 'checkedAt')},
                 'status': 'retained' if old_index['items'] else 'unavailable', 'error': message, 'collectionGate': gate, 'calendarStatus': calendar_status}
        save(index_path, stamp(index, old_index, now))
        return ['catalog_or_policy']
    basis = hashlib.sha256(json.dumps(selected, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    prior = old_index.get('progress') or {}
    completed = set(prior.get('completed', [])) if prior.get('basis') == basis else set()
    completed_for = dict(prior.get('completedFor', {}))
    failures = dict(prior.get('failures', {}))
    items, deferred = [], []
    stopped = False
    for quote in selected:
        code = quote['code']
        path = output / 'warrant-branches' / f'{code}.json'
        old = read(path)
        error = None
        try:
            if not robots.can_fetch(AGENT, source_url(code)):
                raise ValueError('Source disallows collection')
            version = {'date': quote['date'], 'volume': quote.get('volume')}
            if (code in completed or completed_for.get(code) == version) and old and old.get('status') == 'ok' and old.get('date') == quote['date']:
                payload = old
            else:
                if failures.get(code, {}).get('retryAfter', '') > now:
                    deferred.append(code)
                    raise TimeoutError('Repeated failures are cooling down')
                # Reserve both HTTP timeouts and retry delay before starting another item.
                if stopped or clock() - started + interval + 30 + max(3, interval) >= budget_seconds:
                    deferred.append(code)
                    raise TimeoutError('Collection paused for a bounded continuation')
                sleeper(interval)
                payload = update_warrant(quote, old, fetch_with_retry(source_url(code), fetcher, sleeper, interval), now, today, calendar)
                completed.add(code)
                completed_for[code] = version
                failures.pop(code, None)
        except Exception as exc:
            if isinstance(exc, HTTPError) and exc.code in (401, 403, 429):
                stopped = True
            print(json.dumps({'warrant': code, 'failureType': type(exc).__name__,
                              'httpStatus': exc.code if isinstance(exc, HTTPError) else None}), flush=True)
            errors.append(code)
            if code not in deferred:
                count = failures.get(code, {}).get('count', 0) + 1
                failures[code] = {'count': count}
                if count >= 3:
                    failures[code]['retryAfter'] = (datetime.fromisoformat(now)+timedelta(hours=24)).isoformat(timespec='seconds')
            error = ('時間預算或來源限制暫停；保留資料待續跑' if code in deferred else '來源暫不可用、日期異常或欄位改變')
            payload = {**(old or {'code': code, 'date': None, 'history': [], 'fetchedAt': None,
                                 'sourceUrl': source_url(code), 'completeBranchCoverage': False, 'missingBranchMeansZero': False}),
                       'status': 'retained' if old and old.get('history') else 'unavailable'}
        payload.pop('checkedAt', None)
        payload['error'] = error
        save(path, payload)
        items.append({**{k: quote.get(k) for k in ('code', 'name', 'market', 'cp', 'underlying', 'underlyingName', 'volume')},
                      'quoteDate': quote['date'], 'date': payload['date'], 'status': payload['status'], 'error': error,
                      'fetchedAt': payload['fetchedAt'], 'fixed': code in config['watchlist'],
                      'count': len(payload['history'][0]['rows']) if payload['history'] else 0})
        print(json.dumps({'warrant': code, 'date': payload['date'], 'status': payload['status']}, ensure_ascii=False), flush=True)
    remaining = list(dict.fromkeys([c for c in pending if c not in {q['code'] for q in selected}] + errors))
    # Keep the oldest backlog bounded and report overflow rather than growing forever.
    cap = max(1, 2*config['topCount']+len(config['watchlist']))
    if len(remaining) > cap:
        issues.append({'reason': '待續跑清單超過容量；保留最早未完成項目', 'overflow': len(remaining)-cap})
        remaining = remaining[:cap]
    index = {'schemaVersion': 3, 'status': 'partial' if remaining else 'ok', 'calendarStatus': calendar_status, 'maxAgeTradingDays': 4,
             'schedule': SCHEDULE, 'topCount': config['topCount'], 'watchlist': config['watchlist'],
             'issues': issues, 'items': sorted(items, key=lambda item: item['code']), 'errorCount': len(remaining), 'error': None,
             'completeBranchCoverage': False, 'missingBranchMeansZero': False}
    if remaining:
        relevant = {q['code'] for q in selected} | set(remaining)
        index['progress'] = {'basis': basis, 'completed': sorted(completed), 'remaining': remaining,
                             'completedFor': {c: v for c, v in completed_for.items() if c in relevant},
                             'failures': {c: v for c, v in failures.items() if c in remaining},
                             'pausedBySource': stopped, 'budgetSeconds': budget_seconds}
    save(index_path, stamp(index, old_index, now))
    return remaining


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path('data/derivatives'))
    parser.add_argument('--config', type=Path, default=Path('automation/derivatives/warrant-branches.json'))
    args = parser.parse_args()
    raise SystemExit(1 if collect(args.output, read(args.config), calendar_fetcher=fetch_json) else 0)
