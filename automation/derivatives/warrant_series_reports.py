"""Weekly and monthly descriptive reports on warrant and underlying closing-price series, with no AI calls.

Every Saturday the weekly report covers Monday to Friday; on the first Saturday of a month the monthly
report covers the previous calendar month. Numbers describe the saved series only: warrants are compared
on the same saved dates as their underlying, untraded days stay missing, and nothing is ranked as a strategy.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from html import escape
import argparse
import json
import math
import re
from pathlib import Path
from statistics import median

if __package__:
    from .publication import save_text
    from .trading_calendar import load_calendar
    from .report_evidence import daily_report, daily_html, period_quality, quality_html, strategy_review, followup_review, history_backfill, institutional_review
else:
    from publication import save_text
    from trading_calendar import load_calendar
    from report_evidence import daily_report, daily_html, period_quality, quality_html, strategy_review, followup_review, history_backfill, institutional_review

RULES = {'version': 'warrant-series-v2', 'liquidShare': 0.8, 'liquidMinDays': 3, 'minPairs': 10,
         'topUnderlyings': 30, 'topWarrants': 5,
         # Fixed thresholds behind the readings: |median underlying move| (%, small/clear/large), correlation
         # (low/mid/high), direction-consistency share (%), liquid share of listed warrants (%),
         # top-10 volume share (%), and the period-on-period change that counts as clear (%).
         'readings': {'move': {'weekly': [1, 3, 7], 'monthly': [2, 5, 10]}, 'corr': [0.5, 0.8], 'consistent': [70, 90],
                      'liquidShare': [15, 30], 'concentration': [30, 50], 'change': 20},
         # Stocks worth a closer look: first the eligibility floor (volume rank, a base day before the period,
         # enough liquid warrants and, monthly, enough slopes), then the top N per anomaly reason.
         'watch': {'volumeRank': 100, 'minLiquid': 10, 'minSideLiquid': 3, 'minSlopes': 10, 'perReason': 10,
                   'slopeDeviation': 1.0, 'changePct': 20},
         'basis': '權證與標的只比較同一批已收錄資料日；無成交日收盤價維持缺值，不補零',
         'description': '固定規則的描述統計與固定門檻的判讀；不是策略績效、不預測、不構成買賣建議'}
LIMITATIONS = [
    '資料為每日收盤，沒有盤中、委託簿與開高低；權證無成交的日子沒有收盤價，期間報酬只用有成交的日子計算。',
    '權證歷史檔只保留最近 60 個資料日，期間起點前沒有資料時以期間內第一個資料日為基準，並在表中標示。',
    '同一標的的權證共用同一段行情，報酬與斜率不是獨立樣本；單週 5 個資料日、單月約 20 個資料日，統計量的變動主要反映樣本短。',
    '斜率是權證日報酬對標的日報酬的最小平方法係數，只在配對段數達門檻時計算，且未扣除時間價值衰減與條款調整。',
    '報告不比較策略、不預測，也不構成任何買賣建議；同一期間內容改變另存新版，舊版本保留。',
]
KIND_NAMES = {'daily': '日報', 'weekly': '週報', 'monthly': '月報'}
SAFE_CODE = re.compile(r'[A-Za-z0-9_-]{1,24}')


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def positive(value):
    return numeric(value) and value > 0


def pct(current, previous):
    return (current / previous - 1) * 100 if numeric(current) and positive(previous) else None


def rounded(value, digits=2):
    return round(value, digits) if numeric(value) else None


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None


def save_json(path, data):
    save_text(path, json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False))


def weekly_period(today):
    """Monday to Friday of the latest week whose Friday is on or before today."""
    friday = today - timedelta(days=(today.weekday() - 4) % 7)
    return friday - timedelta(days=4), friday


def monthly_period(today):
    """The previous calendar month."""
    last = today.replace(day=1) - timedelta(days=1)
    return last.replace(day=1), last


def kinds_for(today, kind):
    if kind == 'auto':
        return ['weekly'] + (['monthly'] if today.day <= 7 else [])
    return ['weekly', 'monthly'] if kind == 'both' else [kind]


def trading_days(calendar, start, end):
    days, cursor = [], start
    while cursor <= end:
        if calendar.is_open(cursor):
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def regression(pairs):
    """Least-squares slope and correlation of warrant returns on underlying returns."""
    xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0 or syy <= 0:
        return None, None
    return sxy / sxx, sxy / math.sqrt(sxx * syy)


def consistent(warrant_pct, underlying_pct, cp):
    """Calls are consistent when they move with the underlying, puts when they move against it."""
    if not numeric(warrant_pct) or not numeric(underlying_pct) or warrant_pct == 0 or underlying_pct == 0:
        return None
    same = (warrant_pct > 0) == (underlying_pct > 0)
    return same if cp == 'C' else not same


def side_summary(items, with_slopes):
    """Describe one side (calls or puts) of an underlying's liquid warrants."""
    returns = [w['returnPct'] for w in items if numeric(w['returnPct'])]
    flags = [w['consistent'] for w in items if w['consistent'] is not None]
    summary = {'liquid': len(items), 'returns': len(returns), 'consistentSamples': len(flags),
               'medianReturnPct': rounded(median(returns)) if returns else None,
               'consistentShare': rounded(100 * sum(flags) / len(flags)) if flags else None}
    if with_slopes:
        slopes = [w['slope'] for w in items if numeric(w['slope'])]
        corrs = [w['corr'] for w in items if numeric(w['corr'])]
        summary.update(slopes=len(slopes), medianSlope=rounded(median(slopes), 3) if slopes else None,
                       medianCorr=rounded(median(corrs), 3) if corrs else None)
    return summary


