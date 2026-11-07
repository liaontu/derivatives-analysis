"""Daily warrant research journal. No new network requests or simulated trades."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from statistics import median
import argparse
import json
import math
import re

try:
    from .daily_reports import read, save, aligned, baseline_days, WARRANT_SOURCES, traded, volume_exclusion
    from .warrant_focus import numeric, percent, volume_evidence, spread_evidence, RULES as FOCUS_RULES
    from .trading_calendar import load_calendar
    from .warrant_issuers import issuer_of, branch_relation, customer_eligible, POLICY, RULES_SHA256
    from .warrant_signals import SIGNAL_RULES
except ImportError:
    from daily_reports import read, save, aligned, baseline_days, WARRANT_SOURCES, traded, volume_exclusion
    from warrant_focus import numeric, percent, volume_evidence, spread_evidence, RULES as FOCUS_RULES
    from trading_calendar import load_calendar
    from warrant_issuers import issuer_of, branch_relation, customer_eligible, POLICY, RULES_SHA256
    from warrant_signals import SIGNAL_RULES

VERSION = 'warrant-journal-v2'
RULES = {'version': VERSION, 'minVolume': 100, 'volumeMultiple': 2, 'volumeWindow': 5,
         'zThreshold': FOCUS_RULES['zThreshold'], 'spreadWindow': 20, 'spreadModel': FOCUS_RULES['spreadModel'],
         'branchStreak': SIGNAL_RULES['branchStreak'], 'issuerPolicy': POLICY, 'issuerRulesSha256': RULES_SHA256,
         'shortlistSize': 50, 'maxPerUnderlying': 2,
         'trackingSize': 20, 'trackingBasis': '每版前20檔，日內依權證去重', 'horizons': [1, 3, 5, 10], 'expiryAlerts': [14, 30],
         'description': '固定研究門檻，尚未驗證交易有效性；不辨識大戶身分、不估算分點庫存。'}
TAGS = {'volume': '成交量放大', 'spread': 'Z 值異常', 'buy': '分點持續買超',
        'sell': '分點持續賣超', 'to-sell': '分點買超轉賣超', 'to-buy': '分點賣超轉買超'}
FIELDS = ('code', 'name', 'market', 'underlying', 'underlyingName', 'cp', 'price', 'volume',
          'strike', 'ratio', 'style', 'expiry', 'lastTrade', 'termsDate')


def optional(path, fallback):
    return read(path) if path.exists() else fallback


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()[:20]


def price_change(current, previous):
    return percent(current, previous) if numeric(current) and current > 0 else None


def sessions(day, count, calendar, direction=-1, include=False):
    cursor = date.fromisoformat(day)
    result = []
    while len(result) < count:
        if not include or result:
            cursor += timedelta(days=direction)
        include = False
        if calendar.is_open(cursor):
            result.append(cursor.isoformat())
    return result


def valid_rows(snapshot):
    rows = snapshot.get('rows', [])
    counts = Counter(r.get('branchCode') for r in rows)
    return {r['branchCode']: r for r in rows if counts[r.get('branchCode')] == 1
            and re.fullmatch(r'[0-9A-Za-z]{4}', r.get('branchCode', ''))
            and all(numeric(r.get(k)) for k in ('buy', 'sell', 'net'))
            and r['buy'] >= 0 and r['sell'] >= 0}


def branch_evidence(quote, payload, calendar):
    """Unranked dates are unknown, never zero; gaps interrupt a streak."""
    day = quote['date']
    history = {p['date']: p for p in payload.get('history', []) if p.get('date', '') <= day}
    current = history.get(day)
    if (not current or current.get('quoteDate') != day or not numeric(current.get('quoteVolume'))
            or not numeric(quote.get('volume')) or not math.isclose(current['quoteVolume'], quote['volume'])):
        return None
    days = sessions(day, 11, calendar, include=True)
    maps = {d: valid_rows(h) for d, h in history.items()}
    issuer = issuer_of(quote.get('name'))
    raw_rows = [{**r, 'issuer':issuer, 'relation':branch_relation(r['branchName'], issuer)} for r in maps.get(day, {}).values()]
    rows = {r['branchCode']:r for r in raw_rows if customer_eligible(r['relation'])}
    def streak(code, start, sign):
        result = 0
        for d in days[start:]:
            r = maps.get(d, {}).get(code)
            if not r or r['net'] * sign <= 0:
                break
            result += 1
        return result
    signals = []
    for code, row in rows.items():
        sign = 1 if row['net'] > 0 else -1 if row['net'] < 0 else 0
        run = streak(code, 0, sign) if sign else 0
        previous = streak(code, 1, -sign) if sign else 0
        tags = []
        if run >= RULES['branchStreak']:
            tags.append('buy' if sign > 0 else 'sell')
        if previous >= RULES['branchStreak']:
            tags.append('to-buy' if sign > 0 else 'to-sell')
        if not tags:
            continue
        points = []
        for d in days[:5]:
            r = maps.get(d, {}).get(code)
            points.append([d, r['net'] if r else None,
                           'observed' if r else 'unranked' if d in history else 'missing'])
        signals.append({'branchCode': code, 'branchName': row['branchName'], 'buy': row['buy'],
                        'relation':row['relation'], 'issuer':issuer,
                        'sell': row['sell'], 'net': row['net'], 'streak': run, 'priorOppositeStreak': previous,
                        'streakCapped': run == len(days), 'priorOppositeCapped': previous == len(days) - 1,
                        'tags': tags, 'points': points, 'observedDays': sum(p[1] is not None for p in points),
                        'observedNet': sum(p[1] for p in points if p[1] is not None)})
    buy = sum(sorted((r['net'] for r in rows.values() if r['net'] > 0), reverse=True)[:3])
    sell = sum(sorted((-r['net'] for r in rows.values() if r['net'] < 0), reverse=True)[:3])
    return {'signals': signals, 'rankedBranches': len(raw_rows), 'rankedRows':raw_rows,
            'top3BuyPct': 100 * buy / quote['volume'] if quote['volume'] > 0 else None,
            'top3SellPct': 100 * sell / quote['volume'] if quote['volume'] > 0 else None}


def diversified(items, size):
    counts, selected = Counter(), []
    for item in items:
        if counts[item['underlying']] >= RULES['maxPerUnderlying']:
            continue
        selected.append(item)
        counts[item['underlying']] += 1
        if len(selected) == size:
            break
    return selected


def build(output, calendar):
    manifest = read(output / 'manifest.json')
    day, sources = aligned(manifest['sources'], WARRANT_SOURCES)
    volume = read(output / 'daily-reports/warrant-volume' / (day + '.json'))
    directory = read(output / 'stock-warrant-directory.json')
    if directory.get('status') != 'ok' or directory.get('quoteDates') != [day]:
        raise ValueError('等待同日個股權證名錄')
    names = {s['code'] for s in volume['stocks']}
    all_rows = read(output / 'warrants.json')['rows']
    rows = [r for r in all_rows if r.get('underlying') in names and r.get('date') == day]
    if len(rows) != sum(s['total'] for s in volume['stocks']):
        raise ValueError('量價日報與當日行情檔數不一致')
    duplicates = Counter(r['code'] for r in rows)
    catalog = {r['code']: r for r in rows if duplicates[r['code']] == 1}
    histories = {code: optional(output / 'warrant-history' / (code + '.json'), {}) for code in names}
    feeds = {s['id']: s for s in manifest['sources']}
    spread_index = optional(output / 'warrant-spreads.json', {})
    peers = {}
    for code in names:
        data = optional(output / 'warrant-spreads' / (code + '.json'), {})
        peers.update(spread_evidence(catalog, data, spread_index, feeds, date.fromisoformat(day), calendar))
    branch_index = optional(output / 'warrant-branches.json', {'items': []})
    branches, branch_issues, branch_sources = {}, Counter(), []
    branch_requested = sum(item.get('underlying') in names for item in branch_index['items'])
    for item in branch_index['items']:
        row = catalog.get(item['code'])
        if not row:
            branch_issues['ETF／指數等不屬本報告個股範圍' if item.get('underlying') not in names else '同日行情缺少此權證'] += 1
            continue
        if item.get('status') != 'ok' or item.get('date') != day:
            branch_issues['分點待更新／非同日／來源異常'] += 1
            continue
        if any(item.get(k) != row.get(k) for k in ('name', 'market', 'cp', 'underlying')):
            branch_issues['權證身分不一致'] += 1
            continue
        payload = optional(output / 'warrant-branches' / (item['code'] + '.json'), {})
        evidence = branch_evidence(row, payload, calendar)
        if evidence is None:
            branch_issues['分點與同日行情未吻合'] += 1
            continue
        branches[item['code']] = evidence
        branch_sources.append({'code': item['code'], 'date': day, 'url': payload.get('sourceUrl'),
                               'fetchedAt': payload.get('fetchedAt'), 'digest': digest(payload.get('history', []))})
    baseline = baseline_days(day, calendar)
    candidates, branch_signals, decisions, issuer_flows, unknown_flows = [], [], {}, [], []
    quality = Counter()
    for code, row in catalog.items():
        history = histories.get(row['underlying'], {})
        q = history.get('quotes', {}).get(code, {})
        identity = all(q.get(k) == row.get(k) for k in ('name', 'market', 'cp'))
        v = volume_evidence(row, history, baseline) if identity and not volume_exclusion(row) else {'status': '無成交／歷史身分不符', 'signal': False}
        quality[v['status']] += 1
        p = sorted(peers.get(code, []), key=lambda x: (-int(x['signal']), -abs(x.get('z') or 0), x['peerCode']))
        peer = p[0] if p else None
        b = branches.get(code)
        if b:
            for ranked in b['rankedRows']:
                item = {**{k:row.get(k) for k in ('code','name','underlying','underlyingName','cp')}, **ranked}
                if ranked['relation'] == 'issuer': issuer_flows.append(item)
                if ranked['relation'] == 'unknown': unknown_flows.append(item)
        tags = (['volume'] if v['signal'] else []) + (['spread'] if peer and peer['signal'] else [])
        if b:
            for signal in b['signals']:
                tags.extend(signal['tags'])
                branch_signals.append({**{k: row.get(k) for k in ('code', 'name', 'underlying', 'underlyingName', 'cp')},
                                       **signal, 'top3BuyPct': b['top3BuyPct'], 'top3SellPct': b['top3SellPct']})
        decisions[code] = {'tags': sorted(set(tags)), 'volumeStatus': v['status']}
        if not tags:
            continue
        prior = max((p for p in q.get('rows', []) if p[0] < day and traded(p)), default=None, key=lambda x: x[0]) if identity else None
        spot = dict(history.get('underlyingPrices', []))
        current_price = row.get('price') if traded([day, row.get('price'), row.get('volume')]) else None
        remaining = (date.fromisoformat(row['expiry']) - date.fromisoformat(day)).days if row.get('expiry') else None
        missing = [k for k in ('strike', 'ratio', 'style', 'expiry', 'lastTrade', 'termsDate') if row.get(k) in (None, '')]
        candidates.append({**{k: row.get(k) for k in FIELDS}, 'price': current_price, 'date': day,
            'underlyingPrice': spot.get(day), 'previousDate': prior[0] if prior else None,
            'warrantPct': price_change(current_price, prior[1]) if prior else None,
            'stockPct': price_change(spot.get(day), spot.get(prior[0])) if prior else None,
            'multiple': v.get('multiple'), 'averageVolume': v.get('average'), 'volumeStatus': v['status'],
            'spread': peer, 'branchAvailable': b is not None, 'branchSignals': len(b['signals']) if b else None,
            'tags': sorted(set(tags)), 'remainingDays': remaining, 'missingTerms': missing})
    candidates.sort(key=lambda r: (-len(r['tags']), -(r['multiple'] or 0), -r['volume'], r['code']))
    shortlist = diversified(candidates, RULES['shortlistSize'])
    branch_signals.sort(key=lambda r: (-int(any(t.startswith('to-') for t in r['tags'])), -r['streak'], -abs(r['net']), r['code'], r['branchCode']))
    stocks = [{k: s[k] for k in ('code', 'name', 'total', 'eligible', 'callBase', 'putBase', 'call', 'put')} for s in volume['stocks']]
    previous_days = sorted(p.stem for p in (output / 'daily-reports/warrant-journal').glob('????-??-??.json') if p.stem < day)
    previous = read(output / 'daily-reports/warrant-journal' / (previous_days[-1] + '.json')) if previous_days else None
    changes = []
    for old in previous.get('shortlist', []) if previous else []:
        current = decisions.get(old['code'])
        status = ('持續有觀察訊號' if current and current['tags'] else
                  '放量條件已消退' if old['tags'] == ['volume'] and current and current['volumeStatus'] == '可比較' else
                  '未觀測到同類訊號／資料不足，無法確認消退')
        changes.append({'code': old['code'], 'name': old['name'], 'before': old['tags'],
                        'after': current['tags'] if current else [], 'status': status})
    summary = {'total': len(rows), 'eligible': sum(s['eligible'] for s in stocks),
               'volumeHits': sum(s['call'] + s['put'] for s in stocks), 'candidates': len(candidates),
               'shortlist': len(shortlist), 'branchRequested': branch_requested, 'branchUniverse': len(branch_index['items']),
               'branchCovered': len(branches), 'branchSignals': len(branch_signals),
               'signals': dict(Counter(t for r in candidates for t in r['tags']))}
    spread_coverage = {'paired': len(peers), 'zReady': sum(any(p['z'] is not None for p in values) for values in peers.values()),
                       'status': spread_index.get('status', 'missing')}
    return {'schemaVersion': 1, 'date': day, 'rules': RULES, 'status': 'ok' if len(branches) == branch_requested and branches else 'partial',
            'sources': sources, 'branchSources': branch_sources, 'directoryDates': directory['sourceDates'],
            'summary': summary, 'volumeExcluded': volume['excluded'], 'volumeQuality': dict(quality),
            'branchIssues': dict(branch_issues), 'spreadCoverage': spread_coverage, 'baselineDates': baseline, 'stocks': stocks,
            'shortlist': shortlist, 'branches': branch_signals, 'issuerFlows':issuer_flows,
            'unknownIssuerFlows':unknown_flows, 'previousDate': previous['date'] if previous else None,
            'changes': changes, 'limits': ['分點為買超、賣超各最多15名；未入榜不等於零交易。',
                '分點代表交易通路，不能辨識單一大戶、推算實際庫存或確認收貨出貨。',
                '排名以訊號種類數、量增倍數、成交量排序，每個標的最多2檔；不是獲利機率。',
                '收盤價差及後續價格變化沒有可成交或成本後獲利的推定。']}, histories


def seal(output, report, now):
    folder = output / 'daily-reports/warrant-journal'
    index = optional(folder / 'index.json', {'dates': [], 'revisions': {}})
    revision = digest(report)
    versions = index.setdefault('revisions', {}).setdefault(report['date'], [])
    found = next((v for v in versions if v['id'] == revision), None)
    if found is None:
        found = {'id': revision, 'recordedAt': now}
        versions.append(found)
    payload = {**report, 'revision': revision, 'recordedAt': found['recordedAt']}
    revision_path = folder / 'revisions' / (report['date'] + '-' + revision + '.json')
    if not revision_path.exists():
        save(revision_path, payload)
    save(folder / (report['date'] + '.json'), payload)
    index.update(dates=sorted(set(index['dates']) | {report['date']}), status='ok')
    index.pop('error', None)
    save(folder / 'index.json', index)
    return payload


def seed_research(output):
    """Freeze a separate exploratory cohort; never label backfilled signals live."""
    path = output / 'daily-reports/warrant-journal/cohorts' / VERSION / 'research-cohort.json'
    if path.exists():
        return read(path)
    cohorts = []
    for file in sorted((output / 'daily-reports/warrant-volume').glob('????-??-??.json')):
        report = read(file)
        items = [{**r, 'underlying': s['code'], 'underlyingName': s['name']} for s in report['stocks'] for r in s['hits']]
        items.sort(key=lambda r: (-r['multiple'], -r['volume'], r['code']))
        for item in diversified(items, RULES['trackingSize']):
            cohorts.append({**item, 'signalDate': report['date'], 'tags': ['volume'], 'mode': 'research',
                            'id': report['date'] + ':' + item['code'], 'sourceDigest': digest(report)})
    data = {'rules': RULES, 'basis': '歷史量價日報重建；僅放量條件，非當時已發布訊號，含後續修訂與樣本選擇限制。', 'items': cohorts}
    save(path, data)
    return data


def capture(output, report):
    path = output / 'daily-reports/warrant-journal/cohorts' / VERSION / 'recorded-cohort.json'
    data = optional(path, {'rules': RULES, 'basis': '啟用後封存觀察；產生時間不等於已驗證上線時間，非即時樣本外績效。', 'items': []})
    seen = {r['id'] for r in data['items']}
    for item in report['shortlist'][:RULES['trackingSize']]:
        key = VERSION + ':' + report['date'] + ':' + item['code']
        if key not in seen:
            data['items'].append({**{k: item.get(k) for k in ('code', 'name', 'market', 'cp', 'underlying', 'underlyingName', 'expiry', 'lastTrade', 'tags')},
                                 'id': key, 'signalDate': report['date'], 'recordedAt': report['recordedAt'],
                                 'revision': report['revision'], 'mode': 'recorded'})
    save(path, data)
    return data


def outcome(event, histories, as_of, calendar):
    available = event['signalDate']
    if event['mode'] == 'recorded':
        recorded_day = datetime.fromisoformat(event['recordedAt']).astimezone(timezone(timedelta(hours=8))).date().isoformat()
        available = max(available, recorded_day)
    try:
        days = sessions(available, 11, calendar, direction=1)
    except ValueError:
        return {**event, 'baselineDate': None, 'baselinePrice': None,
                'results': [{'horizon': n, 'date': None, 'status': '日曆未驗證', 'pct': None, 'stockPct': None} for n in RULES['horizons']]}
    history = histories.get(event['underlying'], {})
    quote = history.get('quotes', {}).get(event['code'], {})
    identity = quote.get('name') == event['name'] and quote.get('cp') == event['cp']
    if event.get('market'):
        identity = identity and quote.get('market') == event['market']
    points = {p[0]: p for p in quote.get('rows', [])} if identity else {}
    if len(points) != len(quote.get('rows', [])):
        points = {}
    spots = dict(history.get('underlyingPrices', []))
    base = points.get(days[0])
    results = []
    for horizon in RULES['horizons']:
        target = days[horizon]
        point = points.get(target)
        limit = event.get('lastTrade') or event.get('expiry')
        status = ('待觀察' if target > as_of else '已過最後交易日／到期日' if limit and target > limit else
                  '缺基準日成交價' if not base or not traded(base) else
                  '觀察日無成交／缺價' if not point or not traded(point) else '可比較')
        results.append({'horizon': horizon, 'date': target, 'status': status,
                        'pct': percent(point[1], base[1]) if status == '可比較' else None,
                        'stockPct': price_change(spots.get(target), spots.get(days[0])) if status == '可比較' else None})
    return {**event, 'baselineDate': days[0], 'baselinePrice': base[1] if base and traded(base) and days[0] <= as_of else None,
            'results': results}


def track(output, report, histories, calendar):
    folder = output / 'daily-reports/warrant-journal'
    old = optional(folder / 'outcomes.json', {})
    if old and old.get('rules',{}).get('version') != VERSION:
        version = old.get('rules',{}).get('version')
        if version != 'warrant-journal-v1':
            raise ValueError('未知日報追蹤規則，拒絕覆寫')
        for name in ('outcomes.json','research-cohort.json','recorded-cohort.json'):
            origin, target = folder/name, folder/'versions'/version/name
            if not origin.exists(): raise ValueError('舊日報樣本缺檔：'+name)
            raw = origin.read_bytes()
            if target.exists() and target.read_bytes() != raw: raise ValueError('日報封存內容不符：'+name)
            if not target.exists():
                target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(raw)
    research, recorded = seed_research(output), capture(output, report)
    all_events = research['items'] + recorded['items']
    # Expired/removed underlyings remain evaluable from archived histories.
    for event in all_events:
        code = event['underlying']
        if code not in histories and re.fullmatch(r'\d{4}', code):
            histories[code] = optional(output / 'warrant-history' / (code + '.json'), {})
    outcomes = [outcome(e, histories, report['date'], calendar) for e in all_events]
    summary = []
    for mode in ('recorded', 'research'):
        for cp in ('C', 'P'):
            for horizon in RULES['horizons']:
                rows = [next(r for r in e['results'] if r['horizon'] == horizon) for e in outcomes if e['mode'] == mode and e['cp'] == cp]
                values = [r['pct'] for r in rows if r['pct'] is not None]
                summary.append({'mode': mode, 'cp': cp, 'horizon': horizon, 'total': len(rows), 'comparable': len(values),
                                'medianPct': median(values) if values else None, 'positive': sum(v > 0 for v in values),
                                'statuses': dict(Counter(r['status'] for r in rows))})
    data = {'asOf': report['date'], 'rules': RULES, 'basis': {'recorded': recorded['basis'], 'research': research['basis']},
            'method': '訊號日／封存日之後首個交易日收盤為基準，再觀察第1、3、5、10個交易日；缺價不順延，不視為成交或扣費後報酬。',
            'summary': summary, 'items': outcomes}
    save(output / 'daily-reports/warrant-journal/outcomes.json', data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('data/derivatives'))
    args = parser.parse_args()
    folder = args.data / 'daily-reports/warrant-journal'
    try:
        calendar = load_calendar(args.data)
        report, histories = build(args.data, calendar)
        payload = seal(args.data, report, datetime.now(timezone.utc).isoformat(timespec='seconds'))
        track(args.data, payload, histories, calendar)
        print(json.dumps({'date': payload['date'], 'revision': payload['revision'], **payload['summary']}, ensure_ascii=False))
    except Exception as exc:
        index = optional(folder / 'index.json', {'dates': [], 'revisions': {}})
        save(folder / 'index.json', {**index, 'status': 'retained', 'error': str(exc)[:180]})
        raise


if __name__ == '__main__':
    main()
