"""Fixed-rule, dated event study of partial warrant broker signals. No orders or network calls."""
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, median
import argparse
import hashlib
import json

try:
    from .daily_reports import read, save
    from .warrant_journal import digest, sessions, price_change
    from .warrant_history import trade_price
    from .warrant_focus import numeric
    from .trading_calendar import load_calendar, CalendarUnavailable
except ImportError:
    from daily_reports import read, save
    from warrant_journal import digest, sessions, price_change
    from warrant_history import trade_price
    from warrant_focus import numeric
    from trading_calendar import load_calendar, CalendarUnavailable

VERSION = 'branch-event-study-v2'
RULES = {'version': VERSION, 'minNetLots': 100, 'horizons': [1, 3, 5, 10], 'cooldownSessions': 11,
         'minimumSamples': 30, 'minimumDates': 10, 'minimumCoverage': .7,
         'baseline': '訊號後首個交易日收盤；觀察其後第1、3、5、10個交易日，不順延缺價。',
         'sampling': '同來源模式、同標的、同類權別訊號，首筆後11個交易日不重複納入；同日取較小腿淨張數最大者，再依代碼排序。'}
try:
    from .warrant_signals import SIGNAL_RULES
except ImportError:
    from warrant_signals import SIGNAL_RULES

LEGACY_VERSION = 'branch-event-study-v1'
LEGACY_SIGNAL_RULES = {'version':'warrant-relationship-v1','branchStreak':3,'rollSellDays':30,'rollBuyDays':60}
GROUPS = {'call-up': '上漲日認購連買', 'call-down': '下跌日認購連買',
          'put-up': '上漲日認售連買', 'put-down': '下跌日認售連買',
          'roll-C': '認購疑似換倉', 'roll-P': '認售疑似換倉', 'both': '認購＋認售雙邊買超'}
MODES = {'research': '歷史重建／遲到資料', 'forward': '啟用後預先封存'}
TERMS = ('strike', 'ratio', 'expiry', 'lastTrade', 'style')
IDENTITY = ('name', 'market', 'cp')
TAIPEI = timezone(timedelta(hours=8))


def optional(path, default):
    return read(path) if path.exists() else default


def prepare_version(folder, now, as_of):
    """Only the reviewed v1 -> v2 migration is allowed; freeze all old evidence first."""
    versions = optional(folder/'versions.json', {'active': VERSION, 'archived': []})
    # Recover the version selector if a prior run stopped after saving its cohort.
    for manifest in sorted((folder/'versions').glob('*/manifest.json')):
        entry = read(manifest)
        if not any(v['version'] == entry['version'] for v in versions['archived']):
            versions['archived'].append(entry)
    cohort = optional(folder/'cohort.json', None)
    prices = optional(folder/'prices.json', {'spots': {}, 'warrants': {}, 'revisions': []})
    if cohort and cohort['rules'].get('version') == LEGACY_VERSION:
        if cohort['rules'] != {**RULES, 'version': LEGACY_VERSION} or cohort.get('signalRules') != LEGACY_SIGNAL_RULES:
            raise ValueError('舊版規則與核准的遷移內容不符，保留原研究')
        archive = folder/'versions'/LEGACY_VERSION
        hashes = {}
        for name in ('cohort.json', 'prices.json', 'outcomes.json'):
            origin, target = folder/name, archive/name
            if not origin.exists():
                raise ValueError('舊版研究缺少必要紀錄，停止遷移：'+name)
            raw = origin.read_bytes()
            if target.exists() and target.read_bytes() != raw:
                # Failed retries may change only the flat result's status/error notice.
                # Such a notice is not new evidence and never replaces the frozen file.
                same_result = name == 'outcomes.json' and {
                    k:v for k,v in read(target).items() if k not in ('status','error')
                } == {k:v for k,v in read(origin).items() if k not in ('status','error')}
                if not same_result:
                    raise ValueError('封存已存在且內容不同，拒絕覆寫：'+name)
            hashes[name] = hashlib.sha256(target.read_bytes() if target.exists() else raw).hexdigest()
        # Validate every file before writing any archive bytes.
        for name in hashes:
            target = archive/name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((folder/name).read_bytes())
        metadata = optional(archive/'manifest.json', None)
        if metadata and metadata.get('sha256') != hashes:
            raise ValueError('封存校驗碼不一致，停止遷移')
        if metadata is None:
            old_result = read(folder/'outcomes.json')
            metadata = {'version': LEGACY_VERSION, 'sourceVersion': LEGACY_SIGNAL_RULES['version'],
                        'archivedAt': now, 'asOf': old_result.get('asOf'), 'events': len(cohort['items']),
                        'activatedAt': cohort['activatedAt'], 'activationAsOf': cohort['activationAsOf'],
                        'path': 'versions/'+LEGACY_VERSION, 'sha256': hashes,
                        'reason': '新版排除發行商總公司分點；舊訊號、價格與結果保持封存，不併入新版統計。'}
            save(archive/'manifest.json', metadata)
        if not any(v['version'] == LEGACY_VERSION for v in versions['archived']):
            versions['archived'].append(metadata)
        # Prices are independent dated observations, not samples or performance.
        # Reuse them by their existing identity keys; new signal evidence is captured afresh.
        cohort = {'rules': RULES, 'signalRules': SIGNAL_RULES, 'activatedAt': now, 'activationAsOf': as_of,
                  'seedPriceVersion': LEGACY_VERSION, 'items': []}
    elif cohort:
        if cohort['rules'] != RULES or cohort.get('signalRules') != SIGNAL_RULES:
            raise ValueError('研究或來源規則已變更；應建立新版本，不能覆寫既有樣本')
    else:
        cohort = {'rules': RULES, 'signalRules': SIGNAL_RULES, 'activatedAt': now, 'activationAsOf': as_of, 'items': []}
    versions['active'] = VERSION
    return cohort, prices, versions


