"""Free, deterministic futures/options daily briefs and weekly/monthly reviews.

Consumes saved official data only. Reports are immutable content-addressed revisions;
historical reconstructions never claim to be prospective trading performance.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from html import escape
import json
import math
from pathlib import Path
from statistics import median

if __package__:
    from .publication import save_text
    from .trading_calendar import load_calendar
else:
    from publication import save_text
    from trading_calendar import load_calendar

VERSION = 'futures-options-reports-v1'
ROOT = 'derivative-reports'
KINDS = {'daily': '每日簡報', 'weekly': '每週完整分析', 'monthly': '每月策略檢討'}
RULES = {'version': VERSION, 'volumeMultiple': 2, 'minVolume': 100,
         'priceThresholdPct': 5, 'holdingSessions': 5, 'reviewDays': 90,
         'minEvaluated': 30, 'minSignalDates': 20, 'autoTune': False}
FEEDS = {
    'futures': '台灣期貨一般時段行情', 'futures-institutional': '大台三大法人',
    'options-institutional': '臺指選擇權三大法人', 'futures-large': '期貨大額交易人',
    'options-large': '選擇權大額交易人', 'pcr': '臺指 P/C',
    'us-options': 'Cboe 選擇權統計', 'us-futures': 'CFE 期貨結算',
    'us-cot-financial': 'CFTC 金融期貨週部位',
}
RULE_LABELS = {'volume': '放量', 'momentum': '價漲放量增倉', 'pullback': '價跌放量增倉'}
LIMITATIONS = [
    '日報追蹤變化、週報整理五日趨勢、月報檢討固定規則；不依短期績效自動改規則，也不下單。',
    '法人淨部位可能包含避險；買權多不代表看多。大額交易人多空排行可能不是同一群人，且台指換算口徑與大台法人不同。',
    'P/C 與履約價未平倉須考慮到期、轉倉；集中履約價不是已確認的支撐或壓力。',
    '美國 CFTC 是週二部位、通常週五公布；本報告按部位日期描述，不代表該日已公開。美國日資料不套用台灣休市日曆。',
    '歷史事件用訊號次日成交收盤為基準，再觀察五個交易日；缺價、尚未到日及到期未結算不補零、不順延找價。',
    '回測為目前保存資料的歷史重建。多履約價與重疊訊號不是獨立樣本；未計到期結算、完整費稅、滑價與可成交量，不是帳戶績效。',
    '成本 1／3 個百分點只做報酬敏感度，不能替代真實交易成本。資料不足時保留「尚無法判定」，不推薦最佳策略。',
]


def numeric(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def delta(a, b):
    return round(a-b, 6) if numeric(a) and numeric(b) else None


def percent(a, b):
    return round((a/b-1)*100, 6) if numeric(a) and numeric(b) and b > 0 else None


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return sha256(encoded(value).encode()).hexdigest()


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else default


def save(path, value):
    save_text(path, encoded(value))


def prior_open(calendar, day):
    cursor = date.fromisoformat(day)-timedelta(days=1)
    while not calendar.is_open(cursor):
        cursor -= timedelta(days=1)
    return cursor.isoformat()


def open_days(calendar, start, end):
    days, cursor = [], date.fromisoformat(start)
    while cursor.isoformat() <= end:
        if calendar.is_open(cursor):
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def project(name, rows):
    if name == 'futures':
        return [r for r in rows if r.get('session') == '一般' and r.get('contract') in
                ('TX', 'TXF', 'MTX', 'MXF', 'TMF', 'TE', 'TEF', 'TF', 'TFF')]
    if name.endswith('institutional'):
        return [r for r in rows if r.get('contract') == ('臺股期貨' if name.startswith('futures') else '臺指選擇權')]
    if name.endswith('large'):
        return [r for r in rows if r.get('contract') == ('TX' if name.startswith('futures') else 'TXO')
                and str(r.get('month')) == '999912' and str(r.get('traderType')) == '0']
    if name == 'us-options':
        return [r for r in rows if r.get('contract') in ('SUM OF ALL PRODUCTS', 'INDEX OPTIONS', 'EQUITY OPTIONS')]
    if name == 'us-cot-financial':
        return [r for r in rows if r.get('shortName') in ('E-MINI S&P 500', 'NASDAQ MINI', 'NASDAQ-100 Consolidated')]
    if name == 'us-futures':
        return [r for r in rows if r.get('contract') == 'VX']
    return rows


def import_position_archives(output, obs):
    """Reuse the site's validated daily reports, without querying an upstream again."""
    for path in sorted((output/'daily-reports/positioning').glob('????-??-??.json')):
        report = read(path, {})
        day = report.get('date')
        if day != path.stem:
            raise ValueError('Archived positioning date mismatch: '+path.name)
        sources = {s['id']: s for s in report.get('sources', []) if s.get('date') == day}
        for prefix, contract in [('futures','TX'),('options','TXO')]:
            name = prefix+'-large'
            if name not in sources:
                continue
            rows = [{'date':day, 'contract':contract, 'month':'999912', 'traderType':'0',
                     **{k:v for k,v in row.items() if k != 'product'}}
                    for row in report.get('large', []) if row.get('product') == prefix]
            if rows:
                obs['feeds'].setdefault(name, {})[day] = {'rows':rows, 'url':sources[name].get('url'),
                    'sha256':digest(rows), 'basis':'已核對同日來源的封存籌碼日報'}