def analyse_underlying(history, start, end, want_slopes, days_traded, volume_by_day):
    """Return the period statistics of one underlying's warrants, or None when none were listed."""
    prices = {d: p for d, p in history.get('underlyingPrices', []) if positive(p)}
    quotes = history.get('quotes', {})
    dates = sorted({r[0] for q in quotes.values() for r in q['rows']})
    if not any(start <= d <= end for d in dates):
        return None
    before = [d for d in dates if d < start]
    base_day = before[-1] if before else None
    priced_before = [d for d in sorted(prices) if d < start]
    priced_in = [d for d in sorted(prices) if start <= d <= end]
    u_base = priced_before[-1] if priced_before else priced_in[0] if priced_in else None
    u_end = priced_in[-1] if priced_in else None
    u_pct = pct(prices.get(u_end), prices.get(u_base)) if u_base and u_end and u_base != u_end else None
    warrants, markets = [], Counter()
    listed = traded = liquid = 0
    volume = 0
    coverage = Counter()
    market_days = defaultdict(set)
    period_dates = [d for d in dates if start <= d <= end]
    coverage['missingSpotDays'] = sum(d not in prices for d in period_dates)
    for code, q in quotes.items():
        rows = sorted(q['rows'])
        in_period = [r for r in rows if start <= r[0] <= end]
        if not in_period:
            continue
        market_days[q.get('market') or '?'].update(r[0] for r in in_period)
        markets[q.get('market')] += 1
        listed += 1
        traded_rows = [r for r in in_period if positive(r[1]) and positive(r[2])]
        coverage['missingVolumeRows'] += sum(not numeric(r[2]) for r in in_period)
        coverage['zeroVolumeRows'] += sum(r[2] == 0 for r in in_period)
        coverage['missingTradedPriceRows'] += sum(positive(r[2]) and not positive(r[1]) for r in in_period)
        w_volume = sum(r[2] for r in in_period if numeric(r[2]))
        volume += w_volume
        for r in in_period:
            if positive(r[1]) and positive(r[2]):
                days_traded[r[0]] += 1
            if numeric(r[2]):
                volume_by_day[r[0]] += r[2]
        if not traded_rows:
            continue
        traded += 1
        is_liquid = len(traded_rows) >= RULES['liquidMinDays'] and len(traded_rows) >= RULES['liquidShare'] * len(in_period)
        liquid += is_liquid
        prior = [r for r in rows if r[0] < start and positive(r[1]) and positive(r[2])]
        w_pct = pct(traded_rows[-1][1], prior[-1][1]) if prior else None
        matched_stock_pct = pct(prices.get(traded_rows[-1][0]), prices.get(prior[-1][0])) if prior else None
        slope = corr = None
        pairs = 0
        if want_slopes:
            seq = [r for r in rows if r[0] <= end and positive(r[1]) and positive(r[2]) and r[0] in prices and (base_day is None or r[0] >= base_day)]
            paired = [(pct(prices[b[0]], prices[a[0]]), pct(b[1], a[1])) for a, b in zip(seq, seq[1:]) if start <= b[0] <= end]
            pairs = len(paired)
            if pairs >= RULES['minPairs']:
                slope, corr = regression(paired)
        warrants.append({'code': code, 'name': q.get('name'), 'cp': q.get('cp'), 'strike': q.get('strike'), 'expiry': q.get('expiry'),
                         'listedDays': len(in_period), 'tradedDays': len(traded_rows), 'volume': w_volume, 'liquid': is_liquid,
                         'returnPct': rounded(w_pct), 'baseDate': prior[-1][0] if prior else None, 'endDate': traded_rows[-1][0],
                         'matchedStockReturnPct': rounded(matched_stock_pct),
                         'consistent': consistent(w_pct, matched_stock_pct, q.get('cp')), 'pairs': pairs,
                         'slope': rounded(slope, 3), 'corr': rounded(corr, 3)})
    liquid_items = [w for w in warrants if w['liquid']]
    return {'code': history['underlying'], 'name': history.get('name') or history['underlying'],
            'market': markets.most_common(1)[0][0] if markets else None,
            'underlyingReturnPct': rounded(u_pct), 'baseDate': u_base, 'endDate': u_end,
            'baseBeforePeriod': bool(priced_before) if u_base else None, 'pricedDays': len(priced_in),
            'warrants': listed, 'traded': traded, 'liquid': liquid, 'volume': volume,
            'coverage': {'counts': dict(coverage), 'dataDays': period_dates, 'marketDays': {k: sorted(v) for k, v in market_days.items()}},
            'call': side_summary([w for w in liquid_items if w['cp'] == 'C'], want_slopes),
            'put': side_summary([w for w in liquid_items if w['cp'] == 'P'], want_slopes),
            '_warrants': warrants}


