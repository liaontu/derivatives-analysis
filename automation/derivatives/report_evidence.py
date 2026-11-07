"""Small, reproducible report summaries from saved evidence; no network or model calls."""
from collections import Counter
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from html import escape
import json
import math
from statistics import median


def read(path):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def sample(label, valid, total, unit, basis):
    return {'label': label, 'valid': valid, 'total': total, 'unit': unit, 'basis': basis}


def history_backfill(output, start=None, end=None):
    ledger = read(output / 'tpex-history-backfill.json')
    days = [r for r in ledger.get('days', []) if r.get('status') in ('imported', 'partial')
            and (not start or r['date'] >= start) and (not end or r['date'] <= end)]
    if not days:
        return None
    return {'market': 'TPEX', 'dates': [r['date'] for r in days], 'days': len(days),
            'quoteRows': sum(r['counts'].get('acceptedQuoteRows', 0) for r in days),
            'tradedRows': sum(r['counts'].get('tradedQuoteRows', 0) for r in days),
            'sourcePath': 'tpex-history-backfill.json', 'status': ledger.get('status'),
            'basis': '櫃買中心免費歷史行情：權證成交價、成交量、同日標的收盤。資料是事後補入；歷史履約條款與完整分點仍未取得，不視為當時已知訊號。'}


def institutional_review(output, start, end):
    index=read(output/'stock-institutional.json')
    if not index:return None
    try:
        from .institutional_research import period_review
    except ImportError:
        from institutional_research import period_review
    return period_review(output,start,end)


def daily_report(output):
    folder = output / 'daily-reports/warrant-journal'
    index = read(folder / 'index.json')
    if index.get('status') != 'ok' or not index.get('dates'):
        raise ValueError('整合日報來源尚未完成；保留前次報告')
    day = max(index['dates'])
    report = read(folder / (day + '.json'))
    if report.get('date') != day or report.get('status') not in ('ok', 'partial'):
        raise ValueError('整合日報日期或狀態未通過核對')
    source_path = folder / 'revisions' / (day + '-' + report['revision'] + '.json')
    if read(source_path) != report:
        raise ValueError('整合日報與封存版本不一致')
    s, spread = report['summary'], report['spreadCoverage']
    shortlist = report['shortlist'][:20]
    aligned = sum(finite(r.get('warrantPct')) and finite(r.get('stockPct')) for r in shortlist)
    quality = {
        'scope': '上市櫃普通股標的權證；分點僅限追蹤清單內已取得的買賣超排行。',
        'samples': [sample('五期均量可比較', s['eligible'], s['total'], '檔', '當日有效成交且有前五交易日成交量基準'),
                    sample('同日分點已核對', s['branchCovered'], s['branchRequested'], '檔', '分母為本報告個股追蹤清單；不是全部權證或全部分點'),
                    sample('價差 Z 值可用', spread['zReady'], spread['paired'], '檔', '分母為有配對的權證；0 筆可用不代表沒有異常'),
                    sample('重點清單股證同期間漲跌', aligned, len(shortlist), '檔', '權證與現股使用相同兩個日期；缺值不補零')],
        'missing': [{'label': k, 'count': v, 'unit': '檔', 'basis': '量價分析未納入原因'} for k, v in report['volumeExcluded'].items()],
        'notes': ['零成交、缺值與不符合條件分開列示；各項有效樣本不可相加。',
                  '當期沒有可用 Z 值時，清單仍可呈現量增與已核對的分點線索。'],
        'branchIssues': report['branchIssues'],
    }
    observations = [
        f"資料日 {day}：{s['total']:,} 檔個股權證中，{s['eligible']:,} 檔可比較前五期均量，{s['volumeHits']:,} 檔達放量門檻。",
        f"日報列出 {len(shortlist)} 檔重點，每個標的最多 2 檔；依訊號種類、量增倍數與成交量篩選，不是獲利排名。",
        f"同日分點核對 {s['branchCovered']}／{s['branchRequested']} 檔追蹤權證；未收錄或未入榜均不當作零交易。",
        f"價差配對涵蓋 {spread['paired']} 檔，其中 {spread['zReady']} 檔有可用 Z 值。",
    ]
    return {
        'schemaVersion': 2, 'kind': 'daily', 'period': {'start': day, 'end': day},
        'rules': {'version': 'warrant-daily-brief-v1', 'shortlistSize': 20, 'sourceRules': report['rules']},
        'market': {'dataDays': [day], 'tradingDays': [day], 'missingDays': []},
        'quality': quality, 'status': report['status'], 'summary': s, 'shortlist': shortlist,
        'changes': report['changes'][:20], 'previousDate': report['previousDate'],
        'sources': report['sources'], 'sourceReport': {
            'path': source_path.relative_to(output).as_posix(),
            'sha256': sha256(source_path.read_bytes()).hexdigest(), 'recordedAt': report['recordedAt']},
        'observations': observations, 'limitations': report['limits'] + [
            '生成時間與行情日期分開顯示；休市或沒有新資料不產生新的行情日。',
            '日報為固定規則自動整理，不呼叫付費 AI。分點補齊會另存同日版本。'],
        'institutionalReview': institutional_review(output,day,day),
    }


