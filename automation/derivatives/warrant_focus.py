"""Free daily research shortlist from existing snapshots, with explicit coverage."""
from collections import Counter, defaultdict
from datetime import date
from statistics import fmean
import math

try:
    from .trading_calendar import default_calendar
    from .warrant_spreads import exclusion, positive, style, valid_day, SAFE, VERSION as SPREAD_VERSION
except ImportError:
    from trading_calendar import default_calendar
    from warrant_spreads import exclusion, positive, style, valid_day, SAFE, VERSION as SPREAD_VERSION

RULES = {'version': 'warrant-focus-v2-trading-calendar', 'volumeWindow': 5, 'volumeMultiple': 2,
         'minVolume': 100, 'zThreshold': 2, 'wideSpreadThreshold': 20,
         'maxSourceAgeTradingDays': 4, 'maxBaselineAgeDays': 30, 'spreadModel': SPREAD_VERSION,
         'basis': '已收錄行情日，非保證連續交易日；當日不進均量基準',
         'description': '固定研究觀察門檻，未經策略有效性驗證；非買賣建議或套利利潤'}


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def percent(current, previous):
    return (current / previous - 1) * 100 if numeric(current) and positive(previous) else None


def source_ids(row):
    return (['twse-warrants', 'twse-volume-put' if row['cp'] == 'P' else 'twse-volume']
            if row['market'] == 'TWSE' else ['tpex-warrants', 'tpex-terms'])


def fresh(row, feeds, today, calendar=None):
    return ((calendar or default_calendar()).fresh(row.get('date'), today, RULES['maxSourceAgeTradingDays'])
            and all(feeds.get(key, {}).get('status') == 'ok' and feeds[key].get('date') == row['date'] for key in source_ids(row)))


def volume_evidence(row, history, calendar):
    saved = history.get('quotes', {}).get(row['code'], {}).get('rows', [])
    dates = [p[0] for p in saved]
    if len(set(dates)) != len(dates):
        return {'status': '歷史日期重複', 'signal': False}
    points = {p[0]: p for p in saved}
    current = points.get(row['date'])
    baseline = sorted(d for d in calendar if d < row['date'])[-RULES['volumeWindow']:]
    result = {'status': '前五期資料不足', 'signal': False, 'baselineDates': baseline,
              'average': None, 'multiple': None, 'delta': None}
    if not current or current[1:3] != [row['price'], row['volume']]:
        return {**result, 'status': '歷史與當日價格／量不一致'}
    if len(baseline) != RULES['volumeWindow']:
        return result
    if (date.fromisoformat(row['date']) - date.fromisoformat(baseline[0])).days > RULES['maxBaselineAgeDays']:
        return {**result, 'status': '前五期基準距今過久'}
    values = [points.get(d, [None, None, None])[2] for d in baseline]
    if any(not numeric(v) or v < 0 for v in values):
        return {**result, 'status': '前五期缺漏／成交量缺值'}
    average = fmean(values)
    result.update(average=average, delta=row['volume'] - average)
    if average == 0:
        return {**result, 'status': '前五期均量為零，不計倍數'}
    multiple = row['volume'] / average
    return {**result, 'status': '可比較', 'multiple': multiple,
            'signal': row['volume'] >= RULES['minVolume'] and multiple >= RULES['volumeMultiple']}


