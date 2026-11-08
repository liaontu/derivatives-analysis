import {esc, fmt, finite} from './model.mjs';

const pct = value => finite(value) ? `${value > 0 ? '+' : ''}${fmt(value, 2)}%` : '—';
const tags = {volume:'成交量放大', spread:'Z 值異常', buy:'分點持續買超', sell:'分點持續賣超', 'to-buy':'分點轉買超', 'to-sell':'分點轉賣超'};
export const safeReportPath = path => /^(daily-reports\/warrant-journal\/revisions\/[\w-]+|warrant-lab-reports\/[\w-]+)\.json$/.test(path || '');
export const reportTime = value => value && Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat('zh-TW', {timeZone:'Asia/Taipei', dateStyle:'short', timeStyle:'short', hour12:false}).format(new Date(value)) + '（台灣）' : '—';

export function institutionalPanel(data,{table,tr}){
  if(!data?.leaders)return '';
  return (data.missingVolumeDates?.length?`<p class="notice warning">缺權證量價日報日期：${esc(data.missingVolumeDates.join('、'))}；該日不能建立放量確認事件。</p>`:'')+Object.entries(data.leaders).map(([market,rows])=>`<h4>${esc(market)} 法人累計觀察</h4><p>外資淨股數絕對值前十名，單位為股；缺日不補零，淨股數不是買賣金額。</p>${table(['標的','有效／缺日','外資淨股數','投信淨股數','自營避險淨股數'],rows.map(r=>tr([esc(r.code+' '+r.name),`${r.days}／${r.missingDays}`,fmt(r.foreign),fmt(r.trust),fmt(r.dealerHedge)])))}`).join('')+
    `<h4>本期到觀察日的法人訊號後五日價格變化</h4><p>僅計觀察日落在本期的事件；本期新訊號尚未到觀察日另計；價格未還原，不是交易損益。<a href="#stock-institutional?view=research">完整規則與 1／3／5 日回測 →</a></p>${table(['市場／規則','現股／權證有效','股／證價格變動中位數','等待／缺價／修訂'],(data.research||[]).map(r=>tr([esc(r.market+'／'+r.label),`${r.stockSamples}／${r.warrantSamples}`,`${pct(r.stockMedianPct)}／${pct(r.warrantMedianPct)}`,`${r.statuses.pending||0}／${r.statuses['missing-prices']||0}／${r.statuses['source-revised']||0}`])))}`;
}