def period_quality(report, universe):
    m = report['market']
    coverage = Counter()
    market_days = {'TWSE': set(), 'TPEX': set()}
    for u in report['underlyings']:
        coverage.update(u.get('coverage', {}).get('counts', {}))
        for market, days in u.get('coverage', {}).get('marketDays', {}).items():
            market_days.setdefault(market, set()).update(days)
    samples = [sample('現股期間漲跌', m['underlyingReturn']['count'], m['underlyings'], '個標的', '有效起訖收盤；期間內起算另外標示')]
    for key, label in [('call', '認購'), ('put', '認售')]:
        s = m[key]
        samples += [sample(label + '期間漲跌', s['returns'], s['liquid'], '檔', '常交易權證且有期間前成交基準'),
                    sample(label + '同期間方向比較', s['consistentSamples'], s['liquid'], '檔', '權證與現股相同起訖日；持平、缺價不納入')]
        if report['kind'] == 'monthly':
            samples.append(sample(label + '斜率／相關', s.get('slopes', 0), s['liquid'], '檔', f"至少 {report['rules']['minPairs']} 個配對報酬區段且有變異；不是獨立交易樣本"))
    missing = [
        {'label': '全體未收錄交易日', 'count': len(m['missingDays']), 'unit': '日', 'basis': '、'.join(m['missingDays']) or '期間內每個預定交易日均至少有部分紀錄'},
        {'label': '缺現股價格', 'count': coverage['missingSpotDays'], 'unit': '個股日', 'basis': '該股有權證紀錄的日期缺現股收盤'},
        {'label': '成交量缺值', 'count': coverage['missingVolumeRows'], 'unit': '權證日', 'basis': '沒有可用成交量，不填零'},
        {'label': '零成交', 'count': coverage['zeroVolumeRows'], 'unit': '權證日', 'basis': '已知零成交；不當成零價格'},
        {'label': '有量但缺成交價', 'count': coverage['missingTradedPriceRows'], 'unit': '權證日', 'basis': '成交量大於零但沒有正值收盤'},
        {'label': '現股期間內起算', 'count': sum(u['baseDate'] is not None and not u['baseBeforePeriod'] for u in report['underlyings']), 'unit': '個標的', 'basis': '缺期間前基準，漲跌僅涵蓋部分期間'},
    ]
    for market, label in [('TWSE', '上市'), ('TPEX', '上櫃')]:
        absent = [d for d in m['tradingDays'] if d not in market_days[market]]
        missing.append({'label': label + '未收錄交易日', 'count': len(absent), 'unit': '日',
                        'basis': '、'.join(absent) or '每日均有部分紀錄；不代表每檔完整'})
    return {'scope': '依本次官方個股名錄篩選已保存的上市櫃普通股標的；不含 ETF／指數。',
            'universeDate': universe.get('checkedDay'), 'samples': samples, 'missing': missing,
            'notes': ['每個日期有紀錄不表示每檔權證、每個欄位或兩個市場完整；以下缺漏按實際保存紀錄計數。',
                      '名錄為本次可得名單，無法據此證明完整歷史全市場；不同期間的商品組成可能改變。',
                      '歷史欄位有限，未還原除權息與完整履約條款；同一標的權證共用行情，不是獨立樣本。']}


