"""Free TAIFEX daily option history, exact-series changes and historical event tests.

Only regular-session rows are archived. No accounts, orders, paid API or model calls.
Expired contracts remain in the archive; historical tests never use today's universe.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

try:
    from .collect import number, iso_date, cp
    from .publication import save_text
    from .trading_calendar import load_calendar
except ImportError:
    from collect import number, iso_date, cp
    from publication import save_text
    from trading_calendar import load_calendar

SOURCE = 'https://www.taifex.com.tw/cht/3/dlOptDailyMarketView'
DOWNLOAD = 'https://www.taifex.com.tw/cht/3/dlOptDataDown'
FIELDS = ['strike', 'cp', 'last', 'volume', 'oi', 'settlement', 'bid', 'ask']
VERSION = 'option-events-v1'
RULES = {
    'volume': '放量：成交量至少 100 口且達前五交易日均量兩倍',
    'momentum': '價量增倉：放量、成交價漲幅至少 5%、未平倉增加',
    'pullback': '價跌增倉：放量、成交價跌幅至少 5%、未平倉增加',
}


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def save(path, value):
    save_text(path, json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False))


def positive(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value > 0 else None


def nonnegative(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def day_range(start, end):
    cursor = date.fromisoformat(start)
    while cursor.isoformat() <= end:
        yield cursor.isoformat()
        cursor += timedelta(days=1)


def parse_csv(raw, start, end):
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp950')
    reader = csv.DictReader(io.StringIO(text))
    required = {'交易日期', '契約', '到期月份(週別)', '履約價', '買賣權', '收盤價', '成交量',
                '結算價', '未沖銷契約數', '最後最佳買價', '最後最佳賣價', '交易時段'}
    if not required <= set(reader.fieldnames or []):
        raise ValueError('HISTORY_COLUMNS_CHANGED')
    groups, dates, identities = {}, set(), set()
    count = 0
    for row in reader:
        count += 1
        if None in row and row[None]:
            raise ValueError('HISTORY_ROW_WIDTH')
        day = iso_date(row['交易日期'])
        if day is None or not start <= day <= end:
            raise ValueError('HISTORY_DATE_MISMATCH')
        if row['交易時段'].strip() not in ('一般', '盤後'):
            raise ValueError('UNKNOWN_SESSION')
        if row['交易時段'].strip() != '一般':
            continue
        contract, month = row['契約'].strip(), row['到期月份(週別)'].strip()
        strike, side = positive(number(row['履約價'])), cp(row['買賣權'])
        if not (re.fullmatch(r'[A-Z0-9]{2,5}', contract) and
                re.fullmatch(r'20\d{2}(?:0[1-9]|1[0-2])(?:[WF][1-5])?', month) and strike and side):
            raise ValueError('HISTORY_IDENTITY_INVALID')
        identity = (day, contract, month, strike, side)
        if identity in identities:
            raise ValueError('HISTORY_DUPLICATE_SERIES')
        identities.add(identity)
        volume, oi = nonnegative(number(row['成交量'])), nonnegative(number(row['未沖銷契約數']))
        if volume is None:
            raise ValueError('HISTORY_VOLUME_INVALID')
        last = positive(number(row['收盤價'])) if volume > 0 else None
        bid, ask = positive(number(row['最後最佳買價'])), positive(number(row['最後最佳賣價']))
        if bid and ask and bid > ask:
            bid = ask = None
        group = groups.setdefault((contract, month), {'contract': contract, 'month': month, 'days': {}})
        group['days'].setdefault(day, []).append([strike, side, last, volume, oi,
                                                 nonnegative(number(row['結算價'])), bid, ask])
        dates.add(day)
    if not count or not dates:
        raise ValueError('HISTORY_EMPTY')
    txo_dates = {d for (c, _), group in groups.items() if c == 'TXO' for d in group['days']}
    if txo_dates != dates:
        raise ValueError('HISTORY_MARKET_COVERAGE_INVALID')
    for group in groups.values():
        for rows in group['days'].values():
            rows.sort(key=lambda r: (r[0], r[1]))
    return groups, sorted(dates)


def download(start, end):
    params = {'down_type': '1', 'commodity_id': 'all', 'commodity_id2': '',
              'queryStartDate': start.replace('-', '/'), 'queryEndDate': end.replace('-', '/')}
    request = urllib.request.Request(DOWNLOAD, data=urllib.parse.urlencode(params).encode(), headers={
        'User-Agent': 'DerivativeInsight/3.0 (free daily market research)', 'Referer': SOURCE})
    with urllib.request.urlopen(request, timeout=90) as response:
        raw = response.read(40_000_001)
    if len(raw) > 40_000_000:
        raise ValueError('HISTORY_RESPONSE_TOO_LARGE')
    return raw


def import_groups(folder, groups, dates):
    """A successful all-contract report replaces entire dates, including removed rows."""
    by_path = {folder / c / (m + '.json'): g for (c, m), g in groups.items()}
    touched = set(dates)
    for path in set(folder.glob('*/*.json')) | set(by_path):
        old = read(path, {'contract': path.parent.name, 'month': path.stem, 'days': {}})
        old['fields'] = FIELDS
        old['days'] = {d: rows for d, rows in old['days'].items() if d not in touched}
        old['days'].update(by_path.get(path, {}).get('days', {}))
        old['days'] = dict(sorted(old['days'].items()))
        save(path, old)


def changes(row, old, baseline):
    price = row[2]
    price_delta = price - old[2] if old and price is not None and old[2] is not None else None
    price_pct = 100 * price_delta / old[2] if price_delta is not None and old[2] > 0 else None
    volume_delta = row[3] - old[3] if old and row[3] is not None and old[3] is not None else None
    volume_pct = 100 * volume_delta / old[3] if volume_delta is not None and old[3] > 0 else None
    oi_delta = row[4] - old[4] if old and row[4] is not None and old[4] is not None else None
    mean = sum(r[3] for r in baseline) / 5 if len(baseline) == 5 and all(r and r[3] is not None for r in baseline) else None
    multiple = row[3] / mean if mean and mean > 0 and row[3] is not None else None
    return {'priceChange': price_delta, 'pricePct': price_pct, 'volumeChange': volume_delta,
            'volumePct': volume_pct, 'oiChange': oi_delta, 'volumeMultiple': multiple}


def event_test(group, sessions, split):
    """Signals at D, baseline at D+1 close, exit D+6 close (five sessions after entry).

    Missing/expired legs are counted, never replaced by the next available quote.
    Distinct-series overlaps remain; results are event returns, not portfolio P&L.
    """
    series = defaultdict(dict)
    for day, rows in group['days'].items():
        for row in rows:
            series[(row[0], row[1])][day] = row
    events = []
    positions = {d: i for i, d in enumerate(sessions)}
    for (strike, side), observations in series.items():
        last_signal = {}
        for day in sorted(observations):
            i = positions[day]
            if i < 5:
                continue
            row = observations[day]
            values = changes(row, observations.get(sessions[i-1]),
                             [observations.get(d) for d in sessions[i-5:i]])
            if row[2] is None or row[3] is None or row[3] < 100 or (values['volumeMultiple'] or 0) < 2:
                continue
            rules = ['volume']
            if values['oiChange'] is not None and values['oiChange'] > 0 and values['pricePct'] is not None:
                if values['pricePct'] >= 5:
                    rules.append('momentum')
                if values['pricePct'] <= -5:
                    rules.append('pullback')
            for rule in rules:
                if i - last_signal.get(rule, -100) <= 6:
                    continue
                last_signal[rule] = i
                entry_day = sessions[i+1] if i+1 < len(sessions) else None
                exit_day = sessions[i+6] if i+6 < len(sessions) else None
                entry, finish = observations.get(entry_day), observations.get(exit_day)
                status = 'pending' if entry_day is None else 'missing-entry' if not entry or entry[2] is None else \
                    'pending' if exit_day is None else 'missing-exit' if not finish or finish[2] is None else 'evaluated'
                pct = (finish[2] / entry[2] - 1) * 100 if status == 'evaluated' else None
                segment = 'evaluation' if day >= split else 'purged' if exit_day is None or exit_day >= split else 'development'
                events.append({'rule': rule, 'date': day, 'month': group['month'], 'strike': strike, 'cp': side,
                               'entryDate': entry_day, 'exitDate': exit_day, 'entry': entry[2] if entry else None,
                               'exit': finish[2] if finish else None, 'status': status, 'segment': segment,
                               'returnPct': pct, 'pricePct': values['pricePct'], 'volumeMultiple': values['volumeMultiple'],
                               'oiChange': values['oiChange']})
    return events


def stats(events):
    counts = Counter(e['status'] for e in events)
    returns = sorted(e['returnPct'] for e in events if e['status'] == 'evaluated')
    n = len(returns)
    return {'signals': len(events), 'counts': dict(counts), 'evaluated': n,
            'mean': sum(returns)/n if n else None,
            'median': (returns[(n-1)//2] + returns[n//2])/2 if n else None,
            'positivePct': 100*sum(v > 0 for v in returns)/n if n else None,
            'meanCost1': sum(v-1 for v in returns)/n if n else None,
            'meanCost3': sum(v-3 for v in returns)/n if n else None,
            'distinctSignalDates': len({e['date'] for e in events if e['status'] == 'evaluated'})}


def rebuild(output, start, end, receipts, failures):
    folder = output / 'option-history'
    calendar = load_calendar(output)
    expected = [d for d in day_range(start, end) if calendar.is_open(d)]
    groups = [read(p) for p in sorted(folder.glob('*/*.json'))]
    dates = sorted({d for g in groups for d in g['days']})
    if not dates:
        raise ValueError('NO_ARCHIVED_OPTION_HISTORY')
    # Include official observed dates even when a calendar exception is not yet in the seed.
    sessions = sorted(set(expected) | set(dates))
    split = sessions[int(len(sessions)*.7)]
    contracts, by_contract = {}, defaultdict(list)
    for group in groups:
        if not group['days']:
            continue
        c = group['contract']
        days = sorted(group['days'])
        latest = group['days'][days[-1]]
        contracts.setdefault(c, []).append({'month': group['month'], 'dates': days,
                                           'rows': sum(len(r) for r in group['days'].values()),
                                           'latestVolume': sum(r[3] or 0 for r in latest)})
        by_contract[c].extend(event_test(group, sessions, split))
    summaries = {}
    for contract, events in by_contract.items():
        buckets = []
        for rule in RULES:
            for side in ('C', 'P'):
                for segment in ('development', 'evaluation', 'purged'):
                    matching = [e for e in events if e['rule'] == rule and e['cp'] == side and e['segment'] == segment]
                    buckets.append({'rule': rule, 'cp': side, 'segment': segment, **stats(matching)})
        summaries[contract] = buckets
        save(output / 'option-backtest' / (contract + '.json'), {
            'version': VERSION, 'splitDate': split, 'summary': buckets,
            'events': sorted(events, key=lambda e: (e['date'], e['month'], e['strike'], e['cp'], e['rule']), reverse=True)})
    missing = sorted(set(expected)-set(dates))
    index = {'schemaVersion': 1, 'version': VERSION, 'source': SOURCE, 'download': DOWNLOAD,
             'start': dates[0], 'latest': dates[-1], 'requestedStart': start, 'requestedEnd': end,
             'dates': dates, 'sessions': sessions, 'missingDates': missing, 'contracts': contracts,
             'rows': sum(e['rows'] for entries in contracts.values() for e in entries),
             'status': 'partial' if failures or missing else 'ok', 'failures': failures,
             'receipts': receipts, 'fields': FIELDS, 'splitDate': split, 'rules': RULES,
             'summary': summaries, 'historyType': 'reconstructed', 'liveOos': 0}
    save(output / 'option-history.json', index)
    return index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('data/derivatives'))
    parser.add_argument('--start', help='Initial backfill start; existing history is retained')
    parser.add_argument('--end', help='Defaults to latest official options snapshot date')
    parser.add_argument('--cache', type=Path, help='Local CSV cache for reproducible initial backfill')
    parser.add_argument('--rebuild-only', action='store_true')
    parser.add_argument('--refresh-latest', action='store_true', help='Recheck latest source even if fetched today')
    parser.add_argument('--max-windows', type=int, default=2, help='Bounded daily recovery; zero means all windows')
    args = parser.parse_args()
    output = args.data
    previous = read(output / 'option-history.json', {})
    end = args.end or read(output / 'options.json')['date']
    start = min(args.start or previous.get('requestedStart', end), previous.get('requestedStart', end))
    date.fromisoformat(start); date.fromisoformat(end)
    if start > end:
        raise ValueError('INVALID_HISTORY_RANGE')
    receipts = previous.get('receipts', [])
    failures = []
    covered = set(previous.get('dates', []))
    calendar = load_calendar(output)
    missing = [d for d in day_range(start, end) if calendar.is_open(d) and d not in covered]
    windows = []
    while missing:
        a = missing[0]
        b = min((date.fromisoformat(a)+timedelta(days=13)).isoformat(), end)
        windows.append((a, b))
        missing = [d for d in missing if d > b]
    # Refresh most recent report to capture source revisions, at most once per Taipei date.
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    manual = args.refresh_latest or os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
    if not windows and (manual or not any(r['end'] == end and r['fetchedAt'][:10] == today for r in receipts)):
        windows = [(end, end)]
    if args.max_windows:
        windows = windows[:args.max_windows]
    if not args.rebuild_only:
        for a, b in windows:
            try:
                cache = args.cache / (a + '_' + b + '.csv') if args.cache else None
                raw = cache.read_bytes() if cache and cache.exists() else download(a, b)
                groups, dates = parse_csv(raw, a, b)
                if cache and not cache.exists():
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_bytes(raw)
                import_groups(output / 'option-history', groups, dates)
                receipt = {'start': a, 'end': b, 'dates': dates, 'sha256': hashlib.sha256(raw).hexdigest(),
                           'fetchedAt': datetime.now(timezone(timedelta(hours=8))).isoformat(),
                           'rows': sum(len(rows) for g in groups.values() for rows in g['days'].values())}
                # Content hash records revisions; identical refreshes do not multiply receipts.
                receipts = [r for r in receipts if (r['start'], r['end'], r['sha256']) != (a, b, receipt['sha256'])]
                receipts.append(receipt)
                print(json.dumps({'start': a, 'end': b, 'days': len(dates), 'rows': receipt['rows']}), flush=True)
                time.sleep(2)
            except Exception as exc:
                failures.append({'start': a, 'end': b, 'reason': type(exc).__name__ + ': ' + str(exc)[:160]})
                print(json.dumps(failures[-1]), flush=True)
                # Respect access/rate-limit failures; do not rotate endpoints or retry aggressively.
                if getattr(exc, 'code', None) in (401, 403, 429):
                    break
    result = rebuild(output, start, end, receipts, failures)
    print(json.dumps({k: result[k] for k in ('start', 'latest', 'rows', 'status', 'missingDates')}), flush=True)
    if failures or result['missingDates']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
