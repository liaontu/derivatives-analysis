import { esc, fmt, finite } from './model.mjs';

export const validReportId = id => /^\d{4}-\d{2}-\d{2}-r\d+-[a-f0-9]{8}$/.test(id || '');
const number = (x, suffix = '') => finite(x) ? `${fmt(x, 2)}${suffix}` : '—';
const actionNames = { 'plan-buy': '新增買單', 'buy': '買入成交', 'plan-sell': '排定賣出', 'sell': '賣出成交', 'cancel-buy': '取消買單', 'unfilled-sell': '賣出未成' };

export function reportContent(index, report, selected, { table, tr, select, empty }) {
  const items = (index?.items || []).filter(i => validReportId(i.id));
  const labels = Object.fromEntries(items.map(i => [i.id, `${i.id} · 行情 ${i.marketDate || '等待新行情'}`]));
  labels[''] = '最新觀察報告';
  const picker = select('lab-report-id', '報告日期與版本', ['', ...items.slice().reverse().map(i => i.id)], selected || '', id => labels[id]);
  const header = `<section class="panel" id="lab-reports"><div class="panel-head"><div><span class="eyebrow">STRATEGY OBSERVATION REPORT</span><h2>策略觀察報告</h2><p class="muted">每天自動整理 · 同日變更保留版本 · 免費下載</p></div>${picker}</div>`;
  const warning = index?.status === 'retained' ? `<div class="notice warning">${esc(index.error || '本次報告未更新，以下保留舊版本。')}</div>` : '';
  if (!report || !validReportId(report.id)) return header + warning + empty('尚無可讀取的觀察報告；等待每日排程產生。') + '</section>';
  const rows = report.accounts.map(a => tr([`${esc(a.name)}<small>${esc(a.description)}</small>`, `${esc(a.startedOn)}<small>${a.observations} 個新行情日</small>`, number(a.equity), number(a.dailyChange), number(a.returnPct, '%'), number(report.commonWindow?.returns?.[a.id], '%'), number(a.maxDrawdownPct, '%'), `${a.closed} 筆 / ${number(a.winRate, '%')}`, number(a.stressPnl), esc(a.sampleStatus)]));
  const details = report.accounts.map(a => `<details><summary>${esc(a.name)} · 持倉 ${a.positions}／待處理 ${a.pending}／待確認 ${a.unresolved}</summary><p>${esc(a.description)}</p><p>當日動作：${Object.entries(a.counts).map(([type, count]) => `${esc(actionNames[type] || type)} ${count} 筆`).join('、') || '沒有新增動作'}</p><p>${Object.entries(a.failures).map(([reason, count]) => `${esc(reason)}：${count} 次`).join('；') || '沒有當日取消買單或賣出未成紀錄；不代表流動性已被證明。'}</p></details>`).join('');
  const common = report.commonWindow ? `${esc(report.commonWindow.first)}～${esc(report.commonWindow.last)}，${report.commonWindow.days} 個共同資料日` : '共同觀察期間不足，暫不作跨開始日期的績效比較';
  const base = `data/derivatives/warrant-lab-reports/${report.id}`;
  return header + warning + `<p>報告日 ${esc(report.reportDate)} · 行情 ${esc(report.marketDate || '尚無啟用後行情')} · ${esc(report.rulesVersion)}</p><p>${esc(report.labMessage || '')}</p><ul>${report.observations.map(t => `<li>${esc(t)}</li>`).join('')}</ul><p><a class="text-link" href="${base}.html" target="_blank" rel="noopener noreferrer">開啟完整報告 ↗</a> · <a href="${base}.html" download>下載 HTML 報告</a> · <a href="${base}.json" download>下載核對資料 JSON</a></p><p class="note">共同區間：${common}。自啟用報酬的起點可能不同，不能直接當成策略排名；當期增減在沒有新行情時留空。各帳戶交易可重疊，不能加總為獨立樣本。</p>${table(['策略', '開始日／觀察數', '淨值', '當期增減', '自啟用報酬', '共同區間報酬', '最大回撤', '完成交易／獲利比例', '加倍成本損益', '樣本狀態'], rows)}<h3>成交與未成交觀察</h3>${details}<details><summary>報告限制與核對紀錄</summary><ul>${report.limitations.map(t => `<li>${esc(t)}</li>`).join('')}</ul><p class="note">生成時間 ${esc(report.generatedAt)} · 內容 SHA-256：<code>${esc(report.contentSha256)}</code></p></details></section>`;
}