def plan(day, calendar):
    try:
        return sessions(day, 11, calendar, direction=1)
    except CalendarUnavailable:
        return []


def qualifies(hint):
    return (numeric(hint.get('aNet')) and abs(hint['aNet']) >= RULES['minNetLots']
            and (not hint.get('b') or numeric(hint.get('bNet')) and abs(hint['bNet']) >= RULES['minNetLots']))


def event_key(day, underlying, hint):
    return digest([VERSION, day, underlying, hint['kind'], hint['branchCode'], hint['a'], hint.get('b')])


def capture_event(day, stock, hint, quotes, branch_payloads, now, cohort, calendar, source):
    if day > datetime.fromisoformat(now).astimezone(TAIPEI).date().isoformat():
        raise ValueError('拒絕封存未來訊號日')
    dates = plan(day, calendar)
    early = bool(dates) and datetime.fromisoformat(now) < datetime.combine(date.fromisoformat(dates[0]), time(9), TAIPEI)
    forward = day > cohort['activationAsOf'] and early
    group = hint['kind'] + '-' + hint['cp'] if hint['kind'] == 'roll' else hint['kind']
    family = ('streak-C' if hint['kind'].startswith('call-') else 'streak-P'
              if hint['kind'].startswith('put-') else group)
    legs, evidence = [], []
    for key in ('a', 'b'):
        if key not in hint:
            continue
        code = hint[key]
        quote = quotes.get((day, code), {})
        expected_cp = ('C' if key == 'a' else 'P') if hint['kind'] == 'both' else hint.get('cp', 'C' if hint['kind'].startswith('call-') else 'P')
        valid = quote.get('underlying') == stock['code'] and quote.get('cp') == expected_cp and quote.get('date') == day
        leg = {'key': key, 'code': code, 'cp': expected_cp, 'net': hint[key+'Net'],
               'name': quote.get('name') if valid else None, 'market': quote.get('market') if valid else None,
               'identityVerified': valid, 'termsDate': day if valid and quote.get('termsDate') == day else None}
        leg.update({k: quote.get(k) if leg['termsDate'] else None for k in TERMS})
        legs.append(leg)
        payload = branch_payloads.get(code, {})
        snapshot = next((s for s in payload.get('history', []) if s['date'] == day), {})
        history = {s['date']:s for s in payload.get('history', []) if s.get('date','') <= day}
        proof_days = sessions(day,11,calendar,include=True) if hint['kind'] not in ('roll','both') else [day]
        proof = []
        for d in proof_days:
            s = history.get(d)
            proof.append({'date':d,'status':'saved' if s else 'missing',
                          'quoteDate':s.get('quoteDate') if s else None,'quoteVolume':s.get('quoteVolume') if s else None,
                          'rows':[r for r in s.get('rows',[]) if r.get('branchCode')==hint['branchCode']] if s else [],
                          'snapshotHash':digest(s) if s else None})
        evidence.append({'code': code, 'url': payload.get('sourceUrl'), 'snapshotHash': digest(snapshot),
                         'history': proof})
    return {'id': event_key(day, stock['code'], hint), 'signalDate': day, 'underlying': stock['code'],
            'underlyingName': stock['name'], 'group': group, 'family': family,
            'mode': 'forward' if forward else 'research', 'recordedAt': now,
            'modeReason': '啟用後、基準日開盤前封存；不是成交或已驗證樣本外績效' if forward else '啟用前訊號或基準日開盤後才取得，非當時可用訊號',
            'hint': hint, 'hintHash': digest(hint), 'legs': legs, 'branchEvidence': evidence,
            'sources': source, 'strength': min(abs(l['net']) for l in legs)}