def strategy_review(output, end):
    """Only use an account report actually archived by the period end. Never leak later results."""
    index = read(output / 'warrant-lab-reports.json')
    eligible = []
    for item in index.get('items', []):
        try:
            saved_day = datetime.fromisoformat(item['generatedAt']).astimezone(timezone(timedelta(hours=8))).date().isoformat()
        except (KeyError, ValueError):
            continue
        if saved_day <= end and item.get('date', '9999') <= end:
            eligible.append(item)
    if not eligible:
        return {'status': 'insufficient', 'asOf': None, 'accounts': [],
                'message': '截至本期末尚無可核對的模擬交易報告；不使用期末之後才產生的績效，也不評選最佳策略。'}
    item = max(eligible, key=lambda i: (i['date'], i['generatedAt']))
    if '/' in item['id'] or '\\' in item['id'] or '..' in item['id']:
        raise ValueError('Invalid strategy report id')
    path = output / 'warrant-lab-reports' / (item['id'] + '.json')
    saved = read(path)
    if not saved or (saved.get('marketDate') and saved['marketDate'] > end):
        raise ValueError('策略月結來源缺檔或超過期末')
    fields = ('name', 'startedOn', 'asOf', 'observations', 'closed', 'returnPct', 'maxDrawdownPct', 'winRate', 'stressPnl', 'unresolved', 'sampleStatus')
    return {'status': 'available', 'asOf': saved.get('marketDate'), 'rulesVersion': saved.get('rulesVersion'),
            'sourcePath': path.relative_to(output).as_posix(), 'sha256': sha256(path.read_bytes()).hexdigest(),
            'accounts': [{k: a.get(k) for k in fields} for a in saved.get('accounts', [])],
            'message': '截至期末已保存的模擬帳戶快照；報酬起點可能不同，不跨版本混算。成本與滑價為假設，樣本不足只記錄，不改策略。'}


def followup_review(output, start, end):
    path = output / 'daily-reports/warrant-journal/outcomes.json'
    data = read(path)
    rows = []
    for mode, label in [('recorded', '啟用後封存觀察'), ('research', '後補歷史放量研究')]:
        for horizon in (1, 3, 5, 10):
            selected = []
            for e in data.get('items', []):
                if e.get('mode') != mode or e.get('signalDate', '9999') >= start:
                    continue
                if mode == 'recorded':
                    saved = datetime.fromisoformat(e['recordedAt']).astimezone(timezone(timedelta(hours=8))).date().isoformat()
                    if saved > end:
                        continue
                result = next((r for r in e['results'] if r['horizon'] == horizon), None)
                if result and result.get('date') and start <= result['date'] <= end:
                    selected.append(result)
            warrants = [r['pct'] for r in selected if finite(r.get('pct'))]
            stocks = [r['stockPct'] for r in selected if finite(r.get('stockPct'))]
            rows.append({'mode': mode, 'label': label, 'horizon': horizon, 'total': len(selected),
                         'warrantSamples': len(warrants), 'stockSamples': len(stocks),
                         'warrantMedianPct': median(warrants) if warrants else None,
                         'stockMedianPct': median(stocks) if stocks else None,
                         'statuses': dict(Counter(r['status'] for r in selected))})
    return {'sourceAsOf': data.get('asOf'), 'sourceRules': data.get('rules', {}).get('version'), 'rows': rows,
            'message': '只看本期開始前的線索，且觀察日落在本期內；1／3／5／10 日分開計數。封存觀察與後補研究分開，價格變動未扣成本，不是交易勝率。來源可能後續修訂，這份統計不是當時可用的樣本外績效。'}