def capture(output):
    """Preserve compact histories, including quote feeds whose collector has no history."""
    path = output / ROOT / 'observations.json'
    obs = read(path, {'feeds': {}})
    import_position_archives(output, obs)
    manifest = read(output/'manifest.json', {})
    sources = {s['id']: s for s in manifest.get('sources', [])}
    issues = {}
    for name in FEEDS:
        source = sources.get(name, {})
        try:
            feed = read(output/(name+'.json'), {})
        except (OSError, ValueError):
            issues[name] = '來源檔案無法讀取；保留以前保存的資料'
            continue
        if source.get('status') != 'ok' or not feed.get('date') or source.get('date') != feed['date']:
            issues[name] = '來源未通過核對；保留以前保存的資料'
            continue
        history = {h['date']: h['rows'] for h in feed.get('history', [])}
        history[feed['date']] = feed.get('rows', [])
        for day, rows in history.items():
            date.fromisoformat(day)
            if any(r.get('date') != day for r in rows):
                raise ValueError(name+': row date differs from feed')
            selected = project(name, rows)
            if not selected:
                continue
            obs['feeds'].setdefault(name, {})[day] = {
                'rows': selected, 'url': source.get('url'), 'sha256': digest(selected),
                'basis': '已保存官方逐日資料；非當日即時訊號',
            }
    # Validated full-market shards supersede the older top-contract reconstruction.
    history_index = read(output/'market-history.json', {})
    if history_index:
        if __package__:
            from .market_history import load_day
        else:
            from market_history import load_day
        for name in FEEDS:
            for day, entry in history_index.get('feeds', {}).get(name, {}).items():
                current = obs['feeds'].get(name, {}).get(day, {})
                if current.get('archiveSha256') == entry['sha256']:
                    continue
                document = load_day(output, entry)
                if document['date'] != day or document['feed'] != name or any(r['date'] != day for r in document['rows']):
                    raise ValueError('Historical archive identity mismatch')
                selected = project(name, document['rows'])
                if selected:
                    obs['feeds'].setdefault(name, {})[day] = {'rows':selected, 'sha256':digest(selected),
                        'url':entry.get('evidence',{}).get('url'), 'archiveSha256':entry['sha256'],
                        'basis':'已驗證完整歷史報表的欄位投影；事後重建'}
    save(path, obs)
    return obs, issues


def select_feed(obs, name, end):
    days = obs['feeds'].get(name, {})
    candidates = sorted(d for d in days if d <= end)
    if not candidates:
        return None, []
    day = candidates[-1]
    return day, days[day]['rows']


def compared_rows(current, old, keys, values):
    lookup = {tuple(r.get(k) for k in keys): r for r in old}
    return [{**r, 'changes': {k: delta(r.get(k), lookup.get(tuple(r.get(i) for i in keys), {}).get(k))
                             for k in values}} for r in current]


def stats(events, cutoff):
    counts, finished = Counter(), []
    for event in events:
        # Historical files may already contain next month's outcome. Do not read it early.
        if not event.get('entryDate') or event['entryDate'] > cutoff:
            status = 'pending'
        elif event.get('status') == 'missing-entry':
            status = 'missing-entry'
        elif not event.get('exitDate') or event['exitDate'] > cutoff:
            status = 'pending'
        else:
            status = event['status']
        counts[status] += 1
        if status == 'evaluated' and numeric(event.get('returnPct')):
            finished.append(event)
    values = [e['returnPct'] for e in finished]
    n = len(values)
    return {'signals': len(events), 'counts': dict(counts), 'evaluated': n,
            'distinctSignalDates': len({e['date'] for e in finished}),
            'mean': sum(values)/n if n else None, 'median': median(values) if n else None,
            'positivePct': 100*sum(v > 0 for v in values)/n if n else None,
            'meanCost1': sum(v-1 for v in values)/n if n else None,
            'meanCost3': sum(v-3 for v in values)/n if n else None,
            'coveragePct': 100*n/len(events) if events else None}


