"""Versioned observation reports from the saved paper ledger, with no AI calls."""
from collections import Counter
from html import escape
from pathlib import Path

try:
    from .publication import save_text
    from .warrant_lab import digest, drawdown, money, review_metrics, validate
except ImportError:
    from publication import save_text
    from warrant_lab import digest, drawdown, money, review_metrics, validate

DESCRIPTIONS = {
    'adaptive': '後續新委託採目前選用規則；持倉與已排定委託保持原規則。',
    'volume2-h5': '成交量達前五期均量 2 倍，持有 5 個有效觀察期。',
    'volume3-h5': '成交量達前五期均量 3 倍，持有 5 個有效觀察期。',
    'volume2-h10': '成交量達前五期均量 2 倍，持有 10 個有效觀察期。',
    'relative-low-h5': 'Z≤−2，且本檔換算價低於同條件對照權證，持有 5 期。',
    'trend-volume-h5': '量增至少 2 倍，權證上漲；認購的標的上漲，認售的標的下跌，持有 5 期。',
    'pullback-volume-h5': '量增至少 2 倍，權證下跌，但認購的標的上漲／認售的標的下跌，持有 5 期。',
    'dual-low-volume-h5': '同時符合量增至少 2 倍、Z≤−2及本檔換算價低於對照，持有 5 期。',
}
LIMITATIONS = [
    '僅上市一般權證、虛擬資金；成本、滑價和成交量限制為研究假設，未驗證實際可成交。',
    '補抓的歷史不當作啟用後成交；新策略不回填舊交易。開始時間不同時只比較共同觀察區間。',
    '候選可共用權證，交易次數與報酬不是獨立樣本；多策略反覆比較仍有選擇偏差。',
    '缺價、低量、到期無法退出會保留待確認部位；不補零、不假裝成交或自動履約。',
    '樣本不足不宣布最佳策略；報告是固定規則整理，沒有付費 AI 呼叫或獲利保證。',
]


def common_window(accounts):
    curves = [{p['date']: p['equity'] for p in a['curve']} for a in accounts.values()]
    if not curves:
        return None
    common = sorted(set.intersection(*(set(c) for c in curves)))
    if len(common) < 2:
        return None
    values = {}
    for key, curve in zip(accounts, curves):
        values[key] = ((curve[common[-1]] / curve[common[0]] - 1) * 100
                       if all(curve[d] is not None and curve[d] > 0 for d in common) else None)
    return {'first': common[0], 'last': common[-1], 'days': len(common), 'returns': values}


def build_observation(lab, report_date):
    strategies = {s['id']: s for s in lab['rules']['strategies']}
    names = {'adaptive': '自動選用帳戶', **{k: s['name'] for k, s in strategies.items()}}
    events = [e for e in lab['journal'] if e.get('marketDate') == report_date]
    actions = [a for e in events for a in e['actions']]
    accounts = []
    for key, acc in lab['accounts'].items():
        curve, trades = acc['curve'], acc['trades']
        last = curve[-1] if curve else None
        equity = last['equity'] if last else lab['rules']['initialCash']
        own = [a for a in actions if a['account'] == key]
        counts = dict(Counter(a['type'] for a in own))
        failures = dict(Counter(a.get('reason', '未提供原因') for a in own if a['type'] in ('cancel-buy', 'unfilled-sell')))
        stale = sum(bool(p['stale']) for p in acc['positions'])
        prior = curve[-2]['equity'] if len(curve) > 1 else lab['rules']['initialCash']
        daily_change = (money(equity - prior) if events and last and last['date'] == report_date
                        and equity is not None and prior is not None else None)
        accounts.append({'id': key, 'name': names[key], 'description': DESCRIPTIONS[key],
                         'startedOn': lab.get('accountStartedOn', {}).get(key, lab['startedOn']),
                         'observations': len(curve), 'asOf': last['date'] if last else None,
                         'equity': equity, 'cash': acc['cash'], 'dailyChange': daily_change,
                         'returnPct': (equity / lab['rules']['initialCash'] - 1) * 100 if equity is not None else None,
                         'maxDrawdownPct': drawdown(curve) * 100 if all(p['equity'] is not None for p in curve) else None,
                         'realizedPnl': money(sum(t['pnl'] for t in trades)),
                         'stressPnl': money(sum(t['stressPnl'] for t in trades)) if trades else None,
                         'closed': len(trades), 'winRate': sum(t['pnl'] > 0 for t in trades) / len(trades) * 100 if trades else None,
                         'positions': len(acc['positions']), 'pending': len(acc['orders']), 'unresolved': stale,
                         'counts': counts, 'failures': failures,
                         'sampleStatus': '資料待確認' if stale else '具比較樣本，仍須完整門檻' if review_metrics(acc) else '樣本不足，持續觀察'})
    common = common_window(lab['accounts'])
    today_review = [r for r in lab['reviews'] if r['date'] == report_date]
    observations = [f"目前選用「{names[lab['currentRule']]}」，共 {len(strategies)} 個固定候選並行；尚未完成的委託不計成交。"]
    if events:
        total = Counter(a['type'] for a in actions)
        observations.append(f"本次行情已記錄：各帳戶合計買入 {total['buy']} 筆、賣出 {total['sell']} 筆、取消買單 {total['cancel-buy']} 筆、賣出未成 {total['unfilled-sell']} 筆；合計不是獨立市場訊號數。")
    else:
        observations.append('當日尚無新的已處理行情，沒有新增模擬成交；不把零交易解讀為策略無效。')
    if common:
        observations.append(f"共同觀察區間為 {common['first']} 至 {common['last']}，共 {common['days']} 個資料日；期間報酬只作描述，不據此宣布較佳策略。")
    else:
        observations.append('共同觀察區間尚不足兩個資料日，暫不比較不同開始日期的累積報酬。')
    observations.append(today_review[-1]['result'] if today_review else '本次未到策略檢查時點；目前規則保持不變，繼續累積新資料。')
    if any(a['unresolved'] for a in accounts):
        observations.append('有部位缺行情或量能不足；相關淨值標示待確認，不用舊估價宣告獲利。')
    return {'schemaVersion': 1, 'reportDate': report_date, 'marketDate': lab['lastMarketDate'],
            'labStatus': lab['status'], 'labMessage': lab.get('error') or lab.get('message'),
            'rulesVersion': lab['rules']['version'], 'rules': lab['rules'],
            'sources': [{k: s.get(k) for k in ('id', 'date', 'status')} for s in lab.get('sources', [])],
            'ledgerSha256': lab['stateSha256'], 'journalHead': lab['journal'][-1]['sha256'] if lab['journal'] else None,
            'currentRule': lab['currentRule'], 'currentRuleName': names[lab['currentRule']], 'accounts': accounts,
            'actions': actions, 'commonWindow': common, 'review': today_review[-1] if today_review else None,
            'observations': observations, 'limitations': LIMITATIONS, 'formalModelEligible': False}


