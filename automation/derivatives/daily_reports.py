"""Archive two independent Taiwan market reports at their own source dates."""
from collections import Counter, defaultdict
from datetime import date, timedelta
import argparse
import json
from pathlib import Path
import re

if __package__:
    from .publication import save_text
    from .warrant_focus import RULES, numeric, percent, volume_evidence
    from .trading_calendar import load_calendar
else:
    from publication import save_text
    from warrant_focus import RULES, numeric, percent, volume_evidence
    from trading_calendar import load_calendar

WARRANT_SOURCES = ('twse-warrants', 'twse-volume', 'twse-volume-put', 'tpex-warrants', 'tpex-terms')
POSITION_SOURCES = ('futures-institutional', 'options-institutional', 'futures-large',
                    'options-large', 'options', 'pcr', 'futures-fcm', 'options-fcm')


def read(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if path.name == 'warrants.json' and value.get('format') == 'underlying-shards':
        if __package__:
            from .warrant_catalog import read_warrants
        else:
            from warrant_catalog import read_warrants
        return read_warrants(path.parent, read)
    return value


def save(path, data):
    save_text(path, json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False))


def aligned(sources, names):
    entries = {s['id']: s for s in sources if s['id'] in names}
    dates = {s.get('date') for s in entries.values()}
    if set(entries) != set(names) or len(dates) != 1 or any(s.get('status') != 'ok' for s in entries.values()):
        raise ValueError('必要來源缺漏、異常或資料日期不同')
    day = date.fromisoformat(dates.pop()).isoformat()
    references = [{k: entries[n].get(k) for k in ('id', 'date', 'url', 'fetchedAt')} for n in names]
    return day, references


def traded(point):
    return len(point) >= 3 and all(numeric(v) and v > 0 for v in point[1:3])


def baseline_days(day, calendar):
    cursor = date.fromisoformat(day)
    days = []
    while len(days) < RULES['volumeWindow']:
        cursor -= timedelta(days=1)
        if calendar.is_open(cursor):
            days.append(cursor.isoformat())
    return sorted(days)


def volume_exclusion(row):
    code, cp = row.get('code', ''), row.get('cp')
    pattern = r'\d{6}' if cp == 'C' else r'\d{5}[PUT]' if cp == 'P' else r'(?!)'
    if not re.fullmatch(pattern, code):
        return '認購售代碼／權別未確認'
    if not traded([row.get('date'), row.get('price'), row.get('volume')]):
        return '無有效成交價格或成交量'
    return None


def warrant_report(output, manifest):
    day, sources = aligned(manifest['sources'], WARRANT_SOURCES)
    directory = read(output / 'stock-warrant-directory.json')
    if directory.get('status') != 'ok' or directory.get('quoteDates') != [day]:
        raise ValueError('等待同日個股權證名錄')
    names = {s['code']: s for s in directory['items']}
    all_rows = read(output / 'warrants.json')['rows']
    rows = [r for r in all_rows if r.get('underlying') in names and r['date'] == day]
    if len(rows) != directory['count']:
        raise ValueError('個股權證名錄與行情檔數不同')
    histories = {}
    for code in names:
        if not re.fullmatch(r'[0-9]{4}', code):
            raise ValueError('個股代碼格式不符')
        path = output / 'warrant-history' / (code + '.json')
        if not path.exists():
            continue
        history = read(path)
        if history.get('underlying') != code:
            continue
        histories[code] = history
    membership = {'dates':{k:v for k,v in directory['sourceDates'].items() if k.endswith('Companies')},
                  'basis':'daily-directory'}
    report = build_volume_report(day, rows, histories, names, sources, baseline_days(day, load_calendar(output)))
    report['coverage'] = {'status':'available', 'markets':['TWSE','TPEX'], 'membership':membership,
                          'notes':[], 'missingSources':[]}
    return report


def build_volume_report(day, rows, histories, names, sources, baseline):
    codes = Counter(r['code'] for r in rows)
    stocks = {code: {'code': code, 'name': info['name'], 'total': 0, 'eligible': 0,
                     'callBase': 0, 'putBase': 0, 'call': 0, 'put': 0, 'hits': []} for code, info in names.items()}
    reasons = Counter()
    for row in rows:
        stock = stocks[row['underlying']]
        stock['total'] += 1
        reason = volume_exclusion(row) if codes[row['code']] == 1 else '權證代碼重複'
        if reason:
            reasons[reason] += 1
            continue
        history = histories.get(row['underlying'], {})
        q = history.get('quotes', {}).get(row['code'], {})
        if any(q.get(k) != row.get(k) for k in ('market', 'name', 'cp')):
            reasons['無吻合的權證歷史'] += 1
            continue
        evidence = volume_evidence(row, history, baseline)
        if evidence['status'] != '可比較':
            reasons[evidence['status']] += 1
            continue
        side = 'call' if row['cp'] == 'C' else 'put'
        stock['eligible'] += 1
        stock[side + 'Base'] += 1
        if not evidence['signal']:
            continue
        stock[side] += 1
        prior = max((p for p in q['rows'] if p[0] < day and traded(p)), default=None, key=lambda p: p[0])
        spot = dict(history['underlyingPrices'])
        wr = percent(row['price'], prior[1]) if prior else None
        sr = percent(spot.get(day), spot.get(prior[0])) if prior else None
        stock['hits'].append({'code': row['code'], 'name': row['name'], 'cp': row['cp'],
                              'volume': row['volume'], 'multiple': round(evidence['multiple'], 3),
                              'previous': prior[0] if prior else None, 'warrantPct': wr, 'stockPct': sr})
    for stock in stocks.values():
        stock['hits'].sort(key=lambda r: (-r['volume'], r['code']))
    return {'date': day, 'sources': sources, 'baselineDates':baseline,
            'rules': {'version':'volume-only-v2', **{k: RULES[k] for k in ('minVolume','volumeWindow','volumeMultiple')}},
            'excluded': dict(reasons), 'stocks': list(stocks.values())}