def event_review(events, start, end):
    rows = []
    for rule in RULE_LABELS:
        for cp in ('C', 'P'):
            group = [e for e in events if e['rule'] == rule and e['cp'] == cp and start <= e['date'] <= end]
            result = stats(group, end)
            enough = result['evaluated'] >= RULES['minEvaluated'] and result['distinctSignalDates'] >= RULES['minSignalDates']
            rows.append({'rule': rule, 'label': RULE_LABELS[rule], 'cp': cp, **result,
                         'assessment': '可繼續檢驗，尚未證實有效' if enough else '樣本不足，尚無法判定'})
    return rows


def option_evidence(output, start, end, kind):
    index = read(output/'option-history.json', {})
    backtest = read(output/'option-backtest/TXO.json', {})
    events = backtest.get('events', [])
    days = [d for d in index.get('dates', []) if d <= end]
    through = days[-1] if days else None
    recent = [e for e in events if e['date'] == through]
    # These are source-file signals, all reported with fixed rules and without outcome ranking.
    watch = sorted(recent, key=lambda e: (-(e.get('volumeMultiple') or 0), e['month'], e['strike'], e['cp'], e['rule']))[:20]
    watch = [{k: e.get(k) for k in ('date', 'month', 'strike', 'cp', 'rule', 'volumeMultiple', 'pricePct', 'oiChange')} for e in watch]
    follow = [e for e in events if e['date'] < start and e.get('exitDate') and start <= e['exitDate'] <= end]
    review_start = (date.fromisoformat(end)-timedelta(days=RULES['reviewDays']-1)).isoformat()
    # Evidence covers only the fields used as of the cutoff, never later outcomes.
    evidence = [{k: e.get(k) for k in ('date','month','strike','cp','rule','pricePct','volumeMultiple','oiChange')}
                | {'asOf': stats([e], end)} for e in events if e['date'] <= end]
    return {'contract': 'TXO', 'version': backtest.get('version'),
            'basis': '固定規則歷史重建；本報告不使用日後價格，不宣稱前瞻樣本外績效',
            'start': days[0] if days else None, 'through': through, 'sessions': len(days),
            'sourceStatus': 'unavailable' if not days or not backtest else 'partial' if any(start <= d <= end for d in index.get('missingDates', [])) else 'available', 'missingDates': [d for d in index.get('missingDates', []) if start <= d <= end],
            'period': event_review(events, start, end), 'followup': stats(follow, end),
            'reviewStart': review_start, 'review': event_review(events, review_start, end) if kind == 'monthly' else [],
            'watch': watch, 'watchTotal': len(recent), 'sha256': digest(evidence),
            'notes': '每種規則的事件可重疊，不相加當成交易筆數。門檻只是研究檢查，不是統計顯著或上線批准。'}


def strike_summary(output, end, base):
    current, previous = {}, {}
    folder = output/'option-history/TXO'
    available = sorted({d for p in folder.glob('*.json') for d in read(p, {}).get('days', {}) if d <= end})
    through = available[-1] if available else None
    for path in folder.glob('*.json'):
        group = read(path)
        for day, target in ((through, current), (base, previous)):
            for row in group.get('days', {}).get(day, []):
                strike, cp, _, _, oi = row[:5]
                if numeric(oi):
                    target[group['month'], strike, cp] = oi
    expiry_totals = Counter()
    for (month, _, _), oi in current.items():
        expiry_totals[month] += oi
    rows = []
    for month, _ in expiry_totals.most_common(3):
        for cp in ('C', 'P'):
            side = sorted(((k, v) for k, v in current.items() if k[0] == month and k[2] == cp and v > 0), key=lambda x: (-x[1], x[0][1]))[:3]
            rows.extend({'month': month, 'cp': cp, 'strike': key[1], 'oi': oi,
                         'change': delta(oi, previous.get(key))} for key, oi in side)
    return {'date': through, 'baseDate': base, 'rows': rows,
            'scope': '一般時段，未平倉最多的三個到期別，各買賣權前三大履約價；只比相同契約'}


def stock_option_coverage(output, end):
    index = read(output/'stock-option-watch.json', {})
    days = sorted(d for d in index.get('dates', []) if d <= end)
    if not days:
        return {'date': None, 'stocks': 0, 'pairs': 0, 'priced': 0, 'quoted': 0, 'active': 0}
    data = read(output/'stock-option-watch'/(days[-1]+'.json'), {})
    result = {'date': days[-1], 'stocks': len(data.get('stocks', {})), 'pairs': 0, 'priced': 0, 'quoted': 0, 'active': 0}
    for stock in data.get('stocks', {}).values():
        for row in stock.get('pairs', []):
            c, p = row.get('C') or {}, row.get('P') or {}
            result['pairs'] += 1
            result['active'] += bool((c.get('volume') or 0)+(p.get('volume') or 0) > 0)
            result['priced'] += all(numeric(v) for v in (c.get('last'), p.get('last'), stock.get('spot')))
            result['quoted'] += (all(numeric(v) for v in (c.get('bid'), p.get('ask'), stock.get('ask')))
                                 or all(numeric(v) for v in (c.get('ask'), p.get('bid'), stock.get('bid'))))
    return result


