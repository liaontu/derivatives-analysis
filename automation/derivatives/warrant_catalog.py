"""Small underlying catalogue and deterministic per-underlying warrant snapshots."""
import re
from collections import defaultdict


def publish_catalog(output, rows, save):
    groups = defaultdict(list)
    for row in rows:
        code = row.get('underlying')
        if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,24}', code):
            groups[code].append(row)
    items, codes = [], {}
    for code, group in sorted(groups.items()):
        group.sort(key=lambda r: (r['market'], r['code']))
        save(output / 'warrants-by-underlying' / f'{code}.json', {'rows': group})
        name = next((r.get('underlyingName') for r in group if r.get('underlyingName')), code)
        items.append({'code': code, 'name': name, 'label': f'{code} · {name}', 'count': len(group)})
        codes.update({r['code']: code for r in group})
    save(output / 'warrant-catalog.json', {'schemaVersion': 1, 'items': items, 'count': len(rows)})
    save(output / 'warrant-code-index.json', {'codes': dict(sorted(codes.items()))})