def position_report(output, manifest):
    day, sources = aligned(manifest['sources'], POSITION_SOURCES)
    feeds = {}
    for name in POSITION_SOURCES:
        feed = read(output / (name + '.json'))
        if feed.get('date') != day:
            raise ValueError('來源行情檔與資料清單日期不同')
        feeds[name] = [r for r in feed['rows'] if r.get('date') == day]
    institutions, large, brokers = [], [], {}
    for prefix, label, symbol in [('futures','臺股期貨','TX'), ('options','臺指選擇權','TXO')]:
        institutions.extend({'product': prefix, **{k:r[k] for k in ('investor','cp','longOi','shortOi','netOi')}}
                            for r in feeds[prefix + '-institutional'] if r['contract'] == label)
        large.extend({'product': prefix, **{k:r[k] for k in ('cp','name','top5Buy','top5Sell','top10Buy','top10Sell','oi')}}
                     for r in feeds[prefix + '-large'] if r['contract'] == symbol and r['month'] == '999912' and r['traderType'] == '0')
        brokers[prefix] = sorted(({'name':r['name'],'volume':r['volume']} for r in feeds[prefix + '-fcm']
                                 if r['contract'] == ('TXF' if prefix == 'futures' else 'TXO') and numeric(r.get('volume'))),
                                key=lambda r: (-r['volume'], r['name']))[:5]
    series = defaultdict(dict)
    for r in feeds['options']:
        if (r['contract'] != 'TXO' or r['session'] != '一般' or r['cp'] not in ('C','P')
                or not numeric(r['strike']) or not re.fullmatch(r'20\d{4}(?:W[1-5]|F[1-5])?', r['month'] or '')):
            continue
        key = (r['month'], r['strike'])
        if r['cp'] in series[key]:
            raise ValueError('臺指選擇權履約價序列重複')
        oi = r['oi']
        series[key][r['cp']] = oi if numeric(oi) and oi >= 0 else None
    if not all([institutions, large, series]) or len(feeds['pcr']) != 1:
        raise ValueError('必要籌碼表沒有有效資料')
    return {'date': day, 'sources': sources, 'institutions': institutions, 'large': large, 'brokers': brokers,
            'pcr': feeds['pcr'][0], 'strikes': [[month,k,s.get('C'),s.get('P')] for (month,k),s in sorted(series.items())]}


REPORTS = {'warrant-volume': warrant_report, 'positioning': position_report}


def archive(output, kind, report):
    folder = output / 'daily-reports' / kind
    save(folder / (report['date'] + '.json'), report)
    days = sorted(p.stem for p in folder.glob('????-??-??.json'))
    save(folder / 'index.json', {'dates': days, 'status':'ok'})
    if kind == 'warrant-volume':
        stocks = report['stocks']
        update_coverage(output, {report['date']:{
            **report.get('coverage', {'status':'available'}),
            'total':sum(s['total'] for s in stocks), 'eligible':sum(s['eligible'] for s in stocks),
            'surges':sum(s['call']+s['put'] for s in stocks)}})


def update_coverage(output, entries, start=None, end=None):
    path = output / 'daily-reports/warrant-volume/coverage.json'
    previous = read(path) if path.exists() else {'days':{}}
    days = {**previous['days'], **entries}
    dates = list(days)
    if start:
        dates.append(str(start))
    if end:
        dates.append(str(end))
    start, end = min(dates), max(dates)
    calendar = load_calendar(output)
    cursor = date.fromisoformat(start)
    while cursor.isoformat() <= end:
        key = cursor.isoformat()
        if key not in days:
            days[key] = {'status':'missing' if calendar.is_open(cursor) else 'closed',
                         'notes':['尚無通過核對的日報'] if calendar.is_open(cursor) else ['依已核實交易日曆休市']}
        cursor += timedelta(days=1)
    save(path, {'start':start,'end':end,'days':dict(sorted(days.items()))})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=Path('data/derivatives'))
    args = parser.parse_args()
    manifest = read(args.data / 'manifest.json')
    errors = []
    for kind, builder in REPORTS.items():
        try:
            archive(args.data, kind, builder(args.data, manifest))
        except Exception as exc:
            folder = args.data / 'daily-reports' / kind
            names = WARRANT_SOURCES if kind == 'warrant-volume' else POSITION_SOURCES
            observed = [{k:s.get(k) for k in ('id','date','status')} for s in manifest['sources'] if s['id'] in names]
            save(folder / 'index.json', {'dates':sorted(p.stem for p in folder.glob('????-??-??.json')),
                                         'status':'retained', 'attemptSources':observed})
            errors.append(f'{kind}: {exc}')
    if errors:
        raise SystemExit('; '.join(errors))


if __name__ == '__main__':
    main()
