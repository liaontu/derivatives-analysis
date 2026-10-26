import { reportContent, validReportId } from './lab-reports.mjs';
import { esc, fmt, finite, toCSV } from './model.mjs';

const number = (x, suffix = '') => finite(x) ? `${fmt(x, 2)}${suffix}` : '—';
export function accountStats(account, initial) {
  const last = account.curve.at(-1), equity = last ? last.equity : initial;
  let peak = initial, drawdown = 0;
  for (const p of account.curve) {
    if (!finite(p.equity)) { drawdown = null; break; }
    peak = Math.max(peak, p.equity);
    drawdown = Math.max(drawdown, (1 - p.equity / peak) * 100);
  }
  return { equity, returnPct: finite(equity) ? (equity / initial - 1) * 100 : null, drawdown,
    realized: account.trades.reduce((sum, t) => sum + t.pnl, 0),
    winRate: account.trades.length ? account.trades.filter(t => t.pnl > 0).length / account.trades.length * 100 : null };
}

export function equityChart(points) {
  const values = points.filter(p => finite(p.equity)).map(p => p.equity);
  if (values.length < 2) return '<div class="empty">開始後累積至少兩個有效資料日，才顯示帳戶淨值曲線。</div>';
  const low = Math.min(...values), high = Math.max(...values), range = high - low || Math.max(1, high * .02);
  const x = i => 60 + i / Math.max(1, points.length - 1) * 600, y = v => 175 - (v - low) / range * 135;
  let drawing = false;
  const path = points.map((p, i) => { if (!finite(p.equity)) { drawing = false; return ''; } const part = `${drawing ? 'L' : 'M'}${x(i)},${y(p.equity)}`; drawing = true; return part; }).join(' ');
  return `<div class="chart"><svg viewBox="0 0 700 220" role="img" aria-label="模擬帳戶淨值"><title>模擬帳戶淨值；缺值中斷</title>${[low, high].map(v => `<text x="55" y="${y(v)}" text-anchor="end">${fmt(v)}</text>`).join('')}<path d="${path}" stroke="#77e2d0" stroke-width="2.4" fill="none"/><text x="60" y="207">${esc(points[0].date)}</text><text x="660" y="207" text-anchor="end">${esc(points.at(-1).date)}</text></svg></div>`;
}