def representatives(events, calendar):
    """Choose before looking at outcomes; different branches and repeated streaks do not multiply a wave."""
    chosen, until = {}, {}
    ordered = sorted(events, key=lambda e: (e['signalDate'], -e['strength'], e['hint']['branchCode'], e['id']))
    for e in ordered:
        key = (e['mode'], e['underlying'], e['family'])
        prior = until.get(key)
        if prior and e['signalDate'] <= prior[0]:
            chosen[e['id']] = prior[1]
            continue
        dates = plan(e['signalDate'], calendar)
        chosen[e['id']] = None
        until[key] = (dates[10] if dates else '9999-12-31', e['id'])
    return chosen


def point_key(underlying, leg):
    return underlying + '/' + leg['code'] + '/' + digest([leg.get(k) for k in IDENTITY])


def update_prices(events, histories, dated_quotes, ledger, as_of, calendar):
    """Retain needed dated observations after the upstream 60-session history rolls away."""
    ledger.setdefault('spots', {}); ledger.setdefault('warrants', {}); ledger.setdefault('revisions', [])
    for event in events:
        dates = plan(event['signalDate'], calendar)
        needed = {dates[n] for n in [0] + RULES['horizons']} if dates else set()
        needed = {d for d in needed if d <= as_of}
        history = histories.get(event['underlying'], {})
        raw_spots = history.get('underlyingPrices', [])
        spot_counts = Counter(r[0] for r in raw_spots)
        spots = ledger['spots'].setdefault(event['underlying'], {})
        def put(target, key, value, reference):
            old = target.get(key)
            if old is not None and old != value:
                change = {'asOf': as_of, 'reference': reference, 'before': old, 'after': value}
                if not ledger['revisions'] or ledger['revisions'][-1] != change:
                    ledger['revisions'].append(change)
            target[key] = value
        for d, price in raw_spots:
            if d in needed:
                put(spots, d, {'price': price if spot_counts[d] == 1 else None}, event['underlying']+'/'+d)
        for leg in event['legs']:
            if not leg['identityVerified']:
                continue
            key = point_key(event['underlying'], leg)
            points = ledger['warrants'].setdefault(key, {})
            quote = history.get('quotes', {}).get(leg['code'], {})
            raw = quote.get('rows', []) if all(quote.get(k) == leg.get(k) for k in IDENTITY) else []
            # Dated official cache/current rows can fill gaps in the rolling display history.
            raw = list(raw)
            for d in needed:
                row = dated_quotes.get((d, leg['code']), {})
                if row.get('underlying') == event['underlying'] and all(row.get(k) == leg.get(k) for k in IDENTITY) and row.get('priceSource') == 'dated-official':
                    raw = [p for p in raw if p[0] != d] + [[d, row.get('price'), row.get('volume')]]
            counts = Counter(p[0] for p in raw)
            for d, price, volume in raw:
                if d not in needed:
                    continue
                old = points.get(d, {})
                row = dated_quotes.get((d, leg['code']), {})
                direct = row.get('priceSource') == 'dated-official' and row.get('underlying') == event['underlying'] and all(row.get(k) == leg.get(k) for k in IDENTITY)
                if old.get('source') == 'dated-official' and not direct:
                    continue
                dated = (row.get('date') == d and row.get('underlying') == event['underlying']
                         and all(row.get(k) == leg.get(k) for k in IDENTITY) and row.get('termsDate') == d)
                terms = {k: row.get(k) for k in TERMS} if dated else old.get('terms')
                value = {'price': price, 'volume': volume, 'terms': terms, 'duplicate': counts[d] != 1,
                         'source': 'dated-official' if direct else 'saved-history'}
                put(points, d, value, key+'/'+d)
    return ledger