def build(output, obs, issues, kind, start, end):
    calendar = load_calendar(output)
    expected = open_days(calendar, start, end)
    base = prior_open(calendar, start)
    panels, sources = {}, []
    for name, label in FEEDS.items():
        through, rows = select_feed(obs, name, end)
        # Taiwan uses exact baseline session; US periods use the last available observation before start.
        if name.startswith('us-'):
            boundary = (date.fromisoformat(start)-timedelta(days=1)).isoformat()
            base_day, old = select_feed(obs, name, boundary)
        else:
            base_day = base
            old = obs['feeds'].get(name, {}).get(base, {}).get('rows', [])
        if name.endswith('institutional'):
            rows = compared_rows(rows, old, ['contract', 'investor', 'cp'], ['netOi'])
        elif name == 'pcr':
            rows = compared_rows(rows, old, [], ['putVolume', 'callVolume', 'putOi', 'callOi', 'volumeRatio', 'oiRatio'])
        elif name == 'futures':
            rows = compared_rows(rows, old, ['contract', 'month', 'session'], ['last', 'volume', 'oi'])
            nearest = {}
            for row in sorted(rows, key=lambda r: str(r['month'])):
                if (row.get('volume') or 0) > 0 and str(row['month']) >= end[:7].replace('-', ''):
                    nearest.setdefault(row['contract'], row)
            rows = list(nearest.values())
        elif name == 'us-options':
            rows = compared_rows(rows, old, ['contract'], ['volumeRatio', 'oiRatio', 'totalVolume'])
        elif name == 'us-cot-financial':
            prior = {r['contract']: r for r in old}
            rows = [{**r, 'categories': [{**c, 'netChange': delta(c.get('net'), next(
                (v.get('net') for v in prior.get(r['contract'], {}).get('categories', []) if v['category'] == c['category']), None))}
                for c in r.get('categories', [])]} for r in rows]
        elif name == 'us-futures':
            rows = compared_rows(rows, old, ['symbol', 'expiry'], ['price'])
            rows = sorted((r for r in rows if r.get('expiry', '') > (through or end)), key=lambda r: (r['expiry'], r['symbol']))[:3]
        elif name.endswith('large'):
            def shares(records):
                return [{**r, 'longShare': round(r['top5Buy']/r['oi']*100, 4) if numeric(r.get('oi')) and r['oi'] > 0 and numeric(r.get('top5Buy')) else None,
                         'shortShare': round(r['top5Sell']/r['oi']*100, 4) if numeric(r.get('oi')) and r['oi'] > 0 and numeric(r.get('top5Sell')) else None} for r in records]
            rows = compared_rows(shares(rows), shares(old), ['contract','cp'], ['top5Buy','top5Sell','longShare','shortShare'])
        is_us = name.startswith('us-')
        missing = [] if is_us else [d for d in expected if d not in obs['feeds'].get(name, {})]
        us_old = is_us and through and (date.fromisoformat(end)-date.fromisoformat(through)).days > (14 if name == 'us-cot-financial' else 4)
        source_row = obs['feeds'].get(name, {}).get(through, {})
        panels[name] = {'date': through, 'baseDate': base_day if old else None, 'rows': rows,
                        'baselineMissing': not bool(old)}
        sources.append({'id': name, 'label': label, 'date': through, 'baseDate': base_day if old else None,
                        'days': len([d for d in obs['feeds'].get(name, {}) if start <= d <= end]),
                        'expected': None if is_us else len(expected), 'missingDates': missing,
                        'status': 'unavailable' if not through else 'retained' if name in issues else 'partial' if missing or us_old else 'available',
                        'note': issues.get(name) or ('美國資料日期已超過檢查門檻；等待更新' if us_old else '美國原頻率；未使用台灣日曆判斷缺日' if is_us else ''),
                        'url': source_row.get('url'), 'sha256': source_row.get('sha256')})
    if not any(s['date'] and start <= s['date'] <= end for s in sources):
        raise ValueError('本期沒有已核對市場資料；保留上一期報告')
    timeline = []
    for day in expected:
        f = obs['feeds'].get('futures-institutional', {}).get(day, {}).get('rows', [])
        p = obs['feeds'].get('pcr', {}).get(day, {}).get('rows', [])
        investors = {r['investor']: r.get('netOi') for r in f}
        ratio = p[0] if p else {}
        timeline.append({'date': day, 'foreignNetOi': investors.get('外資及陸資'), 'trustNetOi': investors.get('投信'),
                         'volume': ratio.get('callVolume', 0)+ratio.get('putVolume', 0) if p else None,
                         'volumeRatio': ratio.get('volumeRatio'), 'oiRatio': ratio.get('oiRatio')})
    report = {'schemaVersion': 1, 'renderVersion': 1, 'kind': kind, 'title': '期貨與選擇權'+KINDS[kind],
              'periodStart': start, 'periodEnd': end, 'baseDate': base, 'rules': RULES,
              'dataThrough': max(s['date'] for s in sources if s['date']), 'sources': sources, 'panels': panels,
              'timeline': timeline, 'strikes': strike_summary(output, end, base),
              'stockOptions': stock_option_coverage(output, end), 'events': option_evidence(output, start, end, kind),
              'limitations': LIMITATIONS, 'status': 'partial' if any(s['status'] != 'available' for s in sources) else 'available'}
    for name, label, summary, dates in [
        ('option-history','臺指選擇權歷史與訊號', report['events'], read(output/'option-history.json', {}).get('dates', [])),
        ('stock-option-watch','個股選擇權與現股配對', report['stockOptions'], read(output/'stock-option-watch.json', {}).get('dates', [])),
    ]:
        through = summary.get('through') or summary.get('date')
        missing = [d for d in expected if d not in dates]
        sources.append({'id':name, 'label':label, 'date':through, 'baseDate':base if base in dates else None,
                        'days':len([d for d in dates if start <= d <= end]), 'expected':len(expected),
                        'missingDates':missing, 'status':'unavailable' if not through or summary.get('sourceStatus') == 'unavailable' else 'partial' if missing else 'available',
                        'note':'配對缺報價另列；無報價不補零', 'sha256':digest(summary), 'url':'https://www.taifex.com.tw/cht/3/optDailyMarketReport'})
    report['status'] = 'partial' if any(s['status'] != 'available' for s in sources) else 'available'
    history = read(output/'market-history.json', {})
    for name in ('futures-fcm','options-fcm'):
        if not history: continue
        entries = history.get('feeds', {}).get(name, {})
        dates = [d for d in expected if d in entries]
        missing = [d for d in expected if d not in entries]
        sources.append({'id':name,'label':'期貨商成交量全表－'+('期貨' if name.startswith('futures') else '選擇權'),
                        'date':max(dates) if dates else None,'baseDate':base if base in entries else None,
                        'days':len(dates),'expected':len(expected),'missingDates':missing,
                        'status':'partial' if missing else 'available',
                        'note':'交易熱度，不代表方向；免費較早全表未保存的日期不補值',
                        'url':'https://www.taifex.com.tw/cht/7/dailyFCM',
                        'sha256':digest({d:entries[d]['sha256'] for d in dates})})
    report['status'] = 'partial' if any(s['status'] != 'available' for s in sources) else 'available'
    report['findings'] = findings(report)
    return report


