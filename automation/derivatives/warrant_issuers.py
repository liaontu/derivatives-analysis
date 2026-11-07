"""Versioned issuer identity heuristics shared by all warrant research modules."""
import re
import json
from hashlib import sha256

VERSION = "warrant-issuer-identity-v1"
POLICY = "exclude-issuer-head-office-v1"
ISSUERS = ('元大', '凱基', '群益', '永豐', '統一', '國票', '台新', '富邦', '國泰', '中信', '兆豐', '元富', '康和')
ISSUER_PATTERN = re.compile('(' + '|'.join(ISSUERS) + r')[0-9A-Z]{2}[購售牛熊][0-9]{2}$')
BROKERS = {'元大': '元大', '元大證券': '元大', '凱基': '凱基', '凱基證券': '凱基', '群益金鼎': '群益', '群益': '群益',
           '永豐金': '永豐', '永豐金證券': '永豐', '統一': '統一', '統一證券': '統一', '國票': '國票', '國票證券': '國票',
           '台新': '台新', '台新證券': '台新', '富邦': '富邦', '富邦證券': '富邦', '國泰': '國泰', '國泰證券': '國泰',
           '中國信託': '中信', '中信': '中信', '兆豐': '兆豐', '兆豐證券': '兆豐', '元富': '元富', '元富證券': '元富',
           '康和': '康和', '康和證券': '康和'}
RELATIONS = {'issuer': '發行商總公司（疑似造市）', 'sameBroker': '同券商分點', 'other': '其他分點', 'unknown': '發行商未判定'}


def issuer_of(name):
    """The issuer written into a warrant name, or None when the name does not follow the convention."""
    match = ISSUER_PATTERN.search(name or '')
    return match.group(1) if match else None


def branch_relation(branch_name, issuer):
    """issuer: the warrant issuer's own head-office branch (presumed market making by name matching); sameBroker: one of its retail branches."""
    if not issuer:
        return 'unknown'
    parts = re.split(r'[-－]', branch_name or '')
    broker = BROKERS.get(parts[0].strip())
    if broker != issuer:
        return 'other'
    return 'issuer' if len(parts) == 1 else 'sameBroker'


def market_making(branches):
    """Per side, the issuers' head-office net flow among ranked warrants; its negative is a rough estimate of those warrants' market net buying, not a market-wide figure."""
    out = {}
    for cp in ('C', 'P'):
        rows = [r for r in branches if r['cp'] == cp and r.get('relation') == 'issuer']
        net = sum(r['net'] for r in rows)
        out[cp] = {'rows': len(rows), 'warrants': len({r['code'] for r in rows}), 'issuerNet': net, 'marketNet': -net}
    return out



def customer_eligible(relation):
    return relation in ("other", "sameBroker")

RULES_SHA256 = sha256(json.dumps({"version":VERSION,"policy":POLICY,"issuers":ISSUERS,"brokers":BROKERS,"eligible":["other","sameBroker"]},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