def leg_result(leg, base, target, points, as_of):
    a, b = points.get(base), points.get(target)
    result = {'pct': None, 'status': 'pending', 'termsStatus': 'unknown', 'basePrice': None, 'targetPrice': None}
    if base <= as_of and a and not a.get('duplicate'):
        result['basePrice'] = trade_price(a['price'], a['volume'])
    if target > as_of:
        return result
    if not leg['identityVerified']:
        return {**result, 'status': 'identity'}
    limits = [v for v in (leg.get('lastTrade'), leg.get('expiry')) if v]
    if limits and target > min(limits):
        return {**result, 'status': 'expired'}
    if not a or not b:
        return {**result, 'status': 'missing-base' if not a else 'missing-target'}
    if a.get('duplicate') or b.get('duplicate'):
        return {**result, 'status': 'duplicate'}
    base_price, target_price = trade_price(a['price'], a['volume']), trade_price(b['price'], b['volume'])
    result.update(basePrice=base_price, targetPrice=target_price)
    ta, tb = a.get('terms'), b.get('terms')
    comparable = ta and tb and all(ta.get(k) is not None and tb.get(k) is not None for k in ('strike', 'ratio', 'expiry'))
    result['termsStatus'] = ('same' if all(ta[k] == tb[k] for k in ('strike', 'ratio', 'expiry')) else 'changed') if comparable else 'unknown'
    # An adjusted contract is not a constant holding. Do not manufacture a return across a known change.
    if result['termsStatus'] == 'changed':
        return {**result, 'status': 'adjusted'}
    if base_price is None or target_price is None:
        return {**result, 'status': 'no-trade-base' if base_price is None else 'no-trade-target'}
    return {**result, 'status': 'ok', 'pct': price_change(target_price, base_price)}


def evaluate(event, ledger, as_of, calendar):
    dates = plan(event['signalDate'], calendar)
    results = []
    for h in RULES['horizons']:
        if not dates:
            results.append({'horizon': h, 'date': None, 'stock': {'pct': None, 'status': 'calendar'},
                            'legs': {l['key']: {'pct': None, 'status': 'calendar', 'termsStatus': 'unknown', 'basePrice': None, 'targetPrice': None} for l in event['legs']}, 'rollGap': None})
            continue
        base, target = dates[0], dates[h]
        spots = ledger['spots'].get(event['underlying'], {})
        a, b = spots.get(base, {}).get('price'), spots.get(target, {}).get('price')
        stock_pct = price_change(b, a) if target <= as_of else None
        stock = {'pct': stock_pct, 'status': 'pending' if target > as_of else 'ok' if stock_pct is not None else 'missing-stock',
                 'basePrice': a if base <= as_of else None, 'targetPrice': b if target <= as_of else None}
        legs = {l['key']: leg_result(l, base, target, ledger['warrants'].get(point_key(event['underlying'], l), {}), as_of) for l in event['legs']}
        gap = legs['b']['pct']-legs['a']['pct'] if event['hint']['kind']=='roll' and all(v['pct'] is not None for v in legs.values()) else None
        results.append({'horizon': h, 'date': target, 'stock': stock, 'legs': legs, 'rollGap': gap})
    return {**{k:v for k,v in event.items() if k not in ('branchEvidence','sources')},
            'baselineDate': dates[0] if dates else None, 'results': results}