def build_report(output, kind, start, end):
    calendar = load_calendar(output)
    days = trading_days(calendar, start, end)
    index = read_json(output / 'warrant-history.json') or {'items': []}
    universe = read_json(output / 'stock-warrant-directory.json')
    if not universe or universe.get('status') != 'ok' or not universe.get('items'):
        raise ValueError('等待可核對的個股名錄；不以代碼猜測股票或 ETF')
    stock_codes = {item['code'] for item in universe['items']}
    days_traded, volume_by_day = Counter(), Counter()
    underlyings = []
    start_key, end_key = start.isoformat(), end.isoformat()
    for item in index.get('items', []):
        code = item.get('code', '')
        if not SAFE_CODE.fullmatch(code) or code not in stock_codes:
            continue
        history = read_json(output / 'warrant-history' / f'{code}.json')
        if not history or history.get('underlying') != code:
            continue
        entry = analyse_underlying(history, start_key, end_key, kind == 'monthly', days_traded, volume_by_day)
        if entry:
            underlyings.append(entry)
    underlyings.sort(key=lambda u: (-u['volume'], u['code']))
    data_days = sorted(set(days_traded) | set(volume_by_day))
    by_market = defaultdict(lambda: {'underlyings': 0, 'warrants': 0, 'traded': 0, 'liquid': 0, 'volume': 0})
    for u in underlyings:
        m = by_market[u['market'] or '?']
        m['underlyings'] += 1
        for key in ('warrants', 'traded', 'liquid', 'volume'):
            m[key] += u[key]
    all_liquid = [w for u in underlyings for w in u['_warrants'] if w['liquid']]
    sides = {cp: side_summary([w for w in all_liquid if w['cp'] == cp], kind == 'monthly') for cp in ('C', 'P')}
    u_returns = [u['underlyingReturnPct'] for u in underlyings if numeric(u['underlyingReturnPct'])]
    market = {'tradingDays': days, 'dataDays': data_days, 'missingDays': [d for d in days if d not in data_days],
              'extraDays': [d for d in data_days if d not in days],
              'underlyings': len(underlyings), 'warrants': sum(u['warrants'] for u in underlyings),
              'traded': sum(u['traded'] for u in underlyings), 'liquid': len(all_liquid),
              'volume': sum(u['volume'] for u in underlyings), 'byMarket': dict(by_market),
              'byDay': [{'date': d, 'warrantsTraded': days_traded.get(d, 0), 'volume': volume_by_day.get(d, 0)} for d in data_days],
              'underlyingReturn': {'count': len(u_returns), 'medianPct': rounded(median(u_returns)) if u_returns else None,
                                   'up': sum(r > 0 for r in u_returns), 'down': sum(r < 0 for r in u_returns), 'flat': sum(r == 0 for r in u_returns)},
              'call': sides['C'], 'put': sides['P'],
              'topVolumeShare': rounded(100 * sum(u['volume'] for u in underlyings[:10]) / sum(u['volume'] for u in underlyings)) if any(u['volume'] for u in underlyings) else None}
    for rank, u in enumerate(underlyings):
        ws = u.pop('_warrants')
        if rank < RULES['topUnderlyings']:
            u['topWarrants'] = sorted(ws, key=lambda w: (-w['volume'], w['code']))[:RULES['topWarrants']]
    previous = previous_report(output, kind, start_key)
    report = {'schemaVersion': 2, 'kind': kind, 'period': {'start': start_key, 'end': end_key},
              'rules': RULES, 'historyLimit': index.get('limit'), 'market': market, 'underlyings': underlyings,
              'previous': previous, 'limitations': LIMITATIONS}
    report['quality'] = period_quality(report, universe)
    report['followupReview'] = followup_review(output, start_key, end_key)
    report['historyBackfill'] = history_backfill(output, start_key, end_key)
    report['institutionalReview'] = institutional_review(output, start_key, end_key)
    report['status'] = 'partial' if market['missingDays'] else 'ok' if market['dataDays'] else 'missing'
    if kind == 'monthly':
        report['strategyReview'] = strategy_review(output, end_key)
    top = [dict(w, underlying=u['code'], underlyingName=u['name']) for u in underlyings for w in u.get('topWarrants', [])]
    report['topWarrants'] = sorted(top, key=lambda w: (-w['volume'], w['code']))[:RULES['topWarrants'] * 2]
    report['observations'] = observations(report)
    report['watch'] = watch_list(report)
    report['readings'] = readings(report)
    return report


def watch_list(report):
    """Stocks worth a closer look this period: eligible by sample size, then ranked by how unusual their numbers are."""
    m, kind, t = report['market'], report['kind'], RULES['watch']
    clear_move = RULES['readings']['move'][kind][1]
    excluded, eligible = Counter(), []
    for rank, u in enumerate(report['underlyings'], 1):
        if rank > t['volumeRank']:
            excluded[f"成交量未進前 {t['volumeRank']} 名"] += 1
        elif not u['baseBeforePeriod']:
            excluded['期間前沒有基準日'] += 1
        elif u['liquid'] < t['minLiquid']:
            excluded[f"常交易不足 {t['minLiquid']} 檔"] += 1
        elif kind == 'monthly' and u['call'].get('slopes', 0) < t['minSlopes']:
            excluded[f"有斜率的認購不足 {t['minSlopes']} 檔"] += 1
        else:
            eligible.append(u)
    reasons = defaultdict(list)

    def pick(candidates, key, label):
        for u in sorted(candidates, key=lambda u: (key(u), -u['volume'], u['code']))[:t['perReason']]:
            reasons[u['code']].append(label(u))

    def weakest_side(u):
        sides = [(u[s]['consistentShare'], name) for s, name in (('call', '認購同向'), ('put', '認售反向'))
                 if u[s]['liquid'] >= t['minSideLiquid'] and u[s]['consistentShare'] is not None]
        return min(sides) if sides else None

    pick([u for u in eligible if weakest_side(u) and weakest_side(u)[0] < RULES['readings']['consistent'][0]],
         lambda u: weakest_side(u)[0],
         lambda u: f"方向一致偏低：{weakest_side(u)[1]}只有 {weakest_side(u)[0]:.0f}%")
    pick([u for u in eligible if numeric(u['underlyingReturnPct']) and abs(u['underlyingReturnPct']) >= clear_move],
         lambda u: -abs(u['underlyingReturnPct']),
         lambda u: f"標的{'明顯' if abs(u['underlyingReturnPct']) < RULES['readings']['move'][kind][2] else '大幅'}{'上漲' if u['underlyingReturnPct'] > 0 else '下跌'} {u['underlyingReturnPct']:+.2f}%")
    market_slope = m['call'].get('medianSlope')
    if kind == 'monthly' and numeric(market_slope):
        pick([u for u in eligible if numeric(u['call'].get('medianSlope')) and abs(u['call']['medianSlope'] - market_slope) >= t['slopeDeviation']],
             lambda u: -abs(u['call']['medianSlope'] - market_slope),
             lambda u: f"認購斜率中位數 {u['call']['medianSlope']:.3f}，與市場中位數 {market_slope:.3f} 相差 {u['call']['medianSlope'] - market_slope:+.2f}")
    previous = (report.get('previous') or {}).get('stocks') or {}

    def change(u):
        before = previous.get(u['code'], {}).get('liquid')
        return (u['liquid'] / before - 1) * 100 if positive(before) else None

    pick([u for u in eligible if change(u) is not None and abs(change(u)) >= t['changePct']],
         lambda u: -abs(change(u)),
         lambda u: f"常交易檔數 {previous[u['code']]['liquid']}→{u['liquid']}（{change(u):+.0f}%）")
    by_code = {u['code']: u for u in eligible}
    items = [{'code': code, 'name': by_code[code]['name'], 'market': by_code[code]['market'], 'volume': by_code[code]['volume'],
              'underlyingReturnPct': by_code[code]['underlyingReturnPct'], 'liquid': by_code[code]['liquid'],
              'call': {k: by_code[code]['call'].get(k) for k in ('medianReturnPct', 'consistentShare', 'medianSlope')},
              'put': {k: by_code[code]['put'].get(k) for k in ('medianReturnPct', 'consistentShare', 'medianSlope')},
              'reasons': texts} for code, texts in reasons.items()]
    items.sort(key=lambda i: (-len(i['reasons']), -i['volume'], i['code']))
    return {'eligible': len(eligible), 'excluded': dict(excluded), 'items': items,
            'basis': f"合格條件：成交量前 {t['volumeRank']} 名、期間前有基準日、常交易至少 {t['minLiquid']} 檔"
                     + (f"、有斜率的認購至少 {t['minSlopes']} 檔" if kind == 'monthly' else '')
                     + f"；理由各取前 {t['perReason']} 名後合併，理由多者在前。名單只表示數字特別，不表示機會或風險。"}


