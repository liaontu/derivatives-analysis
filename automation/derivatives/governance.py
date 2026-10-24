"""Public data-quality receipts. No credentials, paid services or private notes."""
from collections import Counter
from datetime import date
from hashlib import sha256
import json

VERSION = 'research-governance-v1'
RECEIPT_LIMIT = 4000


def sample_maturity(count):
    return 'Preliminary' if count < 50 else 'Early' if count < 100 else 'Medium' if count < 250 else '250+'


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def provenance(payload, old, url):
    """Hash published normalized rows, not HTTP bytes or a signature of accuracy."""
    checksum = digest(payload['rows'])
    previous = digest(old['rows']) if old and old.get('rows') else None
    return {'url': url, 'sourceDate': payload['date'], 'fetchedAt': payload['fetchedAt'],
            'sha256': checksum, 'hashBasis': 'normalized-published-rows-v1',
            'sameDateRevision': bool(old and old.get('date') == payload['date'] and
                                     previous and checksum != previous),
            'previousSha256': previous if old and old.get('date') == payload['date'] and
                              previous != checksum else None}


def source_quality(source, today):
    issues = []
    status = source.get('status')
    if status != 'ok':
        issues.append({'type': 'Delayed update' if status == 'retained' else 'Missing',
                       'severity': 'High', 'message': '更新失敗，保留上次成功值' if status == 'retained' else '來源無可用資料'})
    try:
        age = (today - date.fromisoformat(source['date'])).days
        if age < 0 or age > source.get('staleAfterDays', 4):
            issues.append({'type': 'Time mismatch', 'severity': 'Medium',
                           'message': '資料日期超出新鮮度提醒範圍；可能包含休市或延後發布'})
    except (KeyError, TypeError, ValueError):
        issues.append({'type': 'Missing', 'severity': 'High', 'message': '缺少有效來源日期'})
    if not (source.get('provenance') or {}).get('sha256'):
        issues.append({'type': 'Provenance missing', 'severity': 'Medium', 'message': '舊資料尚無內容校驗碼，待成功更新補上'})
    return {'level': 'blocked' if status != 'ok' else 'partial' if issues else 'checked',
            'issues': issues, 'scope': '來源日期、下載狀態與內容版本；不代表每個欄位完整或可正式建模'}


def collect_quality(output, manifest, read, save, today):
    previous = read(output / 'source-audit.json') or {'receipts': []}
    receipts = {r['id']: r for r in previous['receipts']}
    sources = []
    for source in manifest['sources']:
        quality = source_quality(source, today)
        sources.append({**source, 'quality': quality})
        key = digest([source['id'], manifest['checkedAt'], source.get('status'), source.get('date'),
                      (source.get('provenance') or {}).get('sha256')])[:24]
        receipts[key] = {'id': key, 'source': source['id'], 'label': source['label'],
                         'checkedAt': manifest['checkedAt'], 'date': source.get('date'),
                         'fetchedAt': source.get('fetchedAt'), 'status': source.get('status'),
                         'url': source['url'], 'count': source.get('count'),
                         'provenance': source.get('provenance'), 'quality': quality}
    ordered = sorted(receipts.values(), key=lambda r: (r['checkedAt'], r['source']))[-RECEIPT_LIMIT:]
    report = {'version': VERSION, 'checkedAt': manifest['checkedAt'],
              'counts': dict(Counter(s['quality']['level'] for s in sources)), 'sources': sources,
              'receiptCount': len(ordered), 'receiptLimit': RECEIPT_LIMIT,
              'firstReceiptAt': ordered[0]['checkedAt'] if ordered else None,
              'recentIssues': [r for r in reversed(ordered) if r['quality']['issues']][:20],
              'limitations': [
                  '官方來源優先；第三方分點排行只作輔助，不能替代官方行情或推定最終投資人身分。',
                  '缺值、不同日期與零成交分開處理；不補零、不以估算值製造正式績效。',
                  '歷史補抓與持續累積資料均屬可重算研究；未建立不可改寫的即時預測帳本，Live OOS 樣本為 0。',
                  '未完整驗證當時可得時間、交易日曆、結算條款與交易成本；正式模型資格維持否。'],
              'policyBasis': ['台股科技3年歷史回補 ETL v2｜Safe Fast Mode 規格',
                              '每日作業流程與模型治理規格', '券商分點免費公開來源口徑與更新時間不一致']}
    save(output / 'source-audit.json', {'schemaVersion': 1, 'version': VERSION,
                                      'limit': RECEIPT_LIMIT, 'receipts': ordered})
    save(output / 'data-quality.json', report)
    return report