export function labContent(report, selected, { table, tr, select, metric, empty }) {
  if (!report?.accounts || !report.rules) return `<section class="panel">${empty(report?.error || '自動試驗尚未產生，等待每日更新。')}</section>`;
  const rules = report.rules, names = Object.fromEntries(rules.strategies.map(s => [s.id, s.name]));
  names.adaptive = '自動選用帳戶';
  const key = report.accounts[selected] ? selected : 'adaptive', account = report.accounts[key], stats = accountStats(account, rules.initialCash);
  const summary = `<section class="panel"><span class="eyebrow">FORWARD PAPER EXPERIMENT</span><h2>每天模擬，累積可檢查的交易紀錄</h2><p>啟用 ${esc(report.startedOn)} · 已記錄 ${fmt(report.dayCount)} 個新行情日 · 最近行情 ${esc(report.lastMarketDate || '等待啟用後的當日行情')}</p><p>${esc(report.message || '')}</p><p class="muted">來源行情日：${[...new Set((report.sources || []).map(s => s.date).filter(Boolean))].map(esc).join('、') || '尚無資料'} · 最近行情處理 ${esc(report.generatedAt ? new Date(report.generatedAt).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false }) : '尚無新行情')}（台北）</p><p class="note">僅上市一般權證；每個帳戶獨立使用 ${fmt(rules.initialCash)} 元虛擬資金，不加總成一個投資組合。${rules.strategies.length} 個固定候選持續並行，自動選用帳戶目前使用「${esc(names[report.currentRule])}」。歷史補抓與既有價差事件回測不算成這裡的模擬成交。</p></section>`;
  const warning = `<div class="notice warning">${report.status === 'retained' ? esc(report.error || '本次試驗暫停，以下保留原紀錄。') + ' ' : ''}研究用虛擬交易，沒有券商連線。成本、滑價與成交量限制是試驗假設；不能證明實際可成交或持續獲利。資料修訂不回寫已記錄的試驗結果；來源與紀錄雜湊可下載核對。</div>`;
  const metrics = `<div class="metrics">${metric('模擬帳戶淨值', number(stats.equity), '元 · 扣估計平倉成本；缺行情時不給確定值')}${metric('模擬報酬', number(stats.returnPct, '%'), '包含估計未實現損益，非真實績效')}${metric('最大回撤', number(stats.drawdown, '%'), '淨值曾自高點下降的比例；缺值時不推估')}${metric('已完成交易', fmt(account.trades.length), `持倉 ${account.positions.length} · 待處理委託 ${account.orders.length}`)}</div>`;
  const comparison = `<section class="panel"><h2>同時比較固定候選</h2>${table(['帳戶／規則', '開始日／觀察數', '淨值', '模擬報酬', '最大回撤', '已完成／獲利比例'], Object.entries(report.accounts).map(([id, a]) => { const s = accountStats(a, rules.initialCash); return tr([esc(names[id]), `${esc(report.accountStartedOn?.[id] || report.startedOn)}<small>${a.curve.length} 個新行情日</small>`, number(s.equity), number(s.returnPct, '%'), number(s.drawdown, '%'), `${a.trades.length} 筆 / ${number(s.winRate, '%')}`]); }))}<p class="note">沒有完成交易時不顯示勝率。各帳戶可能使用相同權證，結果並非獨立樣本。開始日期不同時，請以報告中的共同觀察區間比較；新候選不回填舊交易。金額滑價估算不模擬盤中委託簿、最小跳動價位或部分成交。</p></section>`;
  const detail = `<section class="panel"><div class="panel-head"><h2>查看帳戶紀錄</h2>${select('lab-account', '模擬帳戶', Object.keys(report.accounts), key, id => names[id])}</div>${equityChart(account.curve.slice(-120))}<p class="note">圖表顯示最近 120 個已記錄行情日，缺值處中斷；完整紀錄可下載。已實現損益 ${number(stats.realized)} 元；可用現金 ${number(account.cash)} 元。</p><h3>目前持倉</h3>${table(['權證／進場日', '張數／投入', '估價／資料日', '累積持有期／狀態'], account.positions.map(p => tr([`${esc(p.code)} ${esc(p.name)}<small>${esc(p.entryDate)} · ${esc(names[p.strategy])}</small>`, `${p.lots} 張 / ${number(p.cost)}`, `${number(p.lastPrice)} / ${esc(p.markDate)}`, `${p.periods} 期 · ${p.stale ? '缺行情或量能不足，估值待確認' : '已取得行情'}`])))}<h3>待處理模擬委託</h3>${table(['決策日', '權證／方向', '預算／原因'], account.orders.map(o => tr([esc(o.decisionDate), `${esc(o.code)} · ${o.side === 'buy' ? '買入' : '賣出持倉'}`, o.side === 'buy' ? `${number(o.budget)} 元 · ${esc(names[o.strategy])}` : esc(o.reason)])))}<h3>最近完成的交易</h3><button id="lab-export" ${account.trades.length ? '' : 'disabled'}>匯出交易 CSV</button>${table(['權證', '買入／賣出日', '張數', '模擬淨損益', '加倍成本壓力損益', '出場原因'], account.trades.slice(-30).reverse().map(t => tr([`${esc(t.code)} ${esc(t.name)}`, `${esc(t.entryDate)} / ${esc(t.exitDate)}`, fmt(t.lots), number(t.pnl), number(t.stressPnl), esc(t.reason)])))}</section>`;
  const reviews = `<section class="panel"><span class="eyebrow">CONTROLLED STRATEGY REVIEW</span><h2>自動檢查改版條件</h2><p>已累積 ${fmt(report.dayCount)} / 61 個新資料日。首次可比較 60 期變化後，每 20 期檢查一次；目前改版 ${report.reviews.filter(r => r.from !== r.to).length} 次。</p><ol><li>候選與目前規則的最近 61 個觀察日期必須完全相同，且最近 60 期至少 30 筆完成交易，後 20 期至少 10 筆，淨值不得缺漏。</li><li>前 40 期、後 20 期分別檢查：候選扣假設成本後報酬均須為正，且各高於目前規則至少 2 個百分點。</li><li>候選最大回撤不得較大；加倍手續費與滑價後，兩段已平倉損益均須為正且較好。</li><li>全部通過才切換後續新委託；已排定委託與持倉沿用原規則，未通過就維持。</li></ol><p class="note">這些是預先固定的研究門檻，尚未證明最佳。滾動區間與重複比較仍可能產生選擇偏差，不能稱為獨立樣本外驗證；沒有自動改程式、無限搜尋參數或付費 AI 呼叫，也不保證越改越好。</p>${table(['檢查日', '原規則 → 後續規則', '結果'], report.reviews.slice(-10).reverse().map(r => tr([esc(r.date), `${esc(names[r.from])} → ${esc(names[r.to])}`, esc(r.result)])))}</section>`;
  const method = `<section class="panel source-list"><h2>成交、成本與資料限制</h2><p>每筆最多 ${fmt(rules.budget)} 元、最多 ${rules.maxPositions} 個標的、每日最多 ${rules.newOrdersPerDay} 筆新買單；同一帳戶同一標的只持有一檔。至少成交 ${rules.minVolume} 張、價格 ${rules.minPrice} 元且距最後交易日 30 個日曆天才可新增。新單最快於下一個已取得行情日，以當日收盤加 1% 滑價估買入，賣出減 1%；委託超過 4 個日曆日、缺值、零成交或條款改變時取消買單。單筆張數不得超過當日成交量 1%，賣出不足則保留待處理。</p><p>假設每次手續費 0.1425%、最低 20 元，賣出交易稅 0.1%；手續費為研究設定，非你的券商費率。壓力測試把手續費與滑價加倍。量增候選只改量增門檻或持有期；另設價差候選，需 Z ≤ −2 且本檔換算價低於對照檔，僅為試驗條件，不代表公允低估。</p><p>持有滿 5／10 個有效觀察期、收盤觸及 −20%／＋30% 或距最後交易日 10 天時，排定後續行情日退出；沒有盤中停損成交假設。到期或流動性不足無法退出時，保留未解決持倉，不補零或假裝成交，不自動推估履約結算。缺漏日不補造，持有期不保證連續交易日；股價方向、時間耗損與發行人報價差異仍會影響權證。</p><p>每日免費排程與網站相同，只處理當日三項上市來源一致且成功的新行情；同日重跑不改寫已記錄帳戶。這是公開共用的研究帳戶，任何裝置看到同一份結果，沒有私人交易資料或真實下單。<a href="https://www.twse.com.tw/zh/products/securities/warrant/educate/qa.html" target="_blank" rel="noopener noreferrer">證交所權證交易與費用說明 ↗</a></p><a href="data/derivatives/warrant-lab.json" download>下載完整帳戶、規則與雜湊紀錄 JSON →</a></section>`;
  return warning + summary + metrics + comparison + detail + reviews + method;
}