export function evidencePanel(report, {table, tr}) {
  const q = report.quality;
  if (!q) return '<div class="notice warning">這是舊版封存報告，未保存逐項缺漏與有效樣本統計。請選擇最新版查看；不以目前資料回填舊版。</div>';
  const samples = q.samples.map(r => tr([esc(r.label), `${fmt(r.valid)}／${fmt(r.total)} ${esc(r.unit)}`, esc(r.basis)]));
  const missing = q.missing.map(r => tr([esc(r.label), `${fmt(r.count)} ${esc(r.unit)}`, esc(r.basis)]));
  const issues = Object.entries(q.branchIssues || {}).map(([k,v]) => esc(k) + ' ' + fmt(v)).join('；');
  const gaps = q.missing.filter(r => r.count > 0 && r.label !== '零成交').map(r => `${esc(r.label)} ${fmt(r.count)} ${esc(r.unit)}`).join('；');
  const imported = report.historyBackfill;
  const institutional=report.institutionalReview;
  const institutionalNote=institutional?`<p class="notice">本期現股法人 ${fmt(institutional.days)}／${fmt(institutional.expectedDays??institutional.days)} 個交易日、${fmt(institutional.stockDaySamples)} 個股日；同日股證可比較 ${fmt(institutional.pairedStockDaySamples)} 個股日。${esc(institutional.message)} ${institutional.missingDates?.length?'缺法人日報：'+esc(institutional.missingDates.join('、')):''} <a href="#stock-institutional">查看法人與權證觀察 →</a></p><details><summary>法人累計與本期事件回測明細</summary>${institutionalPanel(institutional,{table,tr})}</details>`:'';
  const dataReview=report.dataReview;
  const reviewPanel=dataReview?`<h4>本期補資料與可判讀範圍</h4>${table(['資料','實際覆蓋','限制／日期'],dataReview.rows.map(r=>tr(r.map(esc))))}<details><summary>影響價格比較的公司行動與限制</summary>${table(['生效日','股票','事件','官方參考價'],dataReview.capitalActions.map(r=>tr([esc(r.date),esc(r.code+' '+r.name),esc(r.action),finite(r.reference)?fmt(r.reference,2):'—'])))}<ul>${dataReview.notes.map(t=>'<li>'+esc(t)+'</li>').join('')}</ul></details><p><a href="#market-completion">逐檔核對與權證流動性資料 →</a></p>`:'';
  const importNote = reviewPanel+(imported ? `<div class="notice">上櫃官方歷史補入：<strong>${fmt(imported.days)} 個交易日、${fmt(imported.quoteRows)} 筆行情</strong>（含零成交），其中 ${fmt(imported.tradedRows)} 筆有有效成交價。<p>${esc(imported.basis)}</p><a href="data/derivatives/tpex-history-backfill.json" download>下載逐日來源、筆數與 SHA-256 →</a></div>` : '');
  const review = report.strategyReview;
  let strategy = '';
  if (review) {
    strategy = `<h3>本月策略檢查</h3><p>${esc(review.message)}</p>`;
    if (review.accounts.length) strategy += `<p class="muted">帳戶資料至 ${esc(review.asOf || '尚無行情')} · ${esc(review.rulesVersion || '')}。加倍成本損益只計完成交易。</p>` + table(['策略','開始日／觀察日數','完成交易／未解決部位','自啟用報酬／最大回撤','獲利比例／加倍成本損益（元）','樣本狀態'], review.accounts.map(a => tr([esc(a.name), `${esc(a.startedOn || '—')}／${fmt(a.observations)}`, `${fmt(a.closed)}／${fmt(a.unresolved)}`, `${pct(a.returnPct)}／${pct(a.maxDrawdownPct)}`, `${pct(a.winRate)}／${finite(a.stressPnl) ? fmt(a.stressPnl,2) : '—'}`, esc(a.sampleStatus || '待核對')])));
    if (safeReportPath(review.sourcePath)) strategy += `<p><a href="data/derivatives/${esc(review.sourcePath)}" download>下載期末策略證據 JSON</a></p>`;
  }
  const follow=report.followupReview;
  const tracking=follow ? `<h3>先前觀察清單後續</h3><p>${esc(follow.message)}</p><p class="muted">來源資料至 ${esc(follow.sourceAsOf || '尚無')} · ${esc(follow.sourceRules || '')}</p>${table(['來源模式／觀察期','本期到觀察日','權證／現股可比較','權證／現股漲跌中位數','狀態筆數'],follow.rows.map(r=>tr([`${esc(r.label)}／${r.horizon} 日`,fmt(r.total),`${fmt(r.warrantSamples)}／${fmt(r.stockSamples)}`,`${pct(r.warrantMedianPct)}／${pct(r.stockMedianPct)}`,Object.entries(r.statuses).map(([k,v])=>esc(k)+' '+fmt(v)).join('；')||'尚無樣本'])) )}` : '';
  return `<section class="report-evidence"><h3>資料範圍、缺漏與有效樣本數</h3>${importNote}${institutionalNote}<p>${esc(q.scope)}${q.universeDate ? ` 名錄檢查日 ${esc(q.universeDate)}。` : ''}</p>${gaps ? `<p class="notice warning">缺漏與比較限制：${gaps}。</p>` : ''}<h4>有效樣本（各項分開計算）</h4>${table(['統計項目','有效／候選樣本','計算條件'], samples)}<details><summary>展開缺漏與排除明細（${q.missing.length} 類）</summary>${table(['項目','數量','說明'], missing)}${issues ? `<p>分點核對：${issues}</p>` : ''}</details><ul>${q.notes.map(t => `<li>${esc(t)}</li>`).join('')}</ul>${tracking}${strategy}</section>`;
}