def quality_html(report):
    q = report.get('quality')
    if not q:
        return '<p>舊版報告未保存逐項有效樣本與欄位缺漏統計；不由目前資料回填舊版。</p>'
    esc = lambda v: escape(str(v), quote=True)
    samples = ''.join(f"<tr><td>{esc(r['label'])}</td><td>{r['valid']:,}／{r['total']:,} {esc(r['unit'])}</td><td>{esc(r['basis'])}</td></tr>" for r in q['samples'])
    missing = ''.join(f"<tr><td>{esc(r['label'])}</td><td>{r['count']:,} {esc(r['unit'])}</td><td>{esc(r['basis'])}</td></tr>" for r in q['missing'])
    issues = '；'.join(f'{k} {v}' for k, v in q.get('branchIssues', {}).items())
    review = report.get('strategyReview')
    imported = report.get('historyBackfill')
    institutional = report.get('institutionalReview')
    institution_note = (f'<p>本期現股法人：{institutional["days"]}／{institutional.get("expectedDays", institutional["days"])} 個交易日、{institutional["stockDaySamples"]:,} 個股日；其中 {institutional["pairedStockDaySamples"]:,} 個股日可與同日股證量價比較。{esc(institutional["message"])}</p>' if institutional else '')
    if institutional and institutional.get('missingDates'):institution_note+='<p>缺法人日報：'+esc('、'.join(institutional['missingDates']))+'</p>'
    if institutional and institutional.get('leaders'):
        if institutional.get('missingVolumeDates'):institution_note+='<p>缺權證量價日報日期：'+esc('、'.join(institutional['missingVolumeDates']))+'；該日不能建立放量確認事件。</p>'
        for market,items in institutional['leaders'].items():
            institution_note+=f'<h3>{esc(market)} 法人累計觀察（外資淨股數絕對值前十名）</h3><div class="scroll"><table><tr><th>標的</th><th>有效／缺日</th><th>外資淨股數</th><th>投信淨股數</th><th>自營避險淨股數</th></tr>'+''.join(f'<tr><td>{esc(r["code"]+" "+r["name"])}</td><td>{r["days"]}／{r["missingDays"]}</td><td>{r["foreign"]:,}</td><td>{r["trust"]:,}</td><td>{r["dealerHedge"]:,}</td></tr>' for r in items)+'</table></div>'
        institution_note+='<h3>本期到觀察日的法人訊號後五日價格變化</h3><p>只計觀察日落在本期的事件；本期新訊號尚未到觀察日另計；價格未還原，不是交易損益。</p><div class="scroll"><table><tr><th>市場／規則</th><th>現股／權證有效樣本</th><th>價格變動中位數%（股／證）</th><th>等待／其他狀態</th></tr>'+''.join(f'<tr><td>{esc(r["market"]+"／"+r["label"])}</td><td>{r["stockSamples"]}／{r["warrantSamples"]}</td><td>{esc(round(r["stockMedianPct"],2) if r["stockMedianPct"] is not None else "—")}／{esc(round(r["warrantMedianPct"],2) if r["warrantMedianPct"] is not None else "—")}</td><td>{esc(str(r["statuses"]))}</td></tr>' for r in institutional.get('research',[]))+'</table></div>'
    import_note = (f'<p>上櫃官方歷史補入：{imported["days"]} 個資料日、{imported["quoteRows"]:,} 筆行情（含零成交），其中 {imported["tradedRows"]:,} 筆有效成交。{esc(imported["basis"])}</p>' if imported else '') + institution_note
    strategy = '<h3>本月策略檢查</h3><p>' + esc(review['message']) + '</p>' if review else ''
    if review and review.get('accounts'):
        strategy += '<div class="scroll"><table><tr><th>策略</th><th>完成交易</th><th>自啟用報酬%</th><th>最大回撤%</th><th>樣本狀態</th></tr>' + ''.join(
            '<tr>' + ''.join('<td>' + esc(a.get(k) if a.get(k) is not None else '—') + '</td>' for k in ('name', 'closed', 'returnPct', 'maxDrawdownPct', 'sampleStatus')) + '</tr>' for a in review['accounts']) + '</table></div>'
    follow = report.get('followupReview')
    tracking = ''
    if follow:
        tracking = '<h3>先前觀察清單後續</h3><p>' + esc(follow['message']) + '</p><div class="scroll"><table><tr><th>來源模式／觀察期</th><th>到期觀察</th><th>權證／現股可比較</th><th>權證／現股漲跌中位數%</th></tr>' + ''.join(
            f'<tr><td>{esc(r["label"])}／{r["horizon"]} 日</td><td>{r["total"]}</td><td>{r["warrantSamples"]}／{r["stockSamples"]}</td><td>{esc(round(r["warrantMedianPct"],2) if r["warrantMedianPct"] is not None else "—")}／{esc(round(r["stockMedianPct"],2) if r["stockMedianPct"] is not None else "—")}</td></tr>' for r in follow['rows']) + '</table></div>'
    return f'<section><h2>資料範圍、缺漏與有效樣本數</h2>{import_note}<p>{esc(q["scope"])}</p><h3>有效樣本（各項分開計算）</h3><div class="scroll"><table><tr><th>統計</th><th>有效／候選</th><th>定義</th></tr>{samples}</table></div><h3>缺漏與排除</h3><div class="scroll"><table><tr><th>項目</th><th>數量</th><th>說明</th></tr>{missing}</table></div><p>{esc(issues)}</p><ul>' + ''.join('<li>' + esc(t) + '</li>' for t in q['notes']) + f'</ul>{tracking}{strategy}</section>'


