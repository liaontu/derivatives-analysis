import { esc, fmt, finite, warrantCp, daysUntil, toCSV, taipeiToday } from './model.mjs';

export const focusLabels = { all: '全部觀察項目', both: '價差異常＋量增', 'spread-anomaly': '價差異常（Z 值）', 'volume-surge': '成交量明顯增加', 'wide-spread': '價差較大（未達異常判定）' };
export function filterFocus(rows, kind = 'all', search = '') {
  const query = search.trim().toLowerCase();
  return rows.filter(r => (kind === 'all' || (kind === 'both' ? r.tags.includes('spread-anomaly') && r.tags.includes('volume-surge') : r.tags.includes(kind))) && (!query || [r.code, r.name, r.underlying, r.underlyingName].join(' ').toLowerCase().includes(query)));
}
const number = (n, unit = '', digits = 2) => finite(n) ? `${fmt(n, digits)}${unit}` : '—';
const signed = n => finite(n) ? `<span class="${n > 0 ? 'positive' : n < 0 ? 'negative' : ''}">${n > 0 ? '+' : ''}${fmt(n, 2)}%</span>` : '—';
const sourceDates = report => [...new Set((report.sources || []).map(s => s.date).filter(Boolean))].map(esc).join('、');
export function focusReasons(row) {
  const v = row.volumeEvidence || {}, p = row.spreadEvidence;
  const reasons = [];
  if (row.tags.includes('spread-anomaly')) reasons.push(`價差異常：|Z| ${number(Math.abs(p.z))} ≥ 2`);
  if (row.tags.includes('volume-surge')) reasons.push(`量增：前五期均量 ${number(v.multiple, ' 倍')}，當期 ${number(row.volume, ' 張')}`);
  if (row.tags.includes('wide-spread')) reasons.push(`價差較大：${number(Math.abs(p.spread), '%')}；${finite(p.z) ? '|Z| 未達 2' : '歷史／變異不足，未判定異常'}`);
  return reasons;
}
export function focusExport(rows) {
  return rows.map(r => ({ ...r, reason: focusReasons(r).join('；'), previousDate: r.previousDate,
    average: r.volumeEvidence.average, multiple: r.volumeEvidence.multiple, volumeDelta: r.volumeEvidence.delta,
    baselineDates: (r.volumeEvidence.baselineDates || []).join('、'), volumeStatus: r.volumeEvidence.status,
    peer: r.spreadEvidence?.peerCode, pair: r.spreadEvidence?.pairId, spread: r.spreadEvidence?.spread, z: r.spreadEvidence?.z,
    zStatus: r.spreadEvidence?.zStatus, priorCount: r.spreadEvidence?.priorCount,
    trainingEndDate: r.spreadEvidence?.trainingEndDate, rule: 'warrant-focus-v1', limit: '固定研究門檻；非套利報酬；成本與可成交性未評估' }));
}
const fields = [['date', '行情日'], ['code', '權證代碼'], ['name', '權證名稱'], ['underlying', '標的代碼'], ['underlyingName', '標的名稱'], ['reason', '入選原因'], ['price', '權證收盤元'], ['pricePct', '較前筆漲跌%'], ['previousDate', '價格比較日'], ['underlyingPrice', '同日標的價格'], ['underlyingPct', '同起訖標的漲跌%'], ['volume', '成交量張'], ['average', '前五期均量張'], ['multiple', '均量倍數'], ['volumeDelta', '較均量增減張'], ['baselineDates', '均量基準日'], ['volumeStatus', '量能比較狀態'], ['peer', '價差對照權證'], ['spread', '本檔相對價差%'], ['z', '本檔相對Z'], ['zStatus', 'Z值狀態'], ['priorCount', '價差前期有效數'], ['trainingEndDate', '價差基準截止'], ['pair', '配對識別'], ['strike', '履約價'], ['expiry', '到期日'], ['lastTrade', '最後交易日'], ['style', '履約方式'], ['rule', '規則版本'], ['limit', '限制']];