def stats(values):
    vals = sorted(v for v in values if numeric(v))
    def quantile(p):
        n = (len(vals)-1)*p; lo = int(n); hi = min(lo+1, len(vals)-1)
        return vals[lo] + (vals[hi]-vals[lo])*(n-lo)
    return {'n': len(vals), 'positive': sum(v > 0 for v in vals), 'median': median(vals) if vals else None,
            'mean': mean(vals) if vals else None, 'p25': quantile(.25) if vals else None, 'p75': quantile(.75) if vals else None}


def aggregate(outcomes, as_of):
    summaries = []
    for mode in MODES:
        for group in GROUPS:
            all_rows = [e for e in outcomes if e['mode'] == mode and e['group'] == group]
            sample = [e for e in all_rows if e['representative']]
            for h in RULES['horizons']:
                pairs = [(e, next(r for r in e['results'] if r['horizon']==h)) for e in sample]
                mature = [(e,r) for e,r in pairs if r['date'] and r['date'] <= as_of]
                roles = ('a','b') if group.startswith('roll-') or group=='both' else ('a',)
                metrics = {}
                for role in ['stock', *roles]:
                    rows = [r['stock'] if role=='stock' else r['legs'][role] for e,r in mature]
                    metrics[role] = {**stats(r['pct'] for r in rows), 'statuses': dict(Counter(r['status'] for r in rows)),
                                     'unknownTerms': sum(r['status']=='ok' and r.get('termsStatus')=='unknown' for r in rows)}
                usable = [(e,r) for e,r in mature if all(r['legs'][k]['status']=='ok' for k in roles) and r['stock']['status']=='ok']
                dates = len({e['signalDate'] for e,r in usable})
                coverage = len(usable)/len(mature) if mature else None
                enough = len(usable)>=RULES['minimumSamples'] and dates>=RULES['minimumDates'] and coverage>=RULES['minimumCoverage']
                direction = (-1 if group.startswith('put-') or group=='roll-P' else 1) if group!='both' else None
                summaries.append({'mode':mode, 'group':group, 'horizon':h, 'raw':len(all_rows), 'selected':len(sample),
                                  'mature':len(mature), 'pending':len(sample)-len(mature), 'usable':len(usable),
                                  'dates':dates, 'stocks':len({e['underlying'] for e,r in usable}), 'coverage':coverage,
                                  'metrics':metrics, 'stockDirection':stats(r['stock']['pct']*direction for e,r in mature if direction is not None and r['stock']['pct'] is not None),
                                  'rollGap':stats(r['rollGap'] for e,r in mature),
                                  'assessment':'可供後續驗證；尚未證明預測力' if enough else '資料／樣本不足，不能排名策略',
                                  'enough':enough})
    return summaries


def load_dated_quotes(output, codes, days, history_cache=None, terms_cache=None):
    """Optional one-time replay of existing, verified local caches; never download missing files."""
    quotes = {}
    current = optional(output/'warrants.json', {'rows': []})
    for row in current['rows']:
        if row['code'] in codes and row.get('date') in days:
            quotes[(row['date'],row['code'])] = {**row, 'priceSource':'dated-official'}
    if not history_cache:
        return quotes
    try:
        from .backfill_volume_reports import Sources, read_cache
    except ImportError:
        from backfill_volume_reports import Sources, read_cache
    source = Sources(history_cache, history_cache)
    for day in sorted(days):
        files = [history_cache/(name+'-'+day+'.json.gz') for name in ('twse-warrants','twse-volume','twse-volume-put','tpex-quotes')]
        if not all(p.exists() for p in files):
            continue
        listed, _ = source.listed(date.fromisoformat(day))
        otc, _ = source.otc(date.fromisoformat(day))
        term_file = terms_cache/('tpex-terms-'+day+'.json.gz') if terms_cache else None
        terms = read_cache(term_file) if term_file and term_file.exists() else None
        if terms and (terms['audit']['date'] != day or any(r.get('date') != day for r in terms['rows'])):
            raise ValueError('上櫃歷史條款日期不一致')
        indexed = {r['code']:r for r in terms['rows']} if terms else {}
        for row in otc:
            t = indexed.get(row['code'], {})
            if all(t.get(k) == row.get(k) for k in ('name','underlying','cp')):
                row.update({k:t.get(k) for k in TERMS}); row['termsDate'] = day
        for row in listed+otc:
            if row['code'] in codes:
                quotes[(day,row['code'])] = {**row, 'priceSource':'dated-official'}
    return quotes