def daily_html(report):
    esc = lambda v: escape(str(v), quote=True)
    num = lambda v: f'{v:,.2f}' if finite(v) else '—'
    rows = ''.join('<tr>' + ''.join(f'<td>{v}</td>' for v in [esc(r['code'] + ' ' + r['name']), esc(r['underlyingName']), num(r.get('stockPct')), num(r.get('warrantPct')), num(r.get('multiple')), num(r.get('volume')), esc(r.get('expiry') or '缺資料'), esc(r.get('style') or '缺資料')]) + '</tr>' for r in report['shortlist'])
    return '<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>權證與現股日報 ' + esc(report['period']['end']) + '</title><style>:root{color-scheme:light}body{font:16px/1.7 system-ui,sans-serif;background:#f4f7f2;color:#1f332e;margin:0}main{max-width:1100px;margin:auto;padding:24px}section{background:white;padding:20px;margin:20px 0;border-radius:12px}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;white-space:nowrap}th,td{text-align:left;padding:10px;border-bottom:1px solid #ddd}small{overflow-wrap:anywhere}@media(max-width:600px){main{padding:12px}section{padding:14px}}@media print{.scroll{overflow:visible}table{white-space:normal;font-size:10px}}</style><main><h1>權證與現股日報</h1><p>行情日期 ' + esc(report['period']['end']) + ' · 保存時間 ' + esc(report['generatedAt']) + '</p><section><h2>每日重點</h2><ul>' + ''.join('<li>' + esc(x) + '</li>' for x in report['observations']) + '</ul></section>' + quality_html(report) + '<section><h2>重點清單（最多 20 檔）</h2><div class="scroll"><table><tr><th>權證</th><th>現股</th><th>現股漲跌%</th><th>權證漲跌%</th><th>均量倍數</th><th>成交張數</th><th>到期日</th><th>履約方式</th></tr>' + rows + '</table></div></section><section><h2>方法與來源</h2><ul>' + ''.join('<li>' + esc(x) + '</li>' for x in report['limitations']) + '</ul><small>來源封存：' + esc(report['sourceReport']['path']) + '<br>來源 SHA-256：' + esc(report['sourceReport']['sha256']) + '<br>內容 SHA-256：' + esc(report['contentSha256']) + '</small></section></main></html>'