def report_html(report):
    esc = lambda value: escape(str(value), quote=True)
    num = lambda value: '—' if value is None else f'{value:,.2f}'
    rows = ''.join('<tr>' + ''.join(f'<td>{esc(v)}</td>' for v in (
        a['name'], a['startedOn'], a['observations'], num(a['equity']), num(a['dailyChange']),
        num(a['returnPct']), num((report.get('commonWindow') or {}).get('returns', {}).get(a['id'])),
        num(a['maxDrawdownPct']), a['closed'], num(a['winRate']), num(a['stressPnl']), a['sampleStatus'])) + '</tr>' for a in report['accounts'])
    details = ''.join(f"<h3>{esc(a['name'])}</h3><p>{esc(a['description'])}</p><p>持倉 {a['positions']}，待處理委託 {a['pending']}，待確認部位 {a['unresolved']}。</p>" +
                      '<ul>' + ''.join(f'<li>{esc(reason)}：{n} 次</li>' for reason, n in a['failures'].items()) + '</ul>' for a in report['accounts'])
    names = {a['id']: a['name'] for a in report['accounts']}
    types = {'plan-buy':'新增買單','plan-sell':'排定賣出','buy':'買入成交','sell':'賣出成交','cancel-buy':'取消買單','unfilled-sell':'賣出未成'}
    actions = ''.join(f"<tr><td>{esc(names.get(a['account'], a['account']))}</td><td>{esc(types.get(a['type'], a['type']))}</td><td>{esc(a.get('code', ''))}</td><td>{esc(a.get('reason') or DESCRIPTIONS.get(a.get('strategy'), '依已排定委託'))}</td><td>{esc(num(a.get('pnl')))}</td></tr>" for a in report['actions'])
    head = ''.join(f'<th>{esc(h)}</th>' for h in ['帳戶／策略', '開始日', '觀察日數', '淨值', '當期淨值增減', '自啟用報酬%', '共同區間報酬%', '最大回撤%', '完成交易', '獲利比例%', '加倍成本損益', '樣本狀態'])
    return f'''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>權證策略觀察報告 {esc(report['id'])}</title>
<style>body{{font:16px/1.7 system-ui,sans-serif;margin:0;background:#0c1721;color:#dbe8ef}}main{{max-width:1200px;margin:auto;padding:24px}}h1,h2,h3{{color:#82dfcd}}section{{background:#15232e;padding:20px;border-radius:12px;margin:20px 0}}.scroll{{overflow:auto}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}th,td{{text-align:left;padding:10px;border-bottom:1px solid #354853}}small{{overflow-wrap:anywhere}}@media print{{body,section{{background:white;color:black}}h1,h2,h3{{color:black}}main{{padding:0}}.scroll{{overflow:visible}}table{{white-space:normal;font-size:10px}}}}</style>
<main><p>衍生視界 · 免費模擬試驗</p><h1>權證策略觀察報告</h1><p>報告 {esc(report['reportDate'])} · 版本 {esc(report['id'])} · 行情 {esc(report['marketDate'] or '尚無啟用後行情')}</p><p>{esc(report.get('labMessage') or '')}</p>
<section><h2>本次觀察</h2><ul>{''.join('<li>'+esc(t)+'</li>' for t in report['observations'])}</ul></section>
<section><h2>帳戶比較</h2><p>當期增減是本次新行情的帳戶淨值變化；無新行情時留空。自啟用報酬的起點不同，不能直接排序判定優劣。加倍成本損益僅含完成交易。</p><div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div></section>
<section><h2>策略與資料狀態</h2>{details}</section><section><h2>當日帳戶動作</h2><div class="scroll"><table><tr><th>帳戶</th><th>動作</th><th>權證</th><th>原因</th><th>平倉損益</th></tr>{actions or '<tr><td colspan="5">本次沒有新增帳戶動作。</td></tr>'}</table></div></section>
<section><h2>限制與核對</h2><p>各帳戶起始虛擬資金 {report['rules']['initialCash']:,} 元；每單預算 {report['rules']['budget']:,} 元。手續費假設 {report['rules']['feeRate']*100:g}%、最低 {report['rules']['minFee']} 元，賣出稅 {report['rules']['sellTax']*100:g}%；買賣各使用 {report['rules']['slippage']*100:g}% 不利滑價，整筆張數最多為成交量 {report['rules']['participation']*100:g}%。加倍成本損益只將已完成交易的手續費與滑價加倍，不是另一個完整資金帳戶。</p><p>來源狀態：{'；'.join(esc(s['id'])+' / '+esc(s['date'] or '缺日期')+' / '+esc(s['status']) for s in report['sources'])}。</p><ul>{''.join('<li>'+esc(t)+'</li>' for t in report['limitations'])}</ul><p>同日內容改變另存新版，舊版本保留。生成時間 {esc(report['generatedAt'])}；規則 {esc(report['rulesVersion'])}。</p><small>報告內容 SHA-256：{esc(report['contentSha256'])}<br>帳戶狀態 SHA-256：{esc(report['ledgerSha256'])}</small></section></main></html>'''


