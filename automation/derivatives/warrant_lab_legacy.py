"""Prospective, fixed-candidate paper experiments. Never sends broker orders."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import math

try:
    from .warrant_spreads import exclusion, positive
except ImportError:
    from warrant_spreads import exclusion, positive

STRATEGIES = [
    {'id': 'volume2-h5', 'name': '量增 2 倍・持有 5 期', 'volume': 2, 'hold': 5},
    {'id': 'volume3-h5', 'name': '量增 3 倍・持有 5 期', 'volume': 3, 'hold': 5},
    {'id': 'volume2-h10', 'name': '量增 2 倍・持有 10 期', 'volume': 2, 'hold': 10},
    {'id': 'relative-low-h5', 'name': '相對價差偏低・持有 5 期', 'z': -2, 'hold': 5},
]
RULES = {'version': 'warrant-paper-v1', 'market': 'TWSE', 'initialCash': 100000,
         'budget': 10000, 'maxPositions': 5, 'newOrdersPerDay': 2, 'lotUnits': 1000,
         'participation': .01, 'minVolume': 100, 'minPrice': .1, 'maxOrderAgeDays': 4,
         'minDaysToLastTrade': 30, 'exitBufferDays': 10,
         'feeRate': .001425, 'minFee': 20, 'sellTax': .001, 'slippage': .01,
         'stopLoss': -.2, 'takeProfit': .3, 'reviewPeriods': 60, 'reviewEvery': 20,
         'minClosed': 30, 'minConfirmClosed': 10, 'segmentAdvantage': .02,
         'strategies': STRATEGIES, 'formalModelEligible': False}
LEGACY_RULES = deepcopy(RULES)
STRATEGIES = STRATEGIES + [
    {'id': 'trend-volume-h5', 'name': '量增順勢・持有 5 期', 'volume': 2, 'direction': 'trend', 'hold': 5},
    {'id': 'pullback-volume-h5', 'name': '量增逆勢回落・持有 5 期', 'volume': 2, 'direction': 'pullback', 'hold': 5},
    {'id': 'dual-low-volume-h5', 'name': '價差偏低＋量增・持有 5 期', 'volume': 2, 'z': -2, 'hold': 5},
]
RULES = {**RULES, 'version': 'warrant-paper-v2', 'strategies': STRATEGIES}
SOURCES = ('twse-warrants', 'twse-volume', 'twse-volume-put')
IDENTITY = ('code', 'underlying', 'cp', 'expiry')
TERMS = IDENTITY + ('strike', 'ratio', 'lastTrade', 'style')
RECORDED = '此行情日已記錄；新委託最快在後續行情日模擬成交，同日重跑不改寫委託、成交與策略紀錄。'


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def money(value):
    return round(value, 2)


def fee(amount, multiplier=1):
    return money(max(RULES['minFee'], amount * RULES['feeRate']) * multiplier)


def days(a, b):
    return (date.fromisoformat(a) - date.fromisoformat(b)).days


def cashflow(price, lots, side, stress=False):
    amount = price * lots * RULES['lotUnits'] * (1 + (1 if side == 'buy' else -1) * RULES['slippage'] * (2 if stress else 1))
    commission = fee(amount, 2 if stress else 1)
    tax = money(amount * RULES['sellTax']) if side == 'sell' else 0
    return money(amount + commission if side == 'buy' else amount - commission - tax)


def account():
    return {'cash': RULES['initialCash'], 'positions': [], 'orders': [], 'trades': [], 'curve': []}


def protected(report):
    keys = ('rules', 'startedOn', 'currentRule', 'lastMarketDate', 'dayCount', 'lastReviewCount', 'accounts', 'reviews')
    if report['rules']['version'] == 'warrant-paper-v2':
        keys += ('accountStartedOn', 'migrations')
    return {k: report[k] for k in keys}


def validate(report, legacy=False):
    if report['rules'] != (LEGACY_RULES if legacy else RULES) or report['stateSha256'] != digest(protected(report)):
        raise ValueError('Paper configuration or state changed; explicit version migration required')
    previous = None
    for event in report['journal']:
        if event['previousSha256'] != previous or event['sha256'] != digest({k: v for k, v in event.items() if k != 'sha256'}):
            raise ValueError('Paper journal integrity failure')
        previous = event['sha256']
    if report['journal'] and report['journal'][-1]['stateSha256'] != report['stateSha256']:
        raise ValueError('Paper state does not match journal')


def migrate(old, now, today):
    """Add zero-history candidates without touching prior accounts, events or reviews."""
    validate(old, legacy=True)
    report = deepcopy(old)
    report.update(schemaVersion=2, rules=deepcopy(RULES), accountStartedOn={k: old['startedOn'] for k in old['accounts']})
    for strategy in STRATEGIES:
        if strategy['id'] not in report['accounts']:
            report['accounts'][strategy['id']] = account()
            report['accountStartedOn'][strategy['id']] = today
    report['migrations'] = [{'at': now, 'from': LEGACY_RULES['version'], 'to': RULES['version'],
                             'previousStateSha256': old['stateSha256'], 'archive': 'warrant-lab-archives/warrant-paper-v1.json'}]
    report['stateSha256'] = digest(protected(report))
    event = {'type': 'strategy-extension', 'at': now, 'marketDate': None, 'actions': [],
             'stateSha256': report['stateSha256'], 'previousSha256': old['journal'][-1]['sha256'] if old['journal'] else None,
             'migration': report['migrations'][-1]}
    event['sha256'] = digest(event)
    report['journal'].append(event)
    validate(report)
    return report


def new_report(now, today):
    result = {'schemaVersion': 2, 'rules': deepcopy(RULES), 'createdAt': now, 'startedOn': today,
              'currentRule': STRATEGIES[0]['id'], 'lastMarketDate': None, 'dayCount': 0, 'lastReviewCount': 0,
              'accounts': {key: account() for key in ['adaptive'] + [s['id'] for s in STRATEGIES]},
              'reviews': [], 'journal': [], 'accountStartedOn': {key: today for key in ['adaptive'] + [s['id'] for s in STRATEGIES]}, 'migrations': []}
    result['stateSha256'] = digest(protected(result))
    return result


def same_issue(position, row):
    return row and all(position.get(k) == row.get(k) for k in IDENTITY)


def eligible(row, day):
    return (row and row.get('market') == 'TWSE' and row.get('date') == day and not exclusion(row)
            and row.get('lastTrade') and days(row['lastTrade'], day) >= RULES['minDaysToLastTrade']
            and row['price'] >= RULES['minPrice'] and row['volume'] >= RULES['minVolume'])


def matches(item, strategy):
    if 'volume' in strategy:
        value = item.get('volumeEvidence', {}).get('multiple')
        if not (positive(value) and value >= strategy['volume'] and 'volume-surge' in item.get('tags', [])):
            return False
    if strategy.get('direction'):
        changes = [item.get('pricePct'), item.get('underlyingPct')]
        baseline = item.get('volumeEvidence', {}).get('baselineDates', [])
        if (not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in changes)
                or not baseline or item.get('previousDate') != baseline[-1] or not baseline[-1] < item['date']):
            return False
        favorable = changes[1] * (1 if item.get('cp') == 'C' else -1 if item.get('cp') == 'P' else 0) > 0
        return favorable and (changes[0] > 0 if strategy['direction'] == 'trend' else changes[0] < 0)
    if 'z' not in strategy:
        return True
    pair = item.get('spreadEvidence') or {}
    z, spread = pair.get('z'), pair.get('spread')
    return (isinstance(z, (int, float)) and math.isfinite(z) and z <= strategy['z']
            and isinstance(spread, (int, float)) and math.isfinite(spread) and spread < 0
            and pair.get('signal') is True and pair.get('priorCount') == 20
            and pair.get('trainingEndDate', '9999') < item['date'])


def settle(acc, quotes, day, actions, key):
    remaining = []
    # Sells may release cash before previously queued buys; no new same-day decisions fill.
    for order in sorted(acc['orders'], key=lambda o: o['side'] == 'buy'):
        if day <= order['decisionDate']:
            remaining.append(order)
            continue
        row = quotes.get(order['code'])
        pos = next((p for p in acc['positions'] if p['code'] == order['code']), None)
        valid = row and same_issue(order, row)
        if order['side'] == 'buy':
            valid = (valid and eligible(row, day) and days(day, order['decisionDate']) <= RULES['maxOrderAgeDays'] and all(row.get(k) == order.get(k) for k in TERMS)
                     and pos is None and len(acc['positions']) < RULES['maxPositions'])
            lots = min(math.floor(row['volume'] * RULES['participation']), math.floor(order['budget'] / (row['price'] * 1000))) if valid else 0
            while lots > 0 and cashflow(row['price'], lots, 'buy') > min(order['budget'], acc['cash']):
                lots -= 1
            if not lots:
                actions.append({'account': key, 'type': 'cancel-buy', 'code': order['code'], 'reason': '次一行情日無有效報價、條款改變、量能／資金不足或已持有'})
                continue
            cost = cashflow(row['price'], lots, 'buy')
            acc['cash'] = money(acc['cash'] - cost)
            acc['positions'].append({**order, 'lots': lots, 'entryDate': day, 'entryClose': row['price'],
                                     'cost': cost, 'periods': 0, 'lastPrice': row['price'], 'markDate': day, 'stale': False})
            actions.append({'account': key, 'type': 'buy', 'code': order['code'], 'lots': lots, 'cost': cost, 'decisionDate': order['decisionDate'], 'strategy': order['strategy']})
        elif pos and valid and math.floor(row['volume'] * RULES['participation']) >= pos['lots']:
            proceeds = cashflow(row['price'], pos['lots'], 'sell')
            trade = {'code': pos['code'], 'name': pos['name'], 'underlying': pos['underlying'], 'strategy': pos['strategy'],
                     'entryDate': pos['entryDate'], 'exitDate': day, 'lots': pos['lots'], 'cost': pos['cost'],
                     'proceeds': proceeds, 'pnl': money(proceeds - pos['cost']), 'reason': order['reason'],
                     'stressPnl': money(cashflow(row['price'], pos['lots'], 'sell', True) - cashflow(pos['entryClose'], pos['lots'], 'buy', True))}
            acc['trades'].append(trade)
            acc['cash'] = money(acc['cash'] + proceeds)
            acc['positions'].remove(pos)
            actions.append({'account': key, 'type': 'sell', **trade})
        elif pos:
            remaining.append(order)
            actions.append({'account': key, 'type': 'unfilled-sell', 'code': pos['code'], 'reason': '缺有效行情／已停交易／量能不足，未假設成交'})
    acc['orders'] = remaining


def mark_and_plan(acc, quotes, focus, strategy, day, actions, key):
    value, complete = acc['cash'], True
    for pos in acc['positions']:
        row = quotes.get(pos['code'])
        valid = same_issue(pos, row) and math.floor(row['volume'] * RULES['participation']) >= pos['lots']
        pos['stale'] = not valid
        if valid:
            pos.update(lastPrice=row['price'], markDate=day)
            if pos['entryDate'] < day:
                pos['periods'] += 1
        else:
            complete = False
        value += cashflow(pos['lastPrice'], pos['lots'], 'sell')
        if any(o['code'] == pos['code'] and o['side'] == 'sell' for o in acc['orders']):
            continue
        change = pos['lastPrice'] / (pos['entryClose'] * (1 + RULES['slippage'])) - 1
        reason = ('到期前退出' if days((row or pos)['lastTrade'], day) <= RULES['exitBufferDays'] else
                  '停損條件' if valid and change <= RULES['stopLoss'] else
                  '停利條件' if valid and change >= RULES['takeProfit'] else
                  '持有期到達' if pos['periods'] >= pos['hold'] else None)
        if reason:
            order = {**{k: pos[k] for k in IDENTITY}, 'side': 'sell', 'decisionDate': day, 'reason': reason}
            acc['orders'].append(order)
            actions.append({'account': key, 'type': 'plan-sell', **order})
    acc['curve'].append({'date': day, 'equity': money(value) if complete else None,
                         'estimatedEquity': money(value), 'complete': complete, 'cash': acc['cash']})
    # Unresolved holdings block new exposure in that account.
    if not complete:
        return
    candidates = [i for i in focus if eligible(quotes.get(i['code']), day) and matches(i, strategy)
                  and all(i.get(k) == quotes[i['code']].get(k) for k in ('price', 'volume') + TERMS)]
    candidates.sort(key=lambda i: (-(i.get('volumeEvidence', {}).get('delta') or 0) if 'volume' in strategy else (i.get('spreadEvidence') or {}).get('z', 0), i['code']))
    held = {p['underlying'] for p in acc['positions']} | {o['underlying'] for o in acc['orders'] if o['side'] == 'buy'}
    available = acc['cash'] - sum(o['budget'] for o in acc['orders'] if o['side'] == 'buy')
    capacity = RULES['maxPositions'] - len(acc['positions']) - sum(o['side'] == 'buy' for o in acc['orders'])
    planned = 0
    for item in candidates:
        if planned >= min(RULES['newOrdersPerDay'], capacity):
            break
        if item['underlying'] in held or available < 100:
            continue
        row = quotes[item['code']]
        budget = min(RULES['budget'], available)
        if cashflow(row['price'], 1, 'buy') > budget:
            continue
        order = {**{k: row.get(k) for k in TERMS}, 'name': row['name'], 'side': 'buy', 'decisionDate': day,
                 'strategy': strategy['id'], 'hold': strategy['hold'], 'budget': budget,
                 'signal': {'volume': item.get('volumeEvidence'), 'spread': item.get('spreadEvidence'),
                            'direction': {k: item.get(k) for k in ('pricePct', 'underlyingPct', 'previousDate', 'date', 'cp')}}}
        acc['orders'].append(order)
        actions.append({'account': key, 'type': 'plan-buy', **order})
        available -= budget
        held.add(item['underlying'])
        planned += 1


def drawdown(points):
    peak, worst = points[0]['equity'] if points else RULES['initialCash'], 0
    for point in points:
        if point['equity'] is None:
            return None
        peak = max(peak, point['equity'])
        worst = max(worst, 1 - point['equity'] / peak)
    return worst


def review_metrics(acc):
    curve = acc['curve'][-61:]
    if len(curve) < 61 or any(p['equity'] is None or p['equity'] <= 0 for p in curve):
        return None
    start, split, end = (curve[i]['date'] for i in (0, 40, 60))
    trades = [t for t in acc['trades'] if start < t['exitDate'] <= end]
    confirm = [t for t in trades if t['exitDate'] > split]
    if len(trades) < RULES['minClosed'] or len(confirm) < RULES['minConfirmClosed']:
        return None
    return {'datesSha256': digest([p['date'] for p in curve]), 'firstReturn': curve[40]['equity'] / curve[0]['equity'] - 1,
            'confirmReturn': curve[60]['equity'] / curve[40]['equity'] - 1,
            'drawdown': drawdown(curve), 'closed': len(trades), 'confirmClosed': len(confirm),
            'firstStressPnl': sum(t['stressPnl'] for t in trades if t['exitDate'] <= split),
            'confirmStressPnl': sum(t['stressPnl'] for t in confirm)}


def review(report, day):
    if report['dayCount'] < 61 or (report['lastReviewCount'] and report['dayCount'] - report['lastReviewCount'] < RULES['reviewEvery']):
        return
    report['lastReviewCount'] = report['dayCount']
    metrics = {s['id']: review_metrics(report['accounts'][s['id']]) for s in STRATEGIES}
    baseline = metrics[report['currentRule']]
    qualified = []
    for key, m in metrics.items():
        if not baseline or not m or key == report['currentRule'] or m['datesSha256'] != baseline['datesSha256']:
            continue
        if (m['firstReturn'] > 0 and m['confirmReturn'] > 0
                and all(m[k] - baseline[k] >= RULES['segmentAdvantage'] for k in ('firstReturn', 'confirmReturn'))
                and m['drawdown'] <= baseline['drawdown']
                and all(m[k] > max(0, baseline[k]) for k in ('firstStressPnl', 'confirmStressPnl'))):
            qualified.append(key)
    chosen = max(qualified, key=lambda k: (min(metrics[k]['firstReturn'], metrics[k]['confirmReturn']), k)) if qualified else report['currentRule']
    report['reviews'].append({'date': day, 'from': report['currentRule'], 'to': chosen, 'metrics': metrics,
                              'result': '通過，後續新委託使用候選規則' if qualified else '樣本不足或未通過全部門檻，維持原規則'})
    report['currentRule'] = chosen


def build_lab(old, warrants, feeds, focus, now, today):
    day_today = today.isoformat()
    report = (migrate(old, now, day_today) if old and old.get('rules', {}).get('version') == LEGACY_RULES['version']
              else deepcopy(old) if old else new_report(now, day_today))
    validate(report)
    report.pop('checkedAt', None)
    report.update(status='waiting', error=None)
    sources = [{k: feeds.get(source, {}).get(k) for k in ('date', 'status')} | {'id': source} for source in SOURCES]
    report['sources'] = sources
    market_days = {s['date'] for s in sources}
    if any(s['status'] != 'ok' for s in sources) or len(market_days) != 1 or day_today not in market_days:
        report['message'] = '等待三個上市權證來源提供同日當日行情；不使用過期行情自動下模擬單。'
        return report
    day = day_today
    # The shortlist must come from this market day's sources, never a stale file from an earlier run.
    focus_dates = {s.get('id'): s.get('date') for s in (focus or {}).get('sources', [])}
    if not focus or focus.get('status') not in ('ok', 'partial') or any(focus_dates.get(source) != day for source in SOURCES):
        report.update(status='retained', message='每日觀察清單未成功更新，模擬帳戶暫停。')
        return report
    if report['lastMarketDate'] and day <= report['lastMarketDate']:
        report.update(status='ok', message=RECORDED)
        return report
    all_rows = [r for r in warrants if r.get('market') == 'TWSE']
    if len({r['code'] for r in all_rows}) != len(all_rows):
        raise ValueError('Duplicate warrant codes')
    quotes = {r['code']: r for r in all_rows if r.get('date') == day and not exclusion(r)}
    if not quotes:
        raise ValueError('No valid same-day warrants')
    focus_items = [i for i in focus.get('items', []) if i.get('market') == 'TWSE' and i.get('date') == day]
    actions, before = [], report['currentRule']
    for key, acc in report['accounts'].items():
        settle(acc, quotes, day, actions, key)
        strategy = next(s for s in STRATEGIES if s['id'] == (before if key == 'adaptive' else key))
        mark_and_plan(acc, quotes, focus_items, strategy, day, actions, key)
    report['dayCount'] += 1
    report['lastMarketDate'] = day
    review(report, day)
    report['stateSha256'] = digest(protected(report))
    entry = {'at': now, 'marketDate': day, 'inputSha256': digest({'warrants': all_rows, 'focus': focus_items, 'sources': sources}),
             'actions': actions, 'ruleBefore': before, 'ruleAfter': report['currentRule'], 'stateSha256': report['stateSha256'],
             'previousSha256': report['journal'][-1]['sha256'] if report['journal'] else None}
    entry['sha256'] = digest(entry)
    report['journal'].append(entry)
    report.update(status='ok', message=RECORDED, generatedAt=now)
    validate(report)
    return report


def collect_lab(output, warrants, feeds, now, today, read, save, focus=None):
    old = read(output / 'warrant-lab.json')
    # The shortlist comes from the same run; a failed one is saved as retained and pauses new decisions.
    if focus is None:
        focus = read(output / 'warrant-focus.json') or {}
    result = build_lab(old, warrants, feeds, focus, now, today)
    if old and old.get('rules', {}).get('version') == LEGACY_RULES['version']:
        archive = output / 'warrant-lab-archives' / 'warrant-paper-v1.json'
        existing = read(archive)
        if existing is not None and existing != old:
            raise ValueError('Existing legacy archive differs; refusing to overwrite')
        if existing is None:
            save(archive, old)
    save(output / 'warrant-lab.json', result)
    return result