export function dailyPanel(report, {table, tr, metric}) {
  const s = report.summary;
  const cards = report.shortlist.map(r => `<article class="journal-card"><div class="panel-head"><div><h4>${esc(r.code)} ${esc(r.name)}</h4><a href="#warrants?underlying=${encodeURIComponent(r.underlying)}">${esc(r.underlying)} ${esc(r.underlyingName)} →</a></div><span class="tag">${r.cp === 'C' ? '認購' : '認售'}</span></div><p class="journal-tags">${r.tags.map(t => `<span class="tag">${esc(tags[t] || t)}</span>`).join('')}</p><dl class="journal-values"><div><dt>現股漲跌</dt><dd>${pct(r.stockPct)}</dd></div><div><dt>權證漲跌</dt><dd>${pct(r.warrantPct)}</dd></div><div><dt>前五期均量倍數</dt><dd>${finite(r.multiple) ? fmt(r.multiple,2) + ' 倍' : '—'}</dd></div></dl><p class="muted">比較基準 ${esc(r.previousDate || '缺資料')} → ${esc(report.period.end)} · 成交 ${fmt(r.volume)} 張</p><p>履約價 ${fmt(r.strike,2)} · 行使比例 ${fmt(r.ratio,4)} · ${esc(r.style || '方式缺值')}<br>到期 ${esc(r.expiry || '缺值')} · 剩餘 ${finite(r.remainingDays) ? r.remainingDays + ' 日曆天' : '缺值'}<br>最後交易日 ${esc(r.lastTrade || '缺資料')}</p><p class="note">分點：${r.branchAvailable ? '已核對當日部分排行' : '當日排行未核對，不視為零交易'}。${r.missingTerms?.length ? '缺條款：' + r.missingTerms.map(esc).join('、') : ''}</p></article>`).join('');
  const changes = report.changes.length ? table(['前期觀察權證','目前狀態'], report.changes.map(r => tr([esc(r.code + ' ' + r.name), esc(r.status)]))) : '<p class="muted">尚無可對照的前期重點清單。</p>';
  const source = report.sourceReport;
  return `<div class="metrics">${metric('行情資料日',esc(report.period.end),'資料日期與保存時間分開標示')}${metric('今日重點',fmt(report.shortlist.length),'最多 20 檔，每個標的最多 2 檔')}${metric('均量可比較',fmt(s.eligible),`候選 ${fmt(s.total)} 檔`)}${metric('同日分點',`${fmt(s.branchCovered)}／${fmt(s.branchRequested)}`,'個股追蹤清單，非全部分點')}</div><h3>每日重點摘要</h3><ul>${report.observations.map(t => `<li>${esc(t)}</li>`).join('')}</ul><h3>本日觀察清單</h3><div class="journal-grid">${cards || '<p>本期沒有符合門檻的權證。</p>'}</div>${evidencePanel(report, {table,tr})}<h3>前期清單後續</h3><p class="muted">前期資料日 ${esc(report.previousDate || '尚無')}；未再入榜可能涉及缺資料。</p>${changes}<p><a href="#warrant-report?date=${esc(report.period.end)}">查看完整日報與 1／3／5／10 日後續追蹤 →</a></p><details><summary>來源、方法與限制</summary><ul>${report.limitations.map(t => `<li>${esc(t)}</li>`).join('')}</ul>${safeReportPath(source.path) ? `<p><a href="data/derivatives/${esc(source.path)}" download>下載本版來源封存 JSON</a></p>` : ''}<p>來源保存時間 ${esc(reportTime(source.recordedAt))}</p><p class="note">來源 SHA-256：<code>${esc(source.sha256)}</code><br>報告 SHA-256：<code>${esc(report.contentSha256)}</code></p></details>`;
}
