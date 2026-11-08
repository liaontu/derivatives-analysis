"""Compact, bounded warrant/underlying history using existing daily feeds."""
from collections import defaultdict
from datetime import date, timedelta
import re
import math

SAFE_CODE = re.compile(r'[A-Za-z0-9_-]{1,24}')


def trade_price(price, volume):
    """A close is observed only when both the price and traded volume are positive."""
    return price if all(type(x) in (float, int) and math.isfinite(x) and x > 0 for x in (price, volume)) else None


def merge_history(old, quotes, underlying_rows, cutoff):
    """Use source dates. A same-day correction replaces; failures add nothing."""
    history = dict((old or {}).get('quotes', {}))
    for quote in quotes:
        code = quote['code']
        previous = history.get(code, {})
        if any(previous.get(k) and quote.get(k) and previous[k] != quote[k] for k in ('name', 'market', 'expiry')):
            previous = {}
        rows = {r[0]: r for r in previous.get('rows', [])}
        existing=rows.get(quote['date'])
        # A later unavailable volume feed is not a source correction. Preserve
        # an observed same-day row; an explicit zero-volume correction still applies.
        kept=quote.get('volume') is None and existing is not None and existing[2] is not None
        if not kept:rows[quote['date']] = [quote['date'], quote.get('price'), quote.get('volume')]
        meta = {k: quote.get(k) for k in ('name', 'market', 'cp', 'expiry', 'strike', 'style')}
        retained=set(previous.get('retainedDates',[]))
        if kept:retained.add(quote['date'])
        else:retained.discard(quote['date'])
        if retained:meta['retainedDates']=sorted(d for d in retained if d>=cutoff)
        history[code] = {**meta, 'rows': [rows[d] for d in sorted(rows) if d >= cutoff][-60:]}
    # Also prune warrants no longer supplied (expired/delisted).
    history = {code: {**item, 'rows': [[d, trade_price(p, v), v] for d, p, v in item['rows'] if d >= cutoff][-60:]}
               for code, item in history.items()}
    history = {code: item for code, item in sorted(history.items()) if item['rows']}
    prices = {r[0]: r for r in (old or {}).get('underlyingPrices', [])}
    for row in underlying_rows:
        old_price=prices.get(row['date'],[None,None])[1]
        if (row.get('price') is None or row['price']<=0) and old_price is not None and old_price>0:continue
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