def previous_report(output, kind, start_key):
    index = read_json(output / 'warrant-series-reports.json') or {'items': []}
    candidates = [i for i in index.get('items', []) if i.get('kind') == kind and i.get('periodEnd', '') < start_key]
    if not candidates:
        return None
    last = max(candidates, key=lambda i: (i['periodEnd'], i['id']))
    saved = read_json(output / 'warrant-series-reports' / f"{last['id']}.json")
    if not saved or saved.get('rules', {}).get('version') != RULES['version']:
        return None
    m = saved['market']
    return {'id': last['id'], 'period': saved['period'],
            'market': {k: m.get(k) for k in ('underlyings', 'warrants', 'traded', 'liquid', 'volume')},
            'call': {k: m.get('call', {}).get(k) for k in ('medianReturnPct', 'medianSlope', 'consistentShare')},
            'put': {k: m.get('put', {}).get(k) for k in ('medianReturnPct', 'medianSlope', 'consistentShare')},
            'underlyingReturn': m.get('underlyingReturn'),
            # Liquid counts of the stocks that were eligible last period, for the activity-change reason.
            'stocks': {u['code']: {'liquid': u['liquid'], 'volume': u['volume']} for u in saved.get('underlyings', [])
                       if u.get('liquid', 0) >= RULES['watch']['minLiquid']}}


def fmt_pct(value):
    return '—' if value is None else f'{value:+.2f}%'


def observations(report):
    m, kind = report['market'], report['kind']
    label = KIND_NAMES[kind]
    out = [f"{report['period']['start']} 至 {report['period']['end']}：交易日曆 {len(m['tradingDays'])} 天，已收錄 {len(m['dataDays'])} 個資料日"
           + (f"，缺 {'、'.join(m['missingDays'])}" if m['missingDays'] else '') + '。']
    out.append(f"{m['underlyings']} 個標的、{m['warrants']} 檔權證曾掛牌；{m['traded']} 檔至少成交一天，{m['liquid']} 檔常交易（成交日達 {int(RULES['liquidShare'] * 100)}% 且至少 {RULES['liquidMinDays']} 天）。")
    if m['underlyingReturn']['count']:
        out.append(f"{m['underlyingReturn']['count']} 個標的有期間報酬，中位數 {fmt_pct(m['underlyingReturn']['medianPct'])}；常交易認購期間報酬中位數 {fmt_pct(m['call']['medianReturnPct'])}（{m['call']['returns']} 檔）、認售 {fmt_pct(m['put']['medianReturnPct'])}（{m['put']['returns']} 檔）。")
    if m['call']['consistentShare'] is not None or m['put']['consistentShare'] is not None:
        out.append(f"方向一致比例：認購與標的同向 {m['call']['consistentShare'] if m['call']['consistentShare'] is not None else '—'}%，認售與標的反向 {m['put']['consistentShare'] if m['put']['consistentShare'] is not None else '—'}%；持平與缺基準的權證不計。")
    if kind == 'monthly':
        out.append(f"配對段數達 {RULES['minPairs']} 段的常交易權證：認購 {m['call']['slopes']} 檔，斜率中位數 {m['call']['medianSlope'] if m['call']['medianSlope'] is not None else '—'}、相關中位數 {m['call']['medianCorr'] if m['call']['medianCorr'] is not None else '—'}；認售 {m['put']['slopes']} 檔，斜率中位數 {m['put']['medianSlope'] if m['put']['medianSlope'] is not None else '—'}、相關中位數 {m['put']['medianCorr'] if m['put']['medianCorr'] is not None else '—'}。")
    if m['topVolumeShare'] is not None:
        out.append(f"成交量最大的 10 個標的佔全部權證成交量 {m['topVolumeShare']}%。")
    if report.get('previous'):
        p = report['previous']
        out.append(f"上一期{label}（{p['period']['start']} 至 {p['period']['end']}）：常交易 {p['market']['liquid']} 檔、成交量 {p['market']['volume']:,} 張；本期 {m['liquid']} 檔、{m['volume']:,} 張。只作描述，不解讀為趨勢。")
    else:
        out.append(f'尚無上一期{label}可比較。')
    out.append('以上為固定規則的描述統計；權證可重疊、樣本短，不據此宣告任何策略或方向。')
    return out


def level(value, cuts, labels):
    """Name of the band a value falls in: labels has one more entry than cuts."""
    return labels[sum(value >= c for c in cuts)]