def collect_reports(output, now, today, read, save):
    lab = read(output / 'warrant-lab.json')
    if not lab or not lab.get('accounts'):
        raise ValueError('Paper ledger unavailable')
    validate(lab, legacy=lab['rules']['version'] == 'warrant-paper-v1')
    content = build_observation(lab, today.isoformat())
    content_sha = digest(content)
    path = output / 'warrant-lab-reports.json'
    index = read(path) or {'schemaVersion': 1, 'items': []}
    index.pop('checkedAt', None)
    existing = next((i for i in index['items'] if i['id'] == index.get('latest') and i['date'] == today.isoformat() and i['contentSha256'] == content_sha), None)
    if existing:
        # A retry never changes the already published observation content or its generation time.
        saved = read(output / 'warrant-lab-reports' / f"{existing['id']}.json")
        if not saved or digest({k: v for k, v in saved.items() if k not in ('id', 'generatedAt', 'contentSha256')}) != content_sha:
            raise ValueError('Saved observation integrity failure')
        save_text(output / 'warrant-lab-reports' / f"{existing['id']}.html", report_html(saved))
        index.update(latest=existing['id'], status='ok', error=None)
        save(path, index)
        return saved
    revision = sum(i['date'] == today.isoformat() for i in index['items']) + 1
    report_id = f'{today.isoformat()}-r{revision}-{content_sha[:8]}'
    report = {**content, 'id': report_id, 'generatedAt': now, 'contentSha256': content_sha}
    destination = output / 'warrant-lab-reports' / f'{report_id}.json'
    if destination.exists():
        saved = read(destination)
        if not saved or saved.get('contentSha256') != content_sha or digest({k: v for k, v in saved.items() if k not in ('id', 'generatedAt', 'contentSha256')}) != content_sha:
            raise ValueError('Refusing to overwrite an existing observation version')
        report = saved  # Recover an interrupted JSON/HTML write without changing the original timestamp.
    else:
        save(destination, report)
    html_path = destination.with_suffix('.html')
    save_text(html_path, report_html(report))
    index['items'].append({'id': report_id, 'date': today.isoformat(), 'generatedAt': report['generatedAt'],
                           'marketDate': lab['lastMarketDate'], 'status': lab['status'], 'contentSha256': content_sha,
                           'strategyCount': len(lab['rules']['strategies'])})
    index.update(latest=report_id, status='ok', error=None)
    save(path, index)
    return report
