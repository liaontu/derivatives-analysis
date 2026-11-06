"""Small underlying catalogue and deterministic per-underlying warrant snapshots."""
import re
from collections import defaultdict
try:
    from .warrant_history import trade_price
except ImportError:
    from warrant_history import trade_price


def comparison(row, history, prices):
    item = history.get('quotes', {}).get(row['code'], {})
    if any(item.get(k) and row.get(k) and item[k] != row[k] for k in ('market', 'name', 'expiry')):
        item = {}
    day = row['date']
    points = sorted((p for p in item.get('rows', []) if p[0] <= day), key=lambda p: p[0])
    prior = [p for p in points if p[0] < day]
    trade = next((p for p in reversed(prior) if trade_price(p[1], p[2]) is not None), None)
    last = prior[-1] if prior else None
    aligned = bool(points and points[-1][0] == day and trade_price(*points[-1][1:3]) == row.get('price') and points[-1][2] == row.get('volume'))
    return [trade[0] if trade else None, trade[1] if trade else None,
            last[0] if last else None, last[2] if last else None, prices.get(day),
            prices.get(trade[0]) if trade else None, len(points), aligned]


def publish_catalog(output, rows, save, read=None):
    groups = defaultdict(list)
    for row in rows:
        code = row.get('underlying')
        if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,24}', code):
            groups[code].append(row)
    items, codes = [], {}
    for code, group in sorted(groups.items()):
        group.sort(key=lambda r: (r['market'], r['code']))
        if read:
            history = read(output / 'warrant-history' / f'{code}.json') or {}
            prices = dict(history.get('underlyingPrices', []))
            group = [{**r, 'basis': comparison(r, history, prices)} for r in group]
        save(output / 'warrants-by-underlying' / f'{code}.json', {'rows': group})
        name = next((r.get('underlyingName') for r in group if r.get('underlyingName')), code)
        items.append({'code': code, 'name': name, 'label': f'{code} · {name}', 'count': len(group)})
        codes.update({r['code']: code for r in group})
    preview = sorted(rows, key=lambda r: (-(r.get('volume') if r.get('volume') is not None else -1), r['code'], r.get('market','')))[:2000]
    save(output / 'warrants-preview.json', {'rows': preview, 'total': len(rows)})
    save(output / 'warrant-catalog.json', {'schemaVersion': 2, 'items': items, 'count': len(rows)})
    save(output / 'warrant-code-index.json', {'codes': dict(sorted(codes.items()))})
