"""Daily matched-term closing-spread research; no quotes or execution claims.

Pair terms are captured when observed, never reconstructed from current terms.
Only past observations enter a signal. Future dates are used only for outcomes.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from hashlib import sha256
from itertools import combinations
import json
import math
import re
from statistics import fmean, stdev, median
try:
    from .governance import VERSION as GOVERNANCE_VERSION, digest, sample_maturity
except ImportError:
    from governance import VERSION as GOVERNANCE_VERSION, digest, sample_maturity

VERSION = 'closing-spread-v1'
WINDOW, THRESHOLD, HORIZON, RETAIN = 20, 2.0, 5, 252
SAFE = re.compile(r'[A-Za-z0-9_-]{1,24}')
RULES = {'version': VERSION, 'window': WINDOW, 'threshold': THRESHOLD, 'horizon': HORIZON,
         'referenceLag': 1, 'retention': RETAIN, 'minEvaluatedSample': 50,
         'governanceVersion': GOVERNANCE_VERSION, 'backtestType': 'Walk-forward Research',
         'formalModelEligible': False, 'calendarBasis': 'source-observation-days',
         'executionCostsAvailable': False, 'settlementTermsVerified': False,
         'description': '同條件收盤價差事件回測，非交易損益或套利保證'}


def positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def style(value):
    return {'E': '歐式', 'EUROPEAN': '歐式', '歐式': '歐式',
            'A': '美式', 'AMERICAN': '美式', '美式': '美式'}.get(str(value or '').strip().upper())


def valid_day(value):
    try:
        return date.fromisoformat(value).isoformat() == value
    except (ValueError, TypeError):
        return False


def exclusion(row):
    if not re.fullmatch(r'\d{4,6}[A-Z]?', str(row.get('underlying', ''))):
        return '非國內股票或ETF代碼'
    code = str(row.get('code', ''))
    if not ((row.get('cp') == 'C' and re.fullmatch(r'\d{6}', code)) or
            (row.get('cp') == 'P' and re.fullmatch(r'\d{5}[PUT]', code))):
        return '非一般認購售權證'
    if not style(row.get('style')) or not positive(row.get('strike')) or not positive(row.get('ratio')):
        return '履約條件缺值'
    day, expiry = row.get('date'), row.get('expiry')
    if not valid_day(day) or not valid_day(expiry) or (row.get('lastTrade') and not valid_day(row['lastTrade'])):
        return '日期缺值或異常'
    if expiry <= day or (row.get('lastTrade') and row['lastTrade'] < day):
        return '已到期或過最後交易日'
    if row.get('termsDate') != day:
        return '行情與條款日期不同'
    if not positive(row.get('price')):
        return '價格缺值或非正值'
    if not positive(row.get('volume')):
        return '無成交或成交量缺值'
    return None


def pair_spec(a, b):
    a, b = sorted((a, b), key=lambda r: r['code'])
    side = lambda r: {k: r.get(k) for k in ('code', 'name', 'market', 'ratio', 'lastTrade')}
    spec = {'a': side(a), 'b': side(b), 'strike': a['strike'], 'expiry': a['expiry'],
            'cp': a['cp'], 'style': style(a['style']), 'underlying': a['underlying']}
    # Names are display-only, so spelling corrections do not reset a series.
    identity = {**spec, 'a': {k: v for k, v in spec['a'].items() if k != 'name'},
                'b': {k: v for k, v in spec['b'].items() if k != 'name'}}
    key = sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    return {**spec, 'id': f"{a['code']}-{b['code']}-{key}"}, [a['date'], a['price'], b['price'], a['volume'], b['volume']]


def spread(point, spec):
    a, b = point[1] / spec['a']['ratio'], point[2] / spec['b']['ratio']
    # Symmetric percentage difference. A/B orientation never follows the price.
    return 200 * (a - b) / (a + b)


def analyze(spec, calendar):
    if calendar != sorted(set(calendar)) or any(not valid_day(d) for d in calendar):
        raise ValueError('Research calendar must be unique and chronological')
    point_dates = [p[0] for p in spec['points']]
    if point_dates != sorted(set(point_dates)) or not set(point_dates) <= set(calendar):
        raise ValueError('Research points must be unique, ordered and on the source calendar')
    if not all(positive(spec[side]['ratio']) for side in ('a', 'b')) or any(
            len(p) != 5 or not all(positive(v) for v in p[1:]) for p in spec['points']):
        raise ValueError('Research points require positive finite prices, volumes and ratios')
    for day, evidence in spec.get('termEvidence', {}).items():
        if day not in point_dates or set(evidence) != {'a', 'b'}:
            raise ValueError('Historical terms evidence must identify both sides of a stored point')
        for item in evidence.values():
            if item.get('basis') == 'effective-announcements-v1':
                if not valid_day(item.get('effective')) or item['effective'] > day or not valid_day(item.get('verifiedFrom')):
                    raise ValueError('Historical terms cannot precede their effective date')
            elif item.get('basis') != 'daily-report' or item.get('date') != day:
                raise ValueError('Unknown historical terms evidence')
    points = {p[0]: p for p in spec['points']}
    rolling, observations = [], []
    for day in calendar:
        point = points.get(day)
        value = spread(point, spec) if point else None
        baseline = rolling[-WINDOW:]
        mean = fmean(baseline) if len(baseline) == WINDOW else None
        sd = stdev(baseline) if mean is not None else None
        z = (value - mean) / sd if value is not None and sd is not None and sd > 1e-10 else None
        status = '缺少同日有效配對' if point is None else '歷史不足' if mean is None else '歷史變異不足' if z is None else '異常待觀察' if abs(z) >= THRESHOLD else '未達門檻'
        observations.append({'date': day, 'spread': value, 'mean': mean, 'z': z, 'priorCount': len(baseline), 'status': status,
                             'trainingEndDate': calendar[len(observations) - 1] if mean is not None else None})
        if point is None:
            rolling = []
        else:
            rolling.append(value)
    events, available_after = [], 0
    for i, obs in enumerate(observations):
        if i < available_after or obs['z'] is None or abs(obs['z']) < THRESHOLD:
            continue
        entry, end = i + 1, i + 1 + HORIZON
        event = {'signalDate': obs['date'], 'signalZ': obs['z'], 'baselineMean': obs['mean'],
                 'id': digest([spec['id'], obs['date'], HORIZON, VERSION, RULES['backtestType']])[:24],
                 'trainingStartDate': calendar[i - WINDOW], 'trainingEndDate': calendar[i - 1],
                 'inputSha256': digest([spec['id'], [points[d] for d in calendar[i - WINDOW:i + 1]]]),
                 'termsEvidenceSha256': digest({d: spec.get('termEvidence', {}).get(d) for d in calendar[i - WINDOW:i + 1]})
                                       if spec.get('termEvidence') else None,
                 'modelVersion': VERSION, 'horizon': HORIZON, 'backtestType': RULES['backtestType'],
                 'formalModelEligible': False,
                 'referenceDate': calendar[entry] if entry < len(calendar) else None,
                 'evaluationDate': calendar[end] if end < len(calendar) else None,
                 'referenceDeviation': None, 'endDeviation': None, 'contraction': None}
        # Keep pending/invalid cases in the denominator inventory, not the rate.
        if entry < len(calendar) and observations[entry]['spread'] is None:
            event['status'] = 'missing'
        elif end >= len(calendar):
            event['status'] = 'pending'
        elif any(o['spread'] is None for o in observations[entry:end + 1]):
            event['status'] = 'missing'
        else:
            before = abs(observations[entry]['spread'] - obs['mean'])
            after = abs(observations[end]['spread'] - obs['mean'])
            event.update(status='evaluated', referenceDeviation=before, endDeviation=after, contraction=before - after)
        events.append(event)
        available_after = end  # Per-pair windows do not overlap; no portfolio aggregation.
    done = [e for e in events if e['status'] == 'evaluated']
    smaller = sum(e['contraction'] > 1e-10 for e in done)
    larger = sum(e['contraction'] < -1e-10 for e in done)
    summary = {'signals': len(events), 'evaluated': len(done), 'pending': sum(e['status'] == 'pending' for e in events),
               'missing': sum(e['status'] == 'missing' for e in events), 'converged': smaller, 'diverged': larger,
               'unchanged': len(done) - smaller - larger, 'convergenceRate': 100 * smaller / len(done) if done else None,
               'medianContraction': median(e['contraction'] for e in done) if done else None,
               'sampleInsufficient': len(done) < RULES['minEvaluatedSample']}
    # Enforced invariants, not a claim of trading profitability or point-in-time availability.
    if len({e['id'] for e in events}) != len(events) or any(
            e['trainingEndDate'] >= e['signalDate'] or
            (e['referenceDate'] and e['referenceDate'] <= e['signalDate']) or
            (e['status'] == 'evaluated' and not (e['referenceDate'] < e['evaluationDate'] <= calendar[-1]))
            for e in events):
        raise ValueError('Research event chronology or identity failed')
    maturity = sample_maturity(len(done))
    audit = {'version': GOVERNANCE_VERSION, 'status': 'passed', 'backtestType': RULES['backtestType'],
             'checks': ['unique-ordered-dates', 'positive-inputs', 'past-only-baseline', 'event-chronology', 'evaluated-only-denominator'],
             'evaluated': len(done), 'maturity': maturity, 'formalModelEligible': False, 'liveOosSamples': 0,
             'calendarBasis': RULES['calendarBasis'],
             'limitations': ['事後歷史重算，來源修訂可能改變結果；非當時留存的即時預測',
                             '依已收錄資料日評估，未完整驗證交易日曆及當時可得時間',
                             '同步報價、完整結算條款及交易／避險成本未驗證']}
    return {'observations': observations, 'latest': observations[-1] if observations else None,
            'backtest': {'events': events, 'summary': summary}, 'audit': audit}


def successful(row, feeds):
    names = ('twse-warrants', 'twse-volume' if row.get('cp') == 'C' else 'twse-volume-put') if row.get('market') == 'TWSE' else ('tpex-warrants', 'tpex-terms')
    return all(feeds.get(n, {}).get('status') == 'ok' for n in names)


def collect_spreads(output, warrants, feeds, now, today, read, save):
    by_underlying = defaultdict(list)
    for row in warrants:
        code = row.get('underlying', '')
        if isinstance(code, str) and SAFE.fullmatch(code):
            by_underlying[code].append(row)
    old_index = read(output / 'warrant-spreads.json') or {'items': []}
    names = {i['code']: i['name'] for i in old_index['items']}
    names.update({code: rows[0].get('underlyingName') or code for code, rows in by_underlying.items()})
    cutoff = (today - timedelta(days=550)).isoformat()
    items, pending = [], []
    audit_totals = Counter()
    for code, name in sorted(names.items()):
        if not SAFE.fullmatch(code):
            continue
        path = output / 'warrant-spreads' / f'{code}.json'
        old = read(path) or {'pairs': [], 'dates': [], 'eligibility': {}}
        rows = by_underlying.get(code, [])
        fresh = [r for r in rows if successful(r, feeds) and valid_day(r.get('date'))]
        dates = sorted(set(old['dates']) | {r['date'] for r in fresh})
        dates = [d for d in dates if d >= cutoff][-RETAIN:]
        if any(d > today.isoformat() for d in dates):
            raise ValueError('Research calendar contains a future source date')
        pairs = {p['id']: {k: v for k, v in p.items() if k not in ('observations', 'latest', 'backtest')} for p in old['pairs']}
        # Re-running one source day replaces all that market's observations,
        # including pairs now invalidated by a source correction or zero volume.
        touched = {(r['market'], r.get('cp'), r['date']) for r in fresh}
        for pair in pairs.values():
            pair['points'] = [p for p in pair['points'] if p[0] in dates and
                              not all((pair[side]['market'], pair['cp'], p[0]) in touched for side in ('a', 'b'))]
            if 'termEvidence' in pair:
                kept = {p[0] for p in pair['points']}
                pair['termEvidence'] = {d: v for d, v in pair['termEvidence'].items() if d in kept}
        groups = defaultdict(list)
        excluded = Counter()
        eligible = 0
        duplicates = Counter((r.get('code'), r['date']) for r in fresh)
        for row in fresh:
            reason = '同日重複代碼' if duplicates[(row.get('code'), row['date'])] > 1 else exclusion(row)
            if reason:
                excluded[reason] += 1
                continue
            eligible += 1
            key = (row['date'], row['cp'], style(row['style']), row['strike'], row['expiry'])
            groups[key].append(row)
        for group in groups.values():
            for a, b in combinations(sorted(group, key=lambda r: r['code']), 2):
                spec, point = pair_spec(a, b)
                existing = pairs.get(spec['id'], {'points': []})
                points = {p[0]: p for p in existing['points']}
                points[point[0]] = point
                pairs[spec['id']] = {**spec, 'points': [points[d] for d in sorted(points) if d in dates]}
                evidence = dict(existing.get('termEvidence', {}))
                if a.get('termEvidence') or b.get('termEvidence'):
                    evidence[point[0]] = {side: row.get('termEvidence', {'basis': 'daily-report', 'date': row['date']})
                                          for side, row in zip(('a', 'b'), (a, b))}
                if evidence:
                    pairs[spec['id']]['termEvidence'] = evidence
        pairs = [p for p in pairs.values() if p['points']]
        for pair in pairs:
            pair.update(analyze(pair, dates))
            audit_totals['pairsChecked'] += 1
            audit_totals['evaluated'] += pair['backtest']['summary']['evaluated']
            audit_totals['pending'] += pair['backtest']['summary']['pending']
            audit_totals['missing'] += pair['backtest']['summary']['missing']
        pairs.sort(key=lambda p: p['id'])
        latest = dates[-1] if dates else None
        current = [p for p in pairs if p['latest'] and p['latest']['spread'] is not None]
        blocked = len(rows) - len(fresh)
        eligibility = {'total': len(rows), 'eligible': eligible, 'excluded': dict(excluded), 'sourceUnavailable': blocked}
        if not fresh and old.get('eligibility'):
            eligibility = {**old['eligibility'], 'sourceUnavailable': len(rows)}
        payload = {'schemaVersion': 1, 'rules': RULES, 'underlying': code, 'name': name, 'dates': dates,
                   'date': latest, 'status': 'partial' if blocked else 'ok', 'eligibility': eligibility, 'pairs': pairs}
        if payload != old:
            pending.append((path, payload))
        items.append({'code': code, 'name': name, 'date': latest, 'days': len(dates), 'pairs': len(current),
                      'historyPairs': len(pairs), 'signals': sum(p['latest']['status'] == '異常待觀察' for p in current),
                      'status': payload['status']})
    # Audit every underlying before replacing any derived files.
    for path, payload in pending:
        save(path, payload)
    save(output / 'warrant-spreads.json', {'schemaVersion': 1, 'rules': RULES,
                                         'status': 'partial' if any(i['status'] != 'ok' for i in items) else 'ok',
                                         'error': None, 'items': items,
                                         'audit': {'version': GOVERNANCE_VERSION, 'status': 'passed', **audit_totals,
                                                   'formalModelEligible': False, 'liveOosSamples': 0},
                                         'backfill': read(output / 'warrant-backfill.json')})