def readings(report):
    """Fixed-threshold readings of the headline numbers: what each means and how large it is, never a forecast."""
    m, kind = report['market'], report['kind']
    t = RULES['readings']
    out = []
    ur, call, put = m['underlyingReturn'], m['call'], m['put']
    if ur['count']:
        med = ur['medianPct']
        size = '接近持平' if abs(med) < t['move'][kind][0] else level(abs(med), t['move'][kind][1:], ['小幅', '明顯', '大幅']) + ('上漲' if med > 0 else '下跌')
        out.append(f"市場方向：標的期間報酬中位數 {med:+.2f}%，{size}；{ur['up']} 個標的上漲、{ur['down']} 個下跌、{ur['flat']} 個持平。這是標的本身的走勢，權證的中位數報酬主要是對它的放大反應。")
    if kind == 'monthly' and call.get('medianSlope') is not None:
        corr = call['medianCorr']
        text = (f"槓桿與連動：常交易認購的斜率中位數 {call['medianSlope']}，即標的日報酬每 1%，認購約 {call['medianSlope']:.2f}%；相關中位數 {corr}（{level(corr, t['corr'], ['低', '中', '高'])}）。")
        text += (f"認售斜率中位數 {put['medianSlope']}、相關 {put['medianCorr']}。" if put.get('medianSlope') is not None else '認售配對段數不足，未計斜率。')
        out.append(text + '斜率含時間價值衰減與條款差異，不等於理論 delta；相關低的權證通常是成交稀疏或報價停滯。')
    elif ur['count'] and abs(ur['medianPct']) >= t['move'][kind][0] and call['medianReturnPct'] is not None:
        ratio = call['medianReturnPct'] / ur['medianPct']
        if ratio > 0:
            text = f"權證反應：常交易認購期間報酬中位數 {call['medianReturnPct']:+.2f}%，約為標的中位數的 {ratio:.1f} 倍"
        else:
            text = f"權證反應：常交易認購期間報酬中位數 {call['medianReturnPct']:+.2f}%，與標的中位數方向相反；標的變動不大時常由時間價值衰減主導，樣本組成改變也會造成"
        text += f"；認售 {put['medianReturnPct']:+.2f}%。" if put['medianReturnPct'] is not None else '；認售樣本不足。'
        out.append(text + '這是兩個中位數的比值，只反映整體幅度，不是個別權證的槓桿。')
    shares = {'C': call['consistentShare'], 'P': put['consistentShare']}
    if shares['C'] is not None or shares['P'] is not None:
        parts = []
        for cp, label in (('C', '認購與標的同向'), ('P', '認售與標的反向')):
            if shares[cp] is not None:
                parts.append(f"{label} {shares[cp]:.0f}%（{level(shares[cp], t['consistent'], ['只有部分', '多數', '幾乎全部'])}）")
        text = '方向一致：' + '，'.join(parts) + '。'
        if shares['C'] is not None and shares['P'] is not None and shares['C'] - shares['P'] > 10:
            text += '認售的一致度明顯低於認購，常見原因是認售成交較稀疏、時間價值衰減在小幅波動時主導；不代表定價錯誤。'
        elif min(s for s in shares.values() if s is not None) < t['consistent'][0]:
            text += '一致度偏低時，先檢查成交稀疏與基準日跨越缺日的權證，再談定價。'
        else:
            text += '一致度高表示常交易權證的收盤價跟著標的走，沒有明顯的流動性脫鉤。'
        out.append(text)
    if m['warrants']:
        share = 100 * m['liquid'] / m['warrants']
        out.append(f"流動性：{m['warrants']:,} 檔曾掛牌中 {m['liquid']:,} 檔常交易（{share:.0f}%，{level(share, t['liquidShare'], ['低', '中', '高'])}），{m['warrants'] - m['traded']:,} 檔整個期間沒有成交。中位數與斜率只涵蓋常交易權證；冷門權證的收盤價不具代表性。")
    if m['topVolumeShare'] is not None:
        out.append(f"集中度：成交量前 10 個標的佔 {m['topVolumeShare']:.0f}%（{level(m['topVolumeShare'], t['concentration'], ['分散', '中等', '集中'])}）。集中度高時，整體中位數主要反映這幾個標的的權證。")
    inside = sum(1 for u in report['underlyings'] if u['baseDate'] and not u['baseBeforePeriod'])
    if m['missingDays']:
        text = f"資料完整度：缺 {len(m['missingDays'])} 個交易日（{'、'.join(m['missingDays'])}），期間統計只涵蓋已收錄日，期間報酬可能跨越缺日。"
    else:
        text = '資料完整度：期間內的交易日全部有資料。'
    if inside:
        text += f"{inside} 個標的沒有期間前的資料，以期間內第一個資料日為基準，報酬只涵蓋部分期間。"
    out.append(text)
    prev = report.get('previous')
    if prev:
        pm = prev['market']
        changes = []
        for key, label in (('liquid', '常交易權證'), ('volume', '成交量')):
            a, b = pm.get(key), m[key]
            if positive(a) and numeric(b):
                chg = (b / a - 1) * 100
                word = '變動不大' if abs(chg) < t['change'] else ('明顯增加' if chg > 0 else '明顯減少')
                changes.append(f"{label} {a:,.0f}→{b:,.0f}（{chg:+.0f}%，{word}）")
        pu = (prev.get('underlyingReturn') or {}).get('medianPct')
        if pu is not None and ur['medianPct'] is not None:
            changes.append(f"標的報酬中位數 {pu:+.2f}%→{ur['medianPct']:+.2f}%")
        out.append('與上一期比較：' + '，'.join(changes) + '。兩期的權證組成與期間長度不同，差異不代表趨勢。')
    watch = report.get('watch')
    if watch:
        out.append(f"值得細看的標的：{watch['eligible']} 個標的樣本足夠，其中 {len(watch['items'])} 個因數字特別進入名單（理由見名單）；" + ('沒有標的觸發任何理由，本期沒有特別需要細看的個股。' if not watch['items'] else '名單只表示數字特別，不表示機會或風險。'))
    out.append('判讀依固定門檻自動產生，只說明數字的含義與相對大小；不是預測、不評價策略，也不構成買賣建議。')
    return out


