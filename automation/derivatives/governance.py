"""Public data-quality receipts. No credentials, paid services or private notes."""
from collections import Counter
from datetime import date
from hashlib import sha256
import json
try:
    from .trading_calendar import default_calendar, CalendarUnavailable
except ImportError:
    from trading_calendar import default_calendar, CalendarUnavailable

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


def source_quality(source, today, calendar=None):
    issues = []
    status = source.get('status')
    if status != 'ok':
        issues.append({'type': 'Delayed update' if status == 'retained' else 'Missing',
                       'severity': 'High', 'message': '更新失敗，保留上次成功值' if status == 'retained' else '來源無可用資料'})
    try:
        trading = source.get('region') == 'TW' and source.get('frequency') != 'weekly'
        age = (calendar or default_calendar()).age(source['date'], today) if trading else (today - date.fromisoformat(source['date'])).days
        if age < 0 or age > source.get('staleAfterTradingDays' if trading else 'staleAfterDays', 4):
            issues.append({'type': 'Time mismatch', 'severity': 'Medium',
                           'message': '資料日期超出交易日新鮮度提醒範圍' if trading else '資料日期超出日曆天新鮮度提醒範圍；可能包含休市或延後發布'})
    except CalendarUnavailable:
        issues.append({'type': 'Calendar unavailable', 'severity': 'High', 'message': '交易日曆未驗證或年份未涵蓋，無法判斷新鮮度'})
    except (KeyError, TypeError, ValueError):
        issues.append({'type': 'Missing', 'severity': 'High', 'message': '缺少有效來源日期'})
    if not (source.get('provenance') or {}).get('sha256'):
        issues.append({'type': 'Provenance missing', 'severity': 'Medium', 'message': '舊資料尚無內容校驗碼，待成功更新補上'})
    return {'level': 'blocked' if status != 'ok' else 'partial' if issues else 'checked',
            'issues': issues, 'scope': '來源日期、下載狀態與內容版本；不代表每個欄位完整或可正式建模'}


def receipt_state(source, quality):
    return [source['id'], source.get('status'), source.get('date'), (source.get('provenance') or {}).get('sha256'),
            quality['level'], sorted(i['type'] for i in quality['issues'])]


def collect_quality(output, manifest, read, save, today, now=None, calendar=None):
    """Receipts record each change of a source's published state; an unchanged rerun adds nothing."""
    now = now or manifest.get('updatedAt') or manifest.get('checkedAt')
    previous = read(output / 'source-audit.json') or {'receipts': []}
    receipts = [{**{k: v for k, v in r.items() if k != 'checkedAt'}, 'observedAt': r.get('observedAt') or r.get('checkedAt')}
                for r in previous['receipts']]
    sources = []
    for source in manifest['sources']:
        quality = source_quality(source, today, calendar)
        sources.append({**source, 'quality': quality})
        state = receipt_state(source, quality)
        latest = max((r for r in receipts if r['source'] == source['id']), key=lambda r: (r['observedAt'] or '', r['id']), default=None)
        if latest and latest.get('state') == state:
            continue
        receipts.append({'id': digest([state, latest['id'] if latest else None])[:24], 'source': source['id'], 'label': source['label'],
                         'observedAt': now, 'date': source.get('date'), 'fetchedAt': source.get('fetchedAt'), 'status': source.get('status'),
                         'url': source['url'], 'count': source.get('count'), 'provenance': source.get('provenance'), 'quality': quality,
                         'state': state, 'previousId': latest['id'] if latest else None})
    ordered = sorted(receipts, key=lambda r: (r['observedAt'] or '', r['source'], r['id']))
    # The window drops the oldest records first but never a source's latest state, which anchors the next comparison.
    keep = {max((r for r in ordered if r['source'] == name), key=lambda r: (r['observedAt'] or '', r['id']))['id']
            for name in {r['source'] for r in ordered}}
    excess, trimmed = len(ordered) - RECEIPT_LIMIT, []
    for receipt in ordered:
        if excess > 0 and receipt['id'] not in keep:
            excess -= 1
            continue
        trimmed.append(receipt)
    ordered = trimmed
    report = {'version': VERSION,
              'counts': dict(Counter(s['quality']['level'] for s in sources)), 'sources': sources,
              'receiptCount': len(ordered), 'receiptLimit': RECEIPT_LIMIT,
              'firstReceiptAt': ordered[0]['observedAt'] if ordered else None,
              'receiptBasis': '每筆紀錄代表某來源發布狀態（取得狀態、資料日、內容校驗碼、品質判定）的一次變化；內容未變的重跑不新增紀錄',
              'recentIssues': [r for r in reversed(ordered) if r['quality']['issues']][:20],
              'limitations': [
                  '官方來源優先；第三方分點排行只作輔助，不能替代官方行情或推定最終投資人身分。',
                  '缺值、不同日期與零成交分開處理；不補零、不以估算值製造正式績效。',
                  '歷史補抓與持續累積資料均屬可重算研究；未建立不可改寫的即時預測帳本，Live OOS 樣本為 0。',
                  '未完整驗證當時可得時間、交易日曆、結算條款與交易成本；正式模型資格維持否。'],
              'policyBasis': ['台股科技3年歷史回補 ETL v2｜Safe Fast Mode 規格',
                              '每日作業流程與模型治理規格', '券商分點免費公開來源口徑與更新時間不一致']}
    save(output / 'source-audit.json', {'schemaVersion': 2, 'version': VERSION,
                                      'limit': RECEIPT_LIMIT, 'receipts': ordered})
    save(output / 'data-quality.json', report)
    return report