def spread_evidence(catalog, data, index, feeds, today, calendar=None):
    found = defaultdict(list)
    if (not data or data.get('status') != 'ok' or index.get('status') not in ('ok', 'partial')
            or index.get('rules', {}).get('version') != SPREAD_VERSION):
        return found
    for pair in data.get('pairs', []):
        a, b = catalog.get(pair['a']['code']), catalog.get(pair['b']['code'])
        latest = pair.get('latest') or {}
        if not a or not b or latest.get('date') != data.get('date') or not numeric(latest.get('spread')):
            continue
        point = next((p for p in pair['points'] if p[0] == data['date']), None)
        if not point:
            continue
        if not all(fresh(r, feeds, today, calendar) and not exclusion(r) and r['date'] == data['date']
                   and all(r[k] == pair[k] for k in ('underlying', 'cp', 'strike', 'expiry'))
                   and style(r['style']) == pair['style'] and r['ratio'] == pair[side]['ratio']
                   and r['price'] == point[i + 1] and r['volume'] == point[i + 3]
                   and r['expiry'] >= today.isoformat() and (not r.get('lastTrade') or r['lastTrade'] >= today.isoformat())
                   for i, (r, side) in enumerate(((a, 'a'), (b, 'b')))):
            continue
        spread = 200 * (a['price'] / a['ratio'] - b['price'] / b['ratio']) / (a['price'] / a['ratio'] + b['price'] / b['ratio'])
        if not math.isclose(spread, latest['spread'], abs_tol=1e-8):
            continue
        z = latest.get('z')
        # Keep the upstream point-in-time baseline; never infer Z from gap size.
        ready = (numeric(z) and latest.get('priorCount') == 20 and valid_day(latest.get('trainingEndDate'))
                 and latest['trainingEndDate'] < data['date'])
        for own, peer, direction in ((a, b, 1), (b, a, -1)):
            found[own['code']].append({'pairId': pair['id'], 'peerCode': peer['code'], 'peerName': peer['name'],
                'date': data['date'], 'spread': spread * direction, 'z': z * direction if ready else None,
                'zStatus': latest.get('status') if not ready else '可比較', 'priorCount': latest.get('priorCount'),
                'trainingEndDate': latest.get('trainingEndDate'), 'signal': ready and abs(z) >= RULES['zThreshold'],
                'wide': abs(spread) >= RULES['wideSpreadThreshold']})
    return found


