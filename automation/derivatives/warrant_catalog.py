"""Small underlying catalogue and deterministic per-underlying warrant snapshots."""
import re
import json
from collections import defaultdict
try:
    from .warrant_history import trade_price
except ImportError:
    from warrant_history import trade_price


def read_warrants(output, read):
    """Current quotes have one canonical copy, in validated underlying shards."""
    path = output / 'warrants.json'
    legacy = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    if legacy and 'rows' in legacy:
        return legacy
    index = read(output / 'warrant-catalog.json')
    if not index or not isinstance(index.get('items'), list):
        raise ValueError('Warrant shard index unavailable')
    rows = []
    for item in index['items']:
        code = item['code']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,24}', code):
            raise ValueError('Unsafe warrant shard')
        part = read(output / 'warrants-by-underlying' / (code + '.json'))
        if not part or len(part.get('rows', [])) != item['count']:
            raise ValueError('Incomplete warrant shard: ' + code)
        if any(r.get('underlying') != code for r in part['rows']):
            raise ValueError('Mismatched warrant shard: ' + code)
        rows.extend({k: v for k, v in r.items() if k != 'basis'} for r in part['rows'])
    if len(rows) != index['count'] or len({(r['market'], r['code']) for r in rows}) != len(rows):
        raise ValueError('Warrant catalogue count or identity mismatch')
    return {'rows': rows}


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
    if sum(len(group) for group in groups.values()) != len(rows):
        raise ValueError('Cannot shard warrants without a valid underlying')
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
    save(output / 'warrant-catalog.json', {'schemaVersion': 3, 'items': items, 'count': len(rows)})
    save(output / 'warrant-code-index.json', {'codes': dict(sorted(codes.items()))})