def number(value, signed=False):
    if not numeric(value):
        return '缺資料'
    return ('+' if signed and value > 0 else '')+f'{value:,.2f}'.rstrip('0').rstrip('.')


def findings(report):
    panels, result = report['panels'], []
    f = panels['futures-institutional']
    for row in f['rows']:
        if row['investor'] == '外資及陸資':
            result.append({'topic': '外資期貨部位', 'fact': f"{f['date']} 大台淨部位 {number(row['netOi'], True)} 口；較 {f['baseDate'] or '缺少基準日'} 變化 {number(row['changes']['netOi'], True)} 口。",
                           'reading': '追蹤淨空是否擴大或縮小；可能包含現股避險，不能直接判定明日漲跌。'})
    p = panels['pcr']
    if p['rows']:
        row = p['rows'][0]
        total = row['callVolume']+row['putVolume']
        changes = row['changes']
        previous = total-changes['callVolume']-changes['putVolume'] if all(numeric(changes.get(k)) for k in ['callVolume','putVolume']) else None
        result.append({'topic': '選擇權交易活動', 'fact': f"{p['date']} 共 {number(total)} 口，較基準日變動 {number(percent(total, previous), True)}%；成交 P/C {number(row['volumeRatio'])}%，未平倉 P/C {number(row['oiRatio'])}%。",
                       'reading': '量增表示交易升溫，無法只由成交量分辨買進、賣出或平倉；P/C 變化也受到期影響。'})
    s = report['stockOptions']
    result.append({'topic': '個股選擇權價差資料', 'fact': f"{s['date'] or '無資料日'} 收錄 {s['stocks']} 檔標的、{s['pairs']} 組配對；{s['priced']} 組可算收盤價差，{s['quoted']} 組有單向三邊盤末報價。",
                   'reading': '收盤價差是待查線索；缺同步報價、可成交量與完整成本，不能確認可執行套利。'})
    sample = report['events']['followup']
    result.append({'topic': '先前訊號後續觀察', 'fact': f"本期到觀察終點的先前訊號 {sample['signals']} 筆，可評估 {sample['evaluated']} 筆；報酬中位數 {number(sample['median'], True)}%。",
                   'reading': '不同規則可能重疊，不相加當作實際交易；缺價不算零報酬。每月才檢討規則，樣本不足維持觀察。'})
    u = panels['us-options']
    rows = {r['contract']: r for r in u['rows']}
    if all(k in rows for k in ['INDEX OPTIONS','EQUITY OPTIONS']):
        result.append({'topic': '美國選擇權', 'fact': f"{u['date']} Cboe 指數成交 P/C {number(rows['INDEX OPTIONS']['volumeRatio'])} 倍、個股 {number(rows['EQUITY OPTIONS']['volumeRatio'])} 倍。",
                       'reading': '不同類別的買賣權交易結構不同；只代表此來源涵蓋範圍，不直接推論整體美股方向。'})
    return result