def build_focus(warrants, histories, spreads, index, feeds, now, today, calendar=None):
    calendar = calendar or default_calendar()
    calendars = defaultdict(set)
    for history in histories.values():
        for q in history.get('quotes', {}).values():
            calendars[q.get('market')].update(p[0] for p in q.get('rows', []) if valid_day(p[0]) and p[0] <= today.isoformat())
    duplicates = Counter(r['code'] for r in warrants)
    catalog = {r['code']: r for r in warrants if duplicates[r['code']] == 1}
    paired = {}
    for code, data in spreads.items():
        paired.update(spread_evidence(catalog, data, index, feeds, today, calendar))
    excluded, volume_status, spread_status = Counter(), Counter(), Counter()
    items, eligible = [], 0
    for row in warrants:
        reason = '權證代碼重複' if duplicates[row['code']] > 1 else exclusion(row)
        if not reason and (row['expiry'] < today.isoformat() or (row.get('lastTrade') and row['lastTrade'] < today.isoformat())):
            reason = '目前已到期／停止交易'
        if not reason and not fresh(row, feeds, today, calendar):
            reason = '必要來源失敗、不同日或資料較舊'
        if reason:
            excluded[reason] += 1
            continue
        eligible += 1
        history = histories.get(row['underlying'], {})
        vol = volume_evidence(row, history, calendars[row['market']])
        volume_status[vol['status']] += 1
        peers = paired.get(row['code'], [])
        candidates = sorted(peers, key=lambda p: (-int(p['signal']), -abs(p['z'] or 0) if p['signal'] else -abs(p['spread']), p['peerCode']))
        pair = candidates[0] if candidates else None
        ready = any(p['z'] is not None for p in peers)
        spread_status['可計算 Z' if ready else '有配對但 Z 不足' if peers else '無可用配對／價差更新不足'] += 1
        tags = []
        if pair and pair['signal']:
            tags.append('spread-anomaly')
        if vol['signal']:
            tags.append('volume-surge')
        if pair and pair['wide'] and not pair['signal']:
            tags.append('wide-spread')
        if not tags:
            continue
        old = sorted((p for p in history.get('quotes', {}).get(row['code'], {}).get('rows', []) if p[0] < row['date']), key=lambda p: p[0])
        previous = old[-1] if old else None
        prices = dict(history.get('underlyingPrices', []))
        item = {k: row.get(k) for k in ('code', 'name', 'market', 'underlying', 'underlyingName', 'date', 'price', 'volume', 'cp', 'strike', 'expiry', 'lastTrade', 'ratio', 'termsDate')}
        item.update(style=style(row['style']), tags=tags, volumeEvidence=vol, spreadEvidence=pair,
                    peerCount=len(peers), previousDate=previous[0] if previous else None,
                    pricePct=percent(row['price'], previous[1] if previous else None),
                    underlyingPrice=prices.get(row['date']),
                    underlyingPct=percent(prices.get(row['date']), prices.get(previous[0])) if previous else None)
        items.append(item)
    def priority(item):
        tags, v, p = item['tags'], item['volumeEvidence'], item['spreadEvidence'] or {}
        tier = 0 if 'spread-anomaly' in tags and 'volume-surge' in tags else 1 if 'spread-anomaly' in tags else 2 if 'volume-surge' in tags else 3
        return (tier, -abs(p.get('z') or 0) if tier < 2 else -(v.get('delta') or 0) if tier == 2 else -abs(p.get('spread') or 0), -(v.get('multiple') or 0), item['code'])
    items.sort(key=priority)
    required = ['twse-warrants', 'twse-volume', 'twse-volume-put', 'tpex-warrants', 'tpex-terms']
    sources = [{'id': key, **{k: feeds.get(key, {}).get(k) for k in ('date', 'status')}} for key in required]
    unavailable = any(s['status'] != 'ok' or not calendar.fresh(s['date'], today, RULES['maxSourceAgeTradingDays']) for s in sources)
    # No run timestamps or day counters: the shortlist only changes when its inputs change.
    return {'schemaVersion': 1, 'rules': RULES,
            'status': 'partial' if unavailable or index.get('status') != 'ok' else 'ok', 'error': None,
            'sources': sources,
            'summary': {'total': len(warrants), 'eligible': eligible, 'listed': len(items),
                        'anomalies': sum('spread-anomaly' in r['tags'] for r in items),
                        'volumeSurges': sum('volume-surge' in r['tags'] for r in items),
                        'both': sum('spread-anomaly' in r['tags'] and 'volume-surge' in r['tags'] for r in items),
                        'wideOnly': sum('wide-spread' in r['tags'] for r in items)},
            'coverage': {'excluded': dict(excluded), 'volume': dict(volume_status), 'spread': dict(spread_status)}, 'items': items}


def collect_focus(output, warrants, feeds, now, today, read, save, calendar=None):
    codes = sorted({r.get('underlying', '') for r in warrants if SAFE.fullmatch(str(r.get('underlying', '')))})
    histories = {code: read(output / 'warrant-history' / f'{code}.json') or {} for code in codes}
    index = read(output / 'warrant-spreads.json') or {}
    spread_codes = {i['code'] for i in index.get('items', []) if i['code'] in histories}
    spreads = {code: read(output / 'warrant-spreads' / f'{code}.json') for code in spread_codes}
    report = build_focus(warrants, histories, spreads, index, feeds, now, today, calendar)
    backfill = index.get('backfill') or {}
    days = [d for d in backfill.get('days', []) if d['status'] == 'imported']
    if days:
        extension = backfill.get('extension') or {}
        report['historyCoverage'] = {'first': days[0]['date'], 'last': days[-1]['date'], 'days': len(days),
                                     'extendedDays': sum(d.get('termsMethod') == 'effective-announcements-v1' for d in days),
                                     'limitations': extension.get('limitations', [])}
    save(output / 'warrant-focus.json', report)
    return report
