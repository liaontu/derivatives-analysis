"""Compact, bounded warrant/underlying history using existing daily feeds."""
from collections import defaultdict
from datetime import date, timedelta
import re

SAFE_CODE = re.compile(r'[A-Za-z0-9_-]{1,24}')


def merge_history(old, quotes, underlying_rows, cutoff):
    """Use source dates. A same-day correction replaces; failures add nothing."""
    history = dict((old or {}).get('quotes', {}))
    for quote in quotes:
        code = quote['code']
        previous = history.get(code, {})
        rows = {r[0]: r for r in previous.get('rows', [])}
        rows[quote['date']] = [quote['date'], quote.get('price'), quote.get('volume')]
        meta = {k: quote.get(k) for k in ('name', 'market', 'cp', 'expiry', 'strike', 'style')}
        history[code] = {**meta, 'rows': [rows[d] for d in sorted(rows) if d >= cutoff][-60:]}
    # Also prune warrants no longer supplied (expired/delisted).
    history = {code: {**item, 'rows': [r for r in item['rows'] if r[0] >= cutoff][-60:]}
               for code, item in history.items()}
    history = {code: item for code, item in sorted(history.items()) if item['rows']}
    prices = {r[0]: r for r in (old or {}).get('underlyingPrices', [])}
    for row in underlying_rows:
        prices[row['date']] = [row['date'], row.get('price')]
    return {'quotes': history, 'underlyingPrices': [prices[d] for d in sorted(prices) if d >= cutoff][-60:]}


def archive(output, warrants, feeds, now, today, read, save):
    groups = defaultdict(list)
    for row in warrants:
        underlying = row.get('underlying', '')
        if isinstance(underlying, str) and SAFE_CODE.fullmatch(underlying):
            groups[underlying].append(row)
    old_index = read(output / 'warrant-history.json') or {'items': []}
    known = {i['code']: i for i in old_index['items']}
    for code, rows in groups.items():
        known[code] = {'code': code, 'name': rows[0].get('underlyingName') or code}
    otc = {r['code']: r for r in feeds.get('tpex-underlyings', {}).get('rows', [])}
    cutoff = (today - timedelta(days=120)).isoformat()
    items = []
    for code, item in sorted(known.items()):
        if not SAFE_CODE.fullmatch(code):
            continue
        path = output / 'warrant-history' / f'{code}.json'
        old = read(path)
        quotes, prices = [], []
        for row in groups.get(code, []):
            source = 'twse-warrants' if row['market'] == 'TWSE' else 'tpex-warrants'
            if feeds.get(source, {}).get('status') == 'ok':
                quotes.append(row)
                if row['market'] == 'TWSE' and 'underlyingPrice' in row:
                    prices.append({'date': row['date'], 'price': row['underlyingPrice']})
        if code in otc and feeds.get('tpex-underlyings', {}).get('status') == 'ok':
            prices.append(otc[code])
        # Underlying prices repeated by the warrant report should agree.
        by_day = defaultdict(set)
        for row in prices:
            by_day[row['date']].add(row['price'])
        prices = [{'date': day, 'price': next(iter(values)) if len(values) == 1 else None}
                  for day, values in sorted(by_day.items())]
        merged = merge_history(old, quotes, prices, cutoff)
        payload = {'schemaVersion': 1, 'underlying': code, 'name': item['name'], **merged}
        if payload != old:
            save(path, payload)
        dates = sorted({r[0] for q in merged['quotes'].values() for r in q['rows']})
        if dates:
            items.append({**item, 'date': dates[-1], 'days': len(dates), 'count': len(merged['quotes'])})
    save(output / 'warrant-history.json', {'schemaVersion': 1, 'items': items,
                                          'limit': 60, 'startPolicy': 'collected-source-dates-only'})