def html_document(report):
    """Portable report with full evidence; also useful without application JavaScript."""
    def table(headers, rows):
        return '<div class="table"><table><thead><tr>'+''.join('<th>'+escape(str(x))+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape('—' if x is None else str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'
    parts = [f'<h1>{escape(report["title"])}</h1><p>{report["periodStart"]}～{report["periodEnd"]} · {escape(report["status"])} · 保存 {escape(report["generatedAt"])}</p>',
             '<p>資料日期各自列示；本報告為盤後研究，無自動交易或策略改版。</p>']
    for f in report['findings']:
        parts.append('<h2>'+escape(f['topic'])+'</h2><p>'+escape(f['fact'])+'</p><p>'+escape(f['reading'])+'</p>')
    parts.append('<h2>逐日變化</h2>'+table(['日期','外資大台淨部位','投信大台淨部位','選擇權成交','成交 P/C%','未平倉 P/C%'],
        [[r['date']]+[number(r[k]) for k in ('foreignNetOi','trustNetOi','volume','volumeRatio','oiRatio')] for r in report['timeline']]))
    for name, label in [('futures-institutional','大台法人部位'),('options-institutional','臺指選擇權法人部位')]:
        p = report['panels'][name]
        parts.append('<h2>'+label+'</h2><p>資料日 '+escape(p['date'] or '尚無資料')+'；基準 '+escape(p['baseDate'] or '缺資料')+'</p>'+table(
            ['法人','買／賣權','多方','空方','淨部位','較基準變化'],
            [[r['investor'],r['cp'] or '—']+[number(r.get(k)) for k in ['longOi','shortOi','netOi']]+[number(r['changes']['netOi'])] for r in p['rows']]))
    parts.append('<h2>期貨行情</h2>'+table(['契約／月份','最後成交','結算價','來源日漲跌%','成交量','未平倉'],
        [[r['contract']+'/'+r['month']]+[number(r.get(k)) for k in ['last','price','changePct','volume','oi']] for r in report['panels']['futures']['rows']]))
    parts.append('<h2>大額交易人集中度</h2>'+table(['商品／權別','資料日','前五大多方','前五大空方','多方占比%','空方占比%','多方占比變化（點）','空方占比變化（點）'],
        [[r['name']+' '+str(r['cp'] or ''),report['panels'][name]['date']]+[number(r[k]) for k in ['top5Buy','top5Sell','longShare','shortShare']]+[number(r['changes'][k]) for k in ['longShare','shortShare']]
         for name in ['futures-large','options-large'] for r in report['panels'][name]['rows']]))
    parts.append('<h2>履約價集中</h2>'+table(['到期別','權別','履約價','未平倉','同契約變化'],
        [[r['month'],r['cp']]+[number(r[k]) for k in ['strike','oi','change']] for r in report['strikes']['rows']]))
    parts.append('<h2>美國市場</h2>'+table(['Cboe 類別','成交量','成交 P/C 倍','未平倉 P/C 倍'],
        [[r['contract']]+[number(r[k]) for k in ['totalVolume','volumeRatio','oiRatio']] for r in report['panels']['us-options']['rows']]))
    parts.append(table(['CFTC 期貨','參與者','淨部位','較基準變化'],
        [[r['shortName'],c['category'],number(c['net']),number(c['netChange'])] for r in report['panels']['us-cot-financial']['rows'] for c in r['categories'] if c['category'] in ['assetManager','leveraged']]))
    parts.append(table(['CFE 契約','到期日','結算價','同契約變化'],
        [[r['symbol'],r['expiry'],number(r['price']),number(r['changes']['price'])] for r in report['panels']['us-futures']['rows']]))
    for key, label in [('period','固定訊號檢查'),('review','近 90 日策略檢討')]:
        if report['events'][key]:
            parts.append('<h2>'+label+'</h2>'+table(['規則','權別','可評估／觸發','有效日期','中位數%','正報酬%','平均%','平均減1點','平均減3點','缺漏／未到期'],
                [[r['label'],r['cp'],f"{r['evaluated']}/{r['signals']}",r['distinctSignalDates']]+[number(r[k]) for k in ['median','positivePct','mean','meanCost1','meanCost3']]+[encoded(r['counts'])]
                 for r in report['events'][key]]))
    parts.append('<h2>資料覆蓋與證據</h2>'+table(['來源','資料日','比較日','已收錄／預期日數','狀態','缺日期','SHA-256'],
        [[s['label'],s['date'],s['baseDate'],str(s['days'])+'/'+str(s['expected']),s['status'],','.join(s['missingDates']),s['sha256']] for s in report['sources']]))
    parts.append('<h2>下一期觀察清單</h2>'+table(['訊號日','到期別','權別','履約價','規則','均量倍數','漲跌%','增減倉'],
        [[r['date'],r['month'],r['cp'],number(r['strike']),RULE_LABELS[r['rule']]]+[number(r[k]) for k in ['volumeMultiple','pricePct','oiChange']] for r in report['events']['watch']]))
    parts.append('<p>完整結構化數據：<a href="'+escape(report['id'])+'.json">下載 JSON</a>；報告內容雜湊 <code>'+report['contentHash']+'</code></p>')
    parts.append('<h2>方法與限制</h2><ul>'+''.join('<li>'+escape(x)+'</li>' for x in report['limitations'])+'</ul>')
    parts.append('<h2>官方來源連結</h2><ul>'+''.join('<li><a href="'+escape(s['url'], quote=True)+'">'+escape(s['label'])+'</a></li>' for s in report['sources'] if str(s.get('url') or '').startswith('https://'))+'</ul>')
    return '<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+escape(report['title'])+'</title><style>body{max-width:1100px;margin:24px auto;padding:0 16px;font:16px/1.6 system-ui;color:#14252e}h1{font-size:26px}th,td{text-align:left;padding:9px;border-bottom:1px solid #ddd;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%}.table{overflow:auto}code{overflow-wrap:anywhere}@media print{table{font-size:10px}.table{overflow:visible}h2{break-after:avoid}tr{break-inside:avoid}}</style>'+''.join(parts)+'</html>'


def archive(output, report, now):
    path = output/(ROOT+'.json')
    index = read(path, {'schemaVersion': 1, 'items': [], 'latest': {}, 'checks': {}})
    content_hash = digest(report)
    match = next((i for i in index['items'] if i.get('contentHash') == content_hash and i.get('archiveState') == 'current'), None)
    if match:
        return match, False
    same = [i for i in index['items'] if i['kind'] == report['kind'] and i['periodEnd'] == report['periodEnd']]
    revision = len(same)+1
    rid = f"{report['kind']}-{report['periodEnd']}-r{revision}-{content_hash[:12]}"
    full = {**report, 'id': rid, 'generatedAt': now, 'revision': revision, 'contentHash': content_hash,
            'revisionReason': '首次依已保存資料產生' if not same else '資料補齊、來源修訂或分析規則修正；舊版保留'}
    save(output/ROOT/(rid+'.json'), full)
    save_text(output/ROOT/(rid+'.html'), html_document(full))
    for item in same:
        item['archiveState'] = 'superseded'
    item = {k: full[k] for k in ['id','kind','periodStart','periodEnd','dataThrough','generatedAt','revision','contentHash','status','revisionReason']}
    item['archiveState'] = 'current'
    index['items'].append(item)
    current = next((i for i in index['items'] if i['id'] == index['latest'].get(report['kind'])), {})
    if report['periodEnd'] >= current.get('periodEnd', ''):
        index['latest'][report['kind']] = rid
    save(path, index)
    return item, True


def run(output, kind, today, now=None):
    now = now or datetime.now(timezone.utc).isoformat(timespec='seconds')
    index_path = output/(ROOT+'.json')
    kinds = ['daily','weekly','monthly'] if kind == 'all' else ['weekly','monthly'] if kind == 'both' else ['weekly']+(['monthly'] if today.day <= 7 else []) if kind == 'auto' else [kind]
    # Daily source corrections may complete an already-published week/month. Recheck
    # those periods without creating a future scheduled report or overwriting a revision.
    previous_index = read(index_path, {})
    if kind == 'daily':
        friday = today-timedelta(days=(today.weekday()-4) % 7)
        targets = {'weekly': friday.isoformat(), 'monthly': (today.replace(day=1)-timedelta(days=1)).isoformat()}
        for review_kind, end in targets.items():
            if any(i['kind'] == review_kind and i['periodEnd'] == end for i in previous_index.get('items', [])):
                kinds.append(review_kind)
    try:
        obs, issues = capture(output)
    except Exception as exc:
        index = read(index_path, {'schemaVersion': 1, 'items': [], 'latest': {}, 'checks': {}})
        for selected in kinds:
            index['checks'][selected] = {'status': 'retained', 'checkedAt': now, 'error': '輸入驗證失敗：'+str(exc)[:400]}
        index['status'] = 'retained'
        save(index_path, index)
        return [], [str(exc)]
    results, failures = [], []
    if kind == 'daily':
        changed = read(output/'market-history.json', {}).get('changedDates', [])
        calendar = load_calendar(output)
        for item in previous_index.get('items', []):
            if item.get('archiveState') != 'current': continue
            baseline = prior_open(calendar, item['periodStart'])
            if any(baseline <= d <= item['periodEnd'] for d in changed):
                try:
                    archive(output, build(output, obs, issues, item['kind'], item['periodStart'], item['periodEnd']), now)
                except Exception as exc:
                    failures.append(item['id']+': '+str(exc))
    for selected in kinds:
        try:
            if selected == 'daily':
                days = [d for name in ('futures-institutional','pcr','us-options') for d in obs['feeds'].get(name, {}) if d <= today.isoformat()]
                if not days:
                    raise ValueError('尚無可用的每日市場資料')
                start = end = max(days)
            elif selected == 'weekly':
                friday = today-timedelta(days=(today.weekday()-4) % 7)
                start, end = (friday-timedelta(days=4)).isoformat(), friday.isoformat()
            else:
                last = today.replace(day=1)-timedelta(days=1)
                start, end = last.replace(day=1).isoformat(), last.isoformat()
            report = build(output, obs, issues, selected, start, end)
            item, changed = archive(output, report, now)
            results.append({'kind': selected, 'id': item['id'], 'changed': changed, 'status': report['status']})
            check = {'status': 'ok', 'dataStatus': report['status'], 'periodEnd': end, 'checkedAt': now, 'error': None}
        except Exception as exc:
            failures.append(selected+': '+str(exc))
            check = {'status': 'retained', 'checkedAt': now, 'error': str(exc)[:500]}
        index = read(index_path, {'schemaVersion': 1, 'items': [], 'latest': {}, 'checks': {}})
        index['checks'][selected] = check
        index['status'] = 'retained' if any(c['status'] == 'retained' for c in index['checks'].values()) else 'ok'
        index['rulesVersion'] = VERSION
        index['schedule'] = '日報於台美資料更新後檢查；週六產生週報，每月第一個週六產生上月月報；來源延遲保留日期，補齊另存修訂。'
        save(index_path, index)
    return results, failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('data/derivatives'))
    parser.add_argument('--kind', choices=['daily','weekly','monthly','auto','all','both'], default='daily')
    parser.add_argument('--today', type=date.fromisoformat, default=datetime.now(timezone(timedelta(hours=8))).date())
    parser.add_argument('--rebuild-from', type=date.fromisoformat)
    args = parser.parse_args()
    if args.rebuild_from:
        obs, issues = capture(args.data)
        calendar = load_calendar(args.data)
        end = min(args.today.isoformat(), max(obs['feeds'].get('futures', {})))
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        for day in open_days(calendar, args.rebuild_from.isoformat(), end):
            archive(args.data, build(args.data, obs, issues, 'daily', day, day), now)
        cursor = args.rebuild_from
        while cursor.isoformat() <= end:
            if cursor.weekday() == 4:
                archive(args.data, build(args.data, obs, issues, 'weekly', (cursor-timedelta(days=4)).isoformat(), cursor.isoformat()), now)
            tomorrow = cursor+timedelta(days=1)
            if tomorrow.month != cursor.month:
                archive(args.data, build(args.data, obs, issues, 'monthly', cursor.replace(day=1).isoformat(), cursor.isoformat()), now)
            cursor = tomorrow
    results, failures = run(args.data, args.kind, args.today)
    print(json.dumps({'reports': results, 'failures': failures}, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
