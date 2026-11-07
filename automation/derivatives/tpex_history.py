"""Fill missing TPEx warrant quote days from free dated public reports.

Prices/volume/spot only. Historical contract terms and broker ranks are never
inferred from today's fields. Existing valid observations are not overwritten.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import argparse
import copy
import json
import math
from urllib.error import HTTPError

try:
    from .backfill_volume_reports import Sources
    from .daily_reports import save
    from .trading_calendar import load_calendar
    from .warrant_history import SAFE_CODE, trade_price
    from .warrant_spreads import positive
except ImportError:
    from backfill_volume_reports import Sources
    from daily_reports import save
    from trading_calendar import load_calendar
    from warrant_history import SAFE_CODE, trade_price
    from warrant_spreads import positive


def read(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def sessions(calendar, start, end):
    result = []
    while start <= end:
        if calendar.is_open(start):
            result.append(start.isoformat())
        start += timedelta(days=1)
    return result


def load_histories(output):
    index = read(output / 'warrant-history.json')
    bank = {}
    for item in index.get('items', []):
        code = item['code']
        if not SAFE_CODE.fullmatch(code):
            raise ValueError('Unsafe underlying identifier')
        h = read(output / 'warrant-history' / (code + '.json'))
        if h.get('underlying') != code:
            raise ValueError('History identity mismatch: ' + code)
        bank[code] = h
    if not bank:
        raise ValueError('Existing verified history is required')
    return bank


def available_days(bank):
    return {r[0] for h in bank.values() for q in h['quotes'].values() if q.get('market') == 'TPEX' for r in q['rows']}


def merge_day(bank, rows, day):
    """Stage an entire validated date, preserving quote identity and newer metadata."""
    if not rows or len({r['code'] for r in rows}) != len(rows):
        raise ValueError('Empty or duplicate dated TPEx quotes')
    counts, changed, spots = Counter(), {}, defaultdict(set)
    for r in rows:
        if r.get('date') != day or r.get('market') != 'TPEX':
            raise ValueError('Historical quote date or market mismatch')
        code, underlying = r['code'], r.get('underlying', '')
        if not isinstance(underlying, str) or not SAFE_CODE.fullmatch(underlying):
            counts['invalidUnderlying'] += 1
            continue
        if not r.get('name') or r.get('cp') not in ('C', 'P'):
            counts['unsupportedKind'] += 1
            continue
        volume = r.get('volume')
        if volume is not None and (isinstance(volume, bool) or not isinstance(volume, (int, float)) or not math.isfinite(volume) or volume < 0):
            raise ValueError('Invalid historical volume')
        if underlying not in changed:
            changed[underlying] = copy.deepcopy(bank.get(underlying) or {
                'schemaVersion': 1, 'underlying': underlying, 'name': r.get('underlyingName') or underlying,
                'quotes': {}, 'underlyingPrices': []})
        h = changed[underlying]
        q = h['quotes'].get(code)
        if q and any(q.get(k) != r.get(k) for k in ('name', 'market', 'cp')):
            counts['identityConflict'] += 1
            continue
        if q is None:
            q = {k: r.get(k) for k in ('name', 'market', 'cp')}
            q.update(expiry=None, strike=None, style=None, rows=[])
            h['quotes'][code] = q
        points = {p[0]: p for p in q['rows']}
        if len(points) != len(q['rows']):
            raise ValueError('Duplicate stored quote dates: ' + code)
        point = [day, trade_price(r.get('price'), volume), volume]
        if day not in points:
            points[day] = point
            counts['addedQuoteRows'] += 1
        elif points[day] != point:
            counts['existingQuoteConflict'] += 1
        else:
            counts['unchangedQuoteRows'] += 1
        q['rows'] = [points[d] for d in sorted(points)][-60:]
        counts['acceptedQuoteRows'] += 1
        counts['tradedQuoteRows'] += point[1] is not None
        if positive(r.get('underlyingPrice')):
            spots[underlying].add(r['underlyingPrice'])
    for code, h in changed.items():
        values = spots[code]
        points = dict(h['underlyingPrices'])
        if len(points) != len(h['underlyingPrices']):
            raise ValueError('Duplicate stored spot dates: ' + code)
        if len(values) > 1:
            counts['spotConflict'] += 1
        elif len(values) == 1:
            value = next(iter(values))
            if not positive(points.get(day)):
                points[day] = value
                counts['addedSpotRows'] += 1
            elif points[day] != value:
                counts['spotConflict'] += 1
        else:
            counts['spotUnavailable'] += 1
        h['underlyingPrices'] = [[d, points[d]] for d in sorted(points)][-60:]
    return changed, dict(counts)


def publish_histories(output, bank, changed):
    for code in sorted(changed):
        save(output / 'warrant-history' / (code + '.json'), bank[code])
    index = read(output / 'warrant-history.json')
    items = []
    for code, h in sorted(bank.items()):
        days = sorted({r[0] for q in h['quotes'].values() for r in q['rows']})
        if days:
            items.append({'code': code, 'name': h['name'], 'date': days[-1], 'days': len(days), 'count': len(h['quotes'])})
    save(output / 'warrant-history.json', {**index, 'items': items})


def backfill(output, cache, start, end, max_days=2, source=None, now=None):
    if start > end or (end - start).days > 100:
        raise ValueError('Historical window must be at most 101 calendar days')
    calendar = load_calendar(output)
    bank = load_histories(output)
    requested = sessions(calendar, start, end)
    available = available_days(bank)
    missing = sorted(set(requested) - available)
    path = output / 'tpex-history-backfill.json'
    ledger = read(path) or {'schemaVersion': 1, 'market': 'TPEX', 'days': []}
    records = {r['date']: r for r in ledger['days']}
    if not missing:
        if path.exists() and (ledger.get('remainingDates') or ledger.get('errors')):
            before = copy.deepcopy(ledger)
            ledger['remainingDates'] = [d for d in ledger.get('remainingDates', []) if d not in available]
            ledger['errors'] = [e for e in ledger.get('errors', []) if e.split(':', 1)[0] not in available]
            ledger['status'] = 'partial' if ledger['remainingDates'] or ledger['errors'] else 'ok'
            if ledger != before:
                save(path, ledger)
        return {'checkedDates': requested, 'newlyImported': [], 'remaining': [], 'errors': []}
    source = source or Sources(cache, cache)
    imported, errors, changed_codes = [], [], set()
    now = now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    for day in missing[:max_days]:
        try:
            rows, audit = source.otc(date.fromisoformat(day))
            staged, counts = merge_day(bank, rows, day)
            if not counts.get('acceptedQuoteRows'):
                raise ValueError('No accepted dated quotes')
            bank.update(staged)
            changed_codes.update(staged)
            conflicts = sum(counts.get(k, 0) for k in ('identityConflict', 'existingQuoteConflict', 'spotConflict'))
            records[day] = {'date': day, 'status': 'partial' if conflicts else 'imported', 'sourceRows': len(rows),
                            'sources': audit, 'importedAt': now, 'counts': counts, 'termsAvailable': False, 'branchesAvailable': False}
            imported.append(day)
        except Exception as exc:
            errors.append(day + ': ' + str(exc)[:180])
            records[day] = {'date': day, 'status': 'error', 'error': str(exc)[:180]}
            if isinstance(exc, HTTPError) and exc.code in (401, 403, 429):
                break
    publish_histories(output, bank, changed_codes)
    remaining = sorted(set(requested) - available_days(bank))
    ledger.update(days=[records[d] for d in sorted(records)], status='partial' if remaining or errors else 'ok',
                  requestedDates=sorted(set(ledger.get('requestedDates', [])) | set(requested)),
                  remainingDates=remaining, errors=errors,
                  fields=['權證成交收盤', '成交量（張）', '同日標的收盤'],
                  limitations=['官方歷史行情沒有歷史履約價、行使比例、到期日與完整分點；不套用今日條款回填過去。',
                               '只補缺少的上櫃行情日期；既有有效價格或身分衝突不覆寫，逐日列計數。',
                               '無成交價格留空；交易量單位由股換算為張。補抓是事後資料，不能作為當時已知訊號。',
                               '時間序列保留最近60個資料日；分點回測與日報既有樣本不重新選樣。'])
    save(path, ledger)
    return {'checkedDates': requested, 'newlyImported': imported, 'remaining': remaining, 'errors': errors}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data', type=Path, default=Path('data/derivatives'))
    p.add_argument('--cache', type=Path, default=Path('.cache/tpex-history'))
    p.add_argument('--start', type=date.fromisoformat)
    p.add_argument('--end', type=date.fromisoformat)
    p.add_argument('--max-days', type=int, default=2)
    args = p.parse_args()
    if not 1 <= args.max_days <= 30:
        raise ValueError('Bounded batch size is 1..30')
    manifest = read(args.data / 'manifest.json')
    latest = max((s.get('date', '') for s in manifest.get('sources', []) if s['id'] in ('twse-warrants', 'tpex-warrants') and s.get('date')), default='')
    today = datetime.now(timezone(timedelta(hours=8))).date()
    end = args.end or min(date.fromisoformat(latest), today - timedelta(days=1))
    if end >= today or (today-end).days > 100:
        raise ValueError('Backfill only completed historical dates within retention')
    start = args.start or end - timedelta(days=14)
    result = backfill(args.data, args.cache, start, end, args.max_days)
    print(json.dumps(result, ensure_ascii=False))
    if result['errors']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