def update(output, now=None, history_cache=None, terms_cache=None):
    now = now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    folder = output/'warrant-signal-backtest'
    index = read(output/'warrant-relationship/index.json')
    if index.get('status') != 'ok':
        raise ValueError('等待近月價量與分點資料完成核對')
    as_of = index['end']; calendar = load_calendar(output)
    if any(index.get('rules',{}).get(k) != v for k,v in SIGNAL_RULES.items()):
        raise ValueError('來源線索規則已改變；需建立新的研究版本')
    cohort, ledger, versions = prepare_version(folder, now, as_of)
    events = {e['id']:e for e in cohort['items']}
    observed, covered, candidates, stock_days = {}, set(), [], {}
    source_coverage = {'eligibleRecords': 0, 'excludedRecords': 0, 'excludedHints': 0}
    for path in sorted((output/'warrant-relationship/stocks').glob('*.json')):
        stock = read(path); stock_days[stock['code']] = stock['days']
        for row in stock['days']:
            if row.get('signalRules') != SIGNAL_RULES:
                source_coverage['excludedRecords'] += 1
                source_coverage['excludedHints'] += len(row['hints'])
                continue
            source_coverage['eligibleRecords'] += 1
            day = row['date']; covered.add((stock['code'],day))
            for hint in row['hints']:
                key = event_key(day,stock['code'],hint); observed[key] = digest(hint)
                if qualifies(hint) and key not in events:
                    candidates.append((day,stock,hint))
    codes = {l['code'] for e in events.values() for l in e['legs']}
    days = set()
    for e in events.values():
        days.add(e['signalDate']); days.update(d for d in plan(e['signalDate'],calendar) if d<=as_of)
    for day,stock,hint in candidates:
        codes.add(hint['a'])
        if hint.get('b'): codes.add(hint['b'])
        days.add(day); days.update(d for d in plan(day,calendar) if d<=as_of)
    underlyings = {e['underlying'] for e in events.values()} | {s['code'] for d,s,h in candidates}
    histories = {u:optional(output/'warrant-history'/(u+'.json'),{}) for u in underlyings}
    quotes = load_dated_quotes(output,codes,days,history_cache,terms_cache)
    # Rolling history clears reused codes when their identity changes. Never import its latest terms into a past date.
    for u,history in histories.items():
        for code,q in history.get('quotes',{}).items():
            if code not in codes: continue
            for d,p,v in q.get('rows',[]):
                if d in days and (d,code) not in quotes:
                    quotes[(d,code)] = {'date':d,'code':code,'underlying':u,**{k:q.get(k) for k in IDENTITY},
                                        'price':p,'volume':v,'termsDate':None,'priceSource':'saved-history'}
        price_counts = Counter(r[0] for r in history.get('underlyingPrices',[]))
        prices = {d:p if price_counts[d]==1 else None for d,p in history.get('underlyingPrices',[])}
        prices.update({d['date']:d['spot'] for d in stock_days.get(u,[])})
        history['underlyingPrices'] = sorted(prices.items())
    branch_codes = {h[k] for d,s,h in candidates for k in ('a','b') if k in h}
    payloads = {c:optional(output/'warrant-branches'/(c+'.json'),{}) for c in branch_codes}
    sources = read(output/'warrant-relationship/sources.json')['days']
    for day,stock,hint in candidates:
        event = capture_event(day,stock,hint,quotes,payloads,now,cohort,calendar,sources.get(day,{}))
        events[event['id']] = event
    cohort['items'] = sorted(events.values(),key=lambda e:(e['signalDate'],e['id']))
    # Keep source dates/URLs after the near-month report and display histories roll away.
    ledger.setdefault('sources',{}).update({d:sources[d] for d in days if d<=as_of and d in sources})
    update_prices(cohort['items'],histories,quotes,ledger,as_of,calendar)
    selected = representatives(cohort['items'],calendar)
    outcomes = []
    for event in cohort['items']:
        out = evaluate(event,ledger,as_of,calendar)
        out['representative'] = selected[event['id']] is None
        out['overlapsWith'] = selected[event['id']]
        out['evidenceState'] = ('same' if observed[event['id']]==event['hintHash'] else 'changed') if event['id'] in observed else (
            'no-longer-observed' if (event['underlying'],event['signalDate']) in covered else 'outside-window')
        outcomes.append(out)
    data = {'schemaVersion':2,'status':'ok','asOf':as_of,'activatedAt':cohort['activatedAt'],
            'activationAsOf':cohort['activationAsOf'],'rules':RULES,'signalRules':SIGNAL_RULES,'groups':GROUPS,'modes':MODES,
            'sourceCoverage':source_coverage,
            'summary':aggregate(outcomes,as_of),'items':outcomes,'priceRevisionCount':len(ledger['revisions']),
            'limits':['分點排行有限，分點不是單一客戶；沒有全市場或個人完整持倉。',
                      '本版排除依權證與分點名稱判定的發行商總公司；這是疑似造市通路判定，不是已確認帳戶身分。同券商其他分點仍保留標示。',
                      '只納入逐個股、逐日明確標記相同來源規則的線索，未標記或其他版本不混入。舊版結果完整封存，現有日期價格可依同一身分沿用。',
                      '連續買超：同分點同權證至少3個交易日淨買超入榜，缺日或未入榜即中斷；現股無漲跌或缺價日不分類。',
                      '疑似換倉：同分點、同標的、同權別，剩30日內權證賣超與剩60日以上權證買超並存；各取淨張數最大一檔。',
                      '雙邊買超：同分點、同標的、同日認購與認售各取買超淨張數最大一檔；不要求相同履約價或到期日。',
                      '每腿淨買賣至少100張，固定門檻，不依回測結果調參；並非大戶認定。',
                      '先固定訊號證據與身分，再更新後續價格。啟用前及遲到資料一律列歷史研究。',
                      '現股與權證均為未還原收盤價變動，不是含息、扣費或可成交損益；未估入稅、手續費、滑價與買賣價差。',
                      '疑似換倉比較新舊權證各自漲跌；差值不是等曝險多空報酬。雙邊買超不合成組合收益。',
                      '已知履約價、行使比例或到期日改變時排除權證漲跌；歷史條款不全時仍列原價變動，另計條款未核對筆數。',
                      '無成交、到期、缺基準或觀察日價格各自列示，不順延、不填零、不把到期直接當成損失100%。',
                      '尚未建立無訊號對照組或控制市場趨勢、槓桿、存續天數與波動率，不能把漲跌差異稱為預測力。',
                      '同股同類訊號去除11交易日內重疊後，跨股仍受共同市場影響，並非完全獨立樣本。',
                      '每組至少30筆完整代表樣本、10個訊號日期、成熟樣本完整率70%才標示可供後續驗證，仍不表示交易策略有效。']}
    save(folder/'cohort.json',cohort); save(folder/'prices.json',ledger); save(folder/'outcomes.json',data)
    save(folder/'versions.json',versions)
    return data


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,default=Path('data/derivatives'))
    parser.add_argument('--history-cache',type=Path);parser.add_argument('--terms-cache',type=Path)
    args=parser.parse_args()
    try:
        data=update(args.data,history_cache=args.history_cache,terms_cache=args.terms_cache)
        print(json.dumps({'asOf':data['asOf'],'events':len(data['items']),
                          'representatives':sum(e['representative'] for e in data['items']),
                          'modes':dict(Counter(e['mode'] for e in data['items']))},ensure_ascii=False))
    except Exception as exc:
        path=args.data/'warrant-signal-backtest/outcomes.json'
        old=optional(path,{'items':[],'summary':[]})
        save(path,{**old,'status':'retained','error':str(exc)[:180]})
        raise


if __name__=='__main__':
    main()