def num(value, digits=2):
    return '—' if not numeric(value) else f'{value:,.{digits}f}'


def watch_html(report, esc):
    watch = report.get('watch')
    if not watch:
        return ''
    monthly = report['kind'] == 'monthly'
    excluded = '；'.join(f'{esc(k)} {v:,}' for k, v in watch['excluded'].items())
    rows = ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in [
        f"{esc(i['code'])} {esc(i['name'])}", '<span style="white-space:normal;display:inline-block;min-width:260px;max-width:520px">' + '；'.join(esc(r) for r in i['reasons']) + '</span>',
        esc(i['market'] or '—'), num(i['underlyingReturnPct']), f"{i['liquid']}", f"{i['volume']:,}",
        f"{num(i['call']['medianReturnPct'])}／{num(i['call']['consistentShare'], 0)}%", f"{num(i['put']['medianReturnPct'])}／{num(i['put']['consistentShare'], 0)}%"]
        + ([num(i['call']['medianSlope'], 3)] if monthly else [])) + '</tr>' for i in watch['items'])
    head = ['標的', '理由', '市場', '標的報酬%', '常交易', '成交量（張）', '認購中位報酬％／同向', '認售中位報酬％／反向'] + (['認購斜率'] if monthly else [])
    return (f"<section><h2>本期值得細看的標的</h2><p>{esc(watch['basis'])}</p><p>樣本足夠的標的 {watch['eligible']:,} 個；未列入：{excluded or '無'}。</p>"
            f"<div class=\"scroll\"><table><thead><tr>{''.join('<th>' + esc(h) + '</th>' for h in head)}</tr></thead><tbody>{rows or '<tr><td colspan=' + chr(34) + str(len(head)) + chr(34) + '>本期沒有標的觸發任何理由。</td></tr>'}</tbody></table></div></section>")