export function createWarrantLab(deps) {
  let report, selected = 'adaptive', selectedReport = '';
  return {
    async page() {
      const loaded = await Promise.all([deps.load('warrant-lab').catch(() => null), deps.load('warrant-lab-reports').catch(() => null)]);
      report = loaded[0]; const index = loaded[1];
      const id = selectedReport || index?.latest;
      const observation = validReportId(id) && index?.items?.some(i => i.id === id) ? await deps.load(`warrant-lab-reports/${id}`).catch(() => null) : null;
      return reportContent(index, observation, selectedReport, deps) + labContent(report, selected, deps);
    },
    bind(render) {
      document.querySelector('#lab-report-id')?.addEventListener('change', e => { selectedReport = e.target.value; render(); });
      document.querySelector('#lab-account')?.addEventListener('change', e => { selected = e.target.value; render(); });
      document.querySelector('#lab-export')?.addEventListener('click', () => {
        const rows = report.accounts[selected].trades;
        const fields = [['code', '權證'], ['name', '名稱'], ['strategy', '規則'], ['entryDate', '進場日'], ['exitDate', '出場日'], ['lots', '張數'], ['cost', '含成本投入'], ['proceeds', '扣成本收入'], ['pnl', '模擬淨損益'], ['stressPnl', '加倍成本壓力損益'], ['reason', '出場原因']];
        const url = URL.createObjectURL(new Blob([toCSV(rows, fields)], { type: 'text/csv;charset=utf-8' }));
        const a = document.createElement('a'); a.href = url; a.download = `warrant-paper-${selected}.csv`; a.click(); URL.revokeObjectURL(url);
      });
    },
  };
}