export function focusContent(report, state, { table, tr, select, metric, empty }) {
  if (!report?.rules || !report?.summary) return `<section class="panel">${empty('每日重點清單尚未產生或本次無法載入。請稍後載入最新資料。')}<a class="text-link" href="#warrant-daily">先查看個股權證觀察表 →</a></section>`;
  const r = report.rules, summary = report.summary, matched = filterFocus(report.items, state.kind, state.search);
  const rows = state.limit === 'all' ? matched : matched.slice(0, 50);
  state.page = Math.max(0, Math.min(state.page, Math.ceil(rows.length / 20) - 1));
  const stale = (report.sources || []).some(s => s.date && daysUntil(s.date) < -r.maxSourceAgeDays);
  const notice = report.status !== 'ok' || stale ? `<div class="notice warning">${esc(report.status === 'retained' ? report.error || '本次計算失敗，以下是上次成功清單。' : '部分來源更新不足或資料較舊；只使用通過檢查的資料，清單可能不完整。')} 來源行情日 ${sourceDates(report) || '尚無'}；清單只隨來源資料變動更新。</div>` : '';
  const header = `<section class="panel focus-intro"><div class="panel-head"><div><span class="eyebrow">DAILY RESEARCH SHORTLIST</span><h2>每天自動掃描，留下有依據的觀察項目</h2><p class="muted">${sourceDates(report) || '尚無行情日'} 行情 · 清單隨來源資料變動更新</p></div><a class="text-link" href="#quality">資料品質 →</a></div><div class="focus-rule-grid"><div><strong>價差異常</strong><p>|Z| ≥ ${r.zThreshold}；以前 ${20} 個完整配對日計算。</p></div><div><strong>成交量明顯增加</strong><p>至少 ${r.minVolume} 張，且達前 ${r.volumeWindow} 期均量 ${r.volumeMultiple} 倍。</p></div><div><strong>較大價差另列觀察</strong><p>絕對價差 ≥ ${r.wideSpreadThreshold}%；歷史不足或未達 Z 門檻，不列異常。</p></div></div><p class="note">掃描官方清單 ${fmt(summary.total)} 檔，當期通過基本條件 ${fmt(summary.eligible)} 檔。這是固定規則的研究清單；沒有使用付費行情或每日 AI 呼叫。大價差與量增不等於可成交套利。</p></section>`;
  const history = report.historyCoverage;
  const historical = history?.extendedDays ? `<section class="panel"><h2>歷史補抓已納入每日掃描</h2><p>上市歷史匯入日期 ${esc(history.first)} 至 ${esc(history.last)}，共 ${fmt(history.days)} 個資料日；其中 ${fmt(history.extendedDays)} 日只涵蓋有生效公告且核對通過的部分權證。Z 值仍須前 20 個完整配對日，資料不足不計算。</p><p class="note">較早區段存在存續樣本選擇偏差；不是全市場歷史，也不是即時樣本外績效。<a href="#warrant-spreads">查看回測、涵蓋範圍與來源 →</a></p></section>` : '';
  const zReady = report.coverage?.spread?.['可計算 Z'] || 0;
  const metrics = `<div class="metrics">${metric('價差異常＋量增', zReady ? fmt(summary.both) : '—', zReady ? '同一檔同日同時符合兩項' : 'Z 值資料不足，尚無法判定')}${metric('價差異常', zReady ? fmt(summary.anomalies) : '—', zReady ? `具有效 Z 的權證 ${fmt(zReady)} 檔` : '至少需要 21 個完整配對日')}${metric('成交量明顯增加', fmt(summary.volumeSurges), '含同時價差異常的權證')}${metric('較大價差待觀察', fmt(summary.wideOnly), '未達異常判定，可能同時量增')}</div>`;
  const controls = `<section class="panel"><div class="filters"><label class="search">權證或標的代碼／名稱<input id="wf-search" type="search" value="${esc(state.search)}" placeholder="例如 2330、台積電、權證代碼"></label>${select('wf-kind', '觀察類別', Object.keys(focusLabels), state.kind, key => focusLabels[key])}${select('wf-limit', '清單範圍', ['50', 'all'], state.limit || '50', key => key === '50' ? '重點前 50 檔' : '全部符合項目')}<button id="wf-apply">篩選觀察項目</button></div><p class="muted">先列「價差異常＋量增」，再列價差異常、量增與較大價差。量增類優先按較均量增加張數排列，再比較倍數；各類可重疊，每檔權證只列一次。這是顯示順序，不是投資評分。</p></section>`;
  const list = `<section class="panel focus-list"><div class="panel-head"><div><h2>每日重點觀察清單</h2><p class="muted">${esc(focusLabels[state.kind])} · ${fmt(rows.length)} / ${fmt(matched.length)} 檔 · ${state.limit === 'all' ? '全部符合' : '目前篩選的重點前 50 檔'} · 按來源日期更新</p></div><button id="wf-export" ${rows.length ? '' : 'disabled'}>匯出重點清單 CSV</button></div>${rows.length ? table(['權證 / 標的', '入選原因', '收盤 / 漲跌', '量能證據', '同條件價差 / Z', '到期 / 資料狀態'], rows.slice(state.page * 20, (state.page + 1) * 20).map(item => {
    const v = item.volumeEvidence, p = item.spreadEvidence;
    return tr([
      `<a class="text-link" href="#daily-watch?code=${encodeURIComponent(item.code)}">${esc(item.code)} · ${esc(item.name)}</a><small>${warrantCp(item.cp)} · ${esc(item.market)} · ${esc(item.date)}</small><a class="text-link" href="#warrant-daily?underlying=${encodeURIComponent(item.underlying)}"><small>標的 ${esc(item.underlying)} ${esc(item.underlyingName)} →</small></a>`,
      focusReasons(item).map(text => `<span class="focus-reason">${esc(text)}</span>`).join(''),
      `${number(item.price, ' 元', 3)}<small>${signed(item.pricePct)} · 較 ${esc(item.previousDate || '尚無前筆')}</small><small>標的 ${number(item.underlyingPrice, '', 3)} · ${signed(item.underlyingPct)}</small>`,
      `${number(item.volume, ' 張', 3)}<small>前五期均量 ${number(v.average, ' 張', 3)}</small><strong>${number(v.multiple, ' 倍')}</strong><details><summary>基準與缺項</summary><p>${esc(v.status)}</p><p>${esc((v.baselineDates || []).join('、') || '尚無基準日期')}</p></details>`,
      p ? `<a class="text-link" href="#warrant-spreads?underlying=${encodeURIComponent(item.underlying)}&pair=${encodeURIComponent(p.pairId)}">${signed(p.spread)} · 對 ${esc(p.peerCode)}</a><small>Z ${number(p.z)} · 前期 ${fmt(p.priorCount)} / 20</small><small>${esc(p.zStatus || '未記錄')}</small>` : '—<small>無有效配對／價差更新不足</small>',
      `${esc(item.expiry)}<small>距今日 ${fmt(daysUntil(item.expiry))} 日曆天 · ${esc(item.style)}</small><small>最後交易 ${esc(item.lastTrade || '未提供')}</small><small>${!item.previousDate ? '價格歷史不足；' : ''}${!finite(item.underlyingPct) ? '標的同比較日資料不足；' : ''}報價／成本未評估</small>`,
    ]);
  })) : empty(state.kind === 'spread-anomaly' || state.kind === 'both' ? '目前沒有符合此類的權證；可能未達門檻或歷史不足。缺少 Z 值不視為沒有風險，也不以較大價差替代異常訊號。' : '目前沒有符合篩選的觀察項目。未達門檻或缺資料時不製造訊號。')}<div class="pagination"><span>第 ${state.page + 1} / ${Math.max(1, Math.ceil(rows.length / 20))} 頁 · 每頁 20 檔</span><div><button data-wf-page="-1" ${state.page === 0 ? 'disabled' : ''}>上一頁</button><button data-wf-page="1" ${(state.page + 1) * 20 >= rows.length ? 'disabled' : ''}>下一頁</button></div></div></section>`;
  const coverage = `<section class="panel source-list"><h2>涵蓋範圍與不足</h2><div class="focus-coverage">${[['量能可比性', report.coverage?.volume], ['價差可比性', report.coverage?.spread], ['未納入基本觀察', report.coverage?.excluded]].map(([title, counts]) => `<div><h3>${title}</h3>${Object.entries(counts || {}).map(([name, count]) => `<p>${esc(name)} <strong>${fmt(count)}</strong> 檔</p>`).join('') || '<p>無項目</p>'}</div>`).join('')}</div><p class="note">量能與價差可比性以通過基本條件的權證為分母；一檔可有多組配對，但清單只取異常 Z 優先、否則絕對價差最大的對象。量能及價差各自判斷，缺一項不會虛構另一項。</p></section>`;
  const method = `<section class="panel source-list"><h2>規則、日期與免費更新</h2><p><strong>量增：</strong>用同市場前 ${r.volumeWindow} 個已收錄行情日的成交量算平均，當日不進基準；零成交納入平均，任一期缺漏則不計倍數。前期均量為零不輸出無限倍數。至少需 ${r.volumeWindow + 1} 個完整資料日；基準最早日距當期超過 ${r.maxBaselineAgeDays} 天也不使用。不是保證連續交易日，日期可展開檢查。</p><p><strong>價差：</strong>使用相同標的、權別、履約價、到期日與歐式／美式的基本條件配對，按行使比例換算；價格、成交量、比例及條款需與當日快照一致。|Z| ≥ 2 至少需要前 20 個完整配對日加上當日。絕對價差 ≥ 20% 僅另列待觀察，不等於公允價折溢價或錯價。</p><p>官方來源更新失敗、行情與條款不同日、距今超過 ${r.maxSourceAgeDays} 天（可能含休市）、零成交、特殊型或已停止交易的商品不作新觀察。若整體計算失敗，保留上次成功清單與原日期並加警示；部分來源失敗時僅展示可確認部分。</p><p>固定門檻 ${esc(r.version)} 尚未經策略有效性驗證；清單不是績效回測或投資建議。未取得同步買賣報價、深度與完整成本，不能當作可成交套利。分點仍是獨立的部分排行，不把它視為持倉或量增原因。</p><p>沿用免費 GitHub Actions，每日台北 08:43、11:43、19:43、22:43 在行情與價差更新後自動產生清單，排程可能延遲。來源日期相同的重跑不算新的一天，不捏造休市資料。<a href="#sources">來源與更新時間 →</a> · <a href="data/derivatives/warrant-focus.json" download>下載完整研究清單 JSON →</a></p></section>`;
  return notice + header + metrics + historical + controls + list + coverage + method;
}

export function createFocusWatch(helpers) {
  const state = { kind: 'all', search: '', page: 0, limit: '50' };
  let report;
  async function page() {
    report = await helpers.load('warrant-focus').catch(() => null);
    return focusContent(report, state, helpers);
  }
  function bind(render) {
    const $ = id => document.getElementById(id);
    const apply = () => { state.search = $('wf-search').value.trim(); state.kind = $('wf-kind').value; state.limit = $('wf-limit').value; state.page = 0; render(); };
    $('wf-apply')?.addEventListener('click', apply);
    $('wf-search')?.addEventListener('keydown', e => { if (e.key === 'Enter') apply(); });
    document.querySelectorAll('[data-wf-page]').forEach(el => el.addEventListener('click', () => { state.page += Number(el.dataset.wfPage); render(); }));
    $('wf-export')?.addEventListener('click', () => {
      const matched = filterFocus(report.items, state.kind, state.search);
      const rows = state.limit === 'all' ? matched : matched.slice(0, 50);
      const url = URL.createObjectURL(new Blob([toCSV(focusExport(rows), fields)], { type: 'text/csv;charset=utf-8' }));
      const a = document.createElement('a'); a.href = url; a.download = `warrant-focus-${taipeiToday()}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  return { page, bind };
}