def report_html(report):
    if report['kind'] == 'daily':
        return daily_html(report)
    esc = lambda value: escape(str(value), quote=True)
    m = report['market']
    kind_name = KIND_NAMES.get(report['kind'], report['kind'])
    monthly = report['kind'] == 'monthly'
    head = ['標的', '市場', '標的報酬%', '基準日', '掛牌／成交／常交易', '成交量（張）', '認購中位報酬%', '認售中位報酬%', '認購同向%', '認售反向%']
    if monthly:
        head += ['認購斜率（檔）', '認售斜率（檔）']
    rows = ''
    for u in report['underlyings'][:RULES['topUnderlyings']]:
        cells = [f"{esc(u['code'])} {esc(u['name'])}", esc(u['market'] or '—'), num(u['underlyingReturnPct']),
                 esc(u['baseDate'] or '—') + ('' if u['baseBeforePeriod'] else '（期間內）' if u['baseDate'] else ''),
                 f"{u['warrants']}／{u['traded']}／{u['liquid']}", f"{u['volume']:,}",
                 num(u['call']['medianReturnPct']), num(u['put']['medianReturnPct']),
                 num(u['call']['consistentShare'], 0), num(u['put']['consistentShare'], 0)]
        if monthly:
            cells += [f"{num(u['call']['medianSlope'], 3)}（{u['call']['slopes']}）", f"{num(u['put']['medianSlope'], 3)}（{u['put']['slopes']}）"]
        rows += '<tr>' + ''.join(f'<td>{c}</td>' for c in cells) + '</tr>'
    day_rows = ''.join(f"<tr><td>{esc(d['date'])}</td><td>{d['warrantsTraded']:,}</td><td>{d['volume']:,}</td></tr>" for d in m['byDay']) or '<tr><td colspan="3">期間內沒有已收錄的資料日。</td></tr>'
    top_rows = ''.join(f"<tr><td>{esc(w['code'])} {esc(w['name'])}</td><td>{esc(w['underlying'])} {esc(w['underlyingName'])}</td><td>{'認購' if w['cp'] == 'C' else '認售' if w['cp'] == 'P' else esc(w['cp'])}</td><td>{num(w['strike'])}</td><td>{esc(w['expiry'] or '—')}</td><td>{w['tradedDays']}／{w['listedDays']}</td><td>{w['volume']:,}</td><td>{num(w['returnPct'])}</td>"
                       + (f"<td>{num(w['slope'], 3)}</td><td>{num(w['corr'], 3)}</td><td>{w['pairs']}</td>" if monthly else '') + '</tr>' for w in report['topWarrants']) or '<tr><td colspan="11">期間內沒有常交易權證。</td></tr>'
    market_rows = ''.join(f"<tr><td>{esc(k)}</td><td>{v['underlyings']:,}</td><td>{v['warrants']:,}</td><td>{v['traded']:,}</td><td>{v['liquid']:,}</td><td>{v['volume']:,}</td></tr>" for k, v in sorted(m['byMarket'].items()))
    prev = report.get('previous')
    compare = ''
    if prev:
        pm = prev['market']
        compare = f"<section><h2>與上一期比較</h2><p>上一期：{esc(prev['id'])}（{esc(prev['period']['start'])} 至 {esc(prev['period']['end'])}）。</p><div class=\"scroll\"><table><tr><th>項目</th><th>上一期</th><th>本期</th></tr>" + ''.join(
            f"<tr><td>{label}</td><td>{a}</td><td>{b}</td></tr>" for label, a, b in [
                ('曾掛牌權證', f"{pm['warrants']:,}", f"{m['warrants']:,}"), ('常交易權證', f"{pm['liquid']:,}", f"{m['liquid']:,}"),
                ('成交量（張）', f"{pm['volume']:,}", f"{m['volume']:,}"),
                ('標的報酬中位數%', num((prev.get('underlyingReturn') or {}).get('medianPct')), num(m['underlyingReturn']['medianPct'])),
                ('常交易認購中位報酬%', num(prev['call']['medianReturnPct']), num(m['call']['medianReturnPct'])),
                ('常交易認售中位報酬%', num(prev['put']['medianReturnPct']), num(m['put']['medianReturnPct']))]
            + ([('認購斜率中位數', num(prev['call'].get('medianSlope'), 3), num(m['call'].get('medianSlope'), 3)),
                ('認售斜率中位數', num(prev['put'].get('medianSlope'), 3), num(m['put'].get('medianSlope'), 3))] if monthly else [])) + '</table></div><p>兩期的樣本與權證組成不同，差異不代表趨勢。</p></section>'
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>權證時間序列{esc(kind_name)} {esc(report['id'])}</title>
<style>:root{{color-scheme:light}}body{{margin:0;background:#f4f7f2;color:#1f332e;font:16px/1.7 Inter,"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif}}main{{max-width:1200px;margin:auto;padding:32px 24px 40px}}.eyebrow{{color:#586b62;letter-spacing:.2em;font-size:.75rem;font-weight:600;margin:0 0 6px}}h1,h2{{margin:0;font-family:"Noto Serif TC","Source Han Serif TC","Songti TC","PMingLiU",serif;font-weight:700;line-height:1.45}}h1{{font-size:2rem;letter-spacing:.06em}}h2{{font-size:1.2rem;letter-spacing:.03em;margin-bottom:10px}}h3{{font-size:1rem;margin:18px 0 4px}}.meta{{color:#4a5f57;font-size:.92rem}}p{{margin:.4em 0}}p:empty{{display:none}}ul{{margin:.4em 0;padding-left:1.4em}}li{{margin:.25em 0}}section{{background:#fff;border:1px solid #dfe7e0;border-radius:14px;padding:22px 24px;margin:22px 0}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;white-space:nowrap;font-size:.9rem;font-variant-numeric:tabular-nums}}th,td{{text-align:left;padding:10px;border-bottom:1px solid #dfe7e0}}th{{color:#4a5f57;font-size:.8rem;font-weight:500;border-bottom-color:#c9d6cd}}small{{display:block;margin-top:10px;color:#586b62;font-size:.8rem;overflow-wrap:anywhere}}@media (max-width:600px){{main{{padding:24px 14px 32px}}section{{padding:18px 16px}}h1{{font-size:1.7rem}}}}@media print{{body{{background:#fff;color:#000}}main{{padding:0}}section{{border-color:#bbb;break-inside:avoid}}.scroll{{overflow:visible}}table{{white-space:normal;font-size:10px}}}}</style>
<main><p class="eyebrow">衍生視界 · 權證與標的收盤序列</p><h1>權證時間序列{esc(kind_name)}</h1><p class="meta">期間 {esc(report['period']['start'])} 至 {esc(report['period']['end'])} · 版本 {esc(report['id'])} · 資料至 {esc(m['dataDays'][-1] if m['dataDays'] else '尚無資料')}</p>
{quality_html(report)}
<section><h2>本期觀察</h2><ul>{''.join('<li>' + esc(t) + '</li>' for t in report['observations'])}</ul></section>
<section><h2>本期判讀</h2><p>依固定門檻自動產生：說明每個數字的含義與相對大小，不是預測或建議。門檻見「方法與限制」。</p><ul>{''.join('<li>' + esc(t) + '</li>' for t in report.get('readings', []))}</ul></section>
{watch_html(report, esc)}
<section><h2>資料日與市場</h2><div class="scroll"><table><tr><th>資料日</th><th>有成交權證</th><th>成交量（張）</th></tr>{day_rows}</table></div><h3>依市場</h3><div class="scroll"><table><tr><th>市場</th><th>標的</th><th>曾掛牌</th><th>曾成交</th><th>常交易</th><th>成交量（張）</th></tr>{market_rows}</table></div></section>
<section><h2>標的比較（成交量前 {RULES['topUnderlyings']} 名）</h2><p>標的報酬以期間最後收盤對基準日收盤；基準日為期間前最後一個資料日，標示「期間內」表示歷史不足而改用期間內第一個資料日。權證期間報酬只用有成交的日子；中位數只含常交易權證。完整 {len(report['underlyings'])} 個標的見 JSON。</p><div class="scroll"><table><thead><tr>{''.join('<th>' + esc(h) + '</th>' for h in head)}</tr></thead><tbody>{rows or '<tr><td colspan="12">期間內沒有標的資料。</td></tr>'}</tbody></table></div></section>
<section><h2>成交量最大的常交易權證</h2><div class="scroll"><table><tr><th>權證</th><th>標的</th><th>權別</th><th>履約價</th><th>到期日</th><th>成交日／掛牌日</th><th>成交量（張）</th><th>期間報酬%</th>{'<th>斜率</th><th>相關</th><th>配對段數</th>' if monthly else ''}</tr>{top_rows}</table></div></section>
{compare}<section><h2>方法與限制</h2><p>常交易：期間內成交日佔掛牌日 {int(RULES['liquidShare'] * 100)}% 以上且至少 {RULES['liquidMinDays']} 天。方向一致：認購與標的同向、認售與標的反向，持平或缺基準不計。{'斜率：權證日報酬對標的日報酬的最小平方法係數，以權證連續兩個有成交的資料日為一段，標的取同一段的報酬；配對段數至少 ' + str(RULES['minPairs']) + ' 段才計算。' if monthly else ''}歷史檔保留上限 {esc(report.get('historyLimit') or '—')} 個資料日。</p><p>判讀門檻：標的中位數報酬的小幅／明顯／大幅以 {'、'.join(str(x) for x in RULES['readings']['move'][report['kind']])}% 為界；相關低／中／高以 {'、'.join(str(x) for x in RULES['readings']['corr'])} 為界；方向一致「多數」與「幾乎全部」以 {'、'.join(str(x) for x in RULES['readings']['consistent'])}% 為界；常交易佔比低／中／高以 {'、'.join(str(x) for x in RULES['readings']['liquidShare'])}% 為界；前 10 名成交量佔比分散／中等／集中以 {'、'.join(str(x) for x in RULES['readings']['concentration'])}% 為界；與上一期相差達 {RULES['readings']['change']}% 才稱明顯增減。</p><ul>{''.join('<li>' + esc(t) + '</li>' for t in report['limitations'])}</ul><p>同一期間內容改變另存新版，舊版本保留。生成時間 {esc(report['generatedAt'])}；規則 {esc(RULES['version'])}。</p><small>報告內容 SHA-256：{esc(report['contentSha256'])}</small></section></main></html>'''


def archive(output, content, now):
    """Save a versioned JSON/HTML pair and update the index; a rerun with identical content changes nothing."""
    content_sha = digest(content)
    folder = output / 'warrant-series-reports'
    path = output / 'warrant-series-reports.json'
    index = read_json(path) or {'schemaVersion': 1, 'items': [], 'latest': {}}
    index.setdefault('latest', {})
    period = content['period']['end'][:7] if content['kind'] == 'monthly' else content['period']['end']
    prefix = f"{content['kind']}-{period}-r"
    same_period = [i for i in index['items'] if i['id'].startswith(prefix)]
    existing = next((i for i in same_period if i['contentSha256'] == content_sha), None)
    if existing:
        saved = read_json(folder / f"{existing['id']}.json")
        if not saved or digest({k: v for k, v in saved.items() if k not in ('id', 'generatedAt', 'contentSha256')}) != content_sha:
            raise ValueError('Saved report integrity failure')
        save_text(folder / f"{existing['id']}.html", report_html(saved))
        report = saved
    else:
        report_id = f'{prefix}{len(same_period) + 1}-{content_sha[:8]}'
        report = {**content, 'id': report_id, 'generatedAt': now, 'contentSha256': content_sha}
        destination = folder / f'{report_id}.json'
        if destination.exists():
            raise ValueError('Refusing to overwrite an existing report version')
        save_json(destination, report)
        save_text(destination.with_suffix('.html'), report_html(report))
        index['items'].append({'id': report_id, 'kind': content['kind'], 'periodStart': content['period']['start'],
                               'periodEnd': content['period']['end'], 'dataThrough': content['market']['dataDays'][-1] if content['market']['dataDays'] else None,
                               'dataDays': len(content['market']['dataDays']), 'generatedAt': now, 'contentSha256': content_sha})
    latest_item = next((i for i in index['items'] if i['id'] == index['latest'].get(content['kind'])), None)
    if not latest_item or latest_item['periodEnd'] <= content['period']['end']:
        index['latest'][content['kind']] = report['id']
    # Only enrich the index: immutable report bytes and hashes stay unchanged.
    latest_period, prior_rules = {}, {}
    for item in index['items']:
        saved = read_json(folder / (item['id'] + '.json'))
        item['status'] = saved.get('status', 'unknown')
        item['rulesVersion'] = saved.get('rules', {}).get('version', 'unknown')
        key = (item['kind'], item['periodEnd'])
        item['revisionReason'] = ('首次報告' if key not in prior_rules else
                                  '規則升版' if prior_rules[key] != item['rulesVersion'] else '資料補齊或修訂')
        prior_rules[key] = item['rulesVersion']
        latest_period[key] = item
    active_rules = {kind: next(i['rulesVersion'] for i in index['items'] if i['id'] == rid)
                    for kind, rid in index['latest'].items()}
    for item in index['items']:
        item['archiveState'] = ('archived-rules' if item['rulesVersion'] != active_rules[item['kind']] else
                                'current' if latest_period[(item['kind'], item['periodEnd'])]['id'] == item['id'] else 'superseded')
    index.setdefault('errors', {}).pop(content['kind'], None)
    index.update(status='retained' if index['errors'] else 'ok', error='; '.join(index['errors'].values()) or None)
    save_json(path, index)
    return report


def run(output, today, kind, now):
    periods = {'weekly': weekly_period, 'monthly': monthly_period}
    results, errors = [], []
    for name in kinds_for(today, kind):
        try:
            if name == 'daily':
                content = daily_report(output)
            else:
                start, end = periods[name](today)
                content = build_report(output, name, start, end)
            if not content['market']['dataDays']:
                raise ValueError('本期沒有已收錄的交易日，保留上期報告')
            results.append(archive(output, content, now))
        except Exception as exc:  # keep older reports readable and record the failure
            path = output / 'warrant-series-reports.json'
            index = read_json(path) or {'schemaVersion': 1, 'items': [], 'latest': {}}
            index.setdefault('errors', {})[name] = str(exc)[:300]
            index.update(status='retained', error=f'{name}: {exc}')
            save_json(path, index)
            errors.append(f'{name}: {exc}')
    if errors:
        path = output / 'warrant-series-reports.json'
        index = read_json(path) or {'schemaVersion': 1, 'items': [], 'latest': {}}
        index.update(status='retained', error='; '.join(index.get('errors', {}).values()))
        save_json(path, index)
    return results, errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('data/derivatives'))
    parser.add_argument('--kind', choices=['auto', 'daily', 'weekly', 'monthly', 'both'], default='auto')
    parser.add_argument('--today', help='Taipei date (YYYY-MM-DD) to run as if on that day')
    parser.add_argument('--now', help='generation timestamp (ISO 8601 UTC) for reproducible runs')
    args = parser.parse_args(argv)
    taipei = timezone(timedelta(hours=8))
    today = date.fromisoformat(args.today) if args.today else datetime.now(taipei).date()
    now = args.now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    results, errors = run(args.data, today, args.kind, now)
    for report in results:
        print(f"{report['id']}: {report['period']['start']}..{report['period']['end']}, {len(report['market']['dataDays'])} data days")
    if errors:
        raise SystemExit('; '.join(errors))


if __name__ == '__main__':
    main()
