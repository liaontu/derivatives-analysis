import { esc, fmt, finite } from './model.mjs';

export const validSeriesReportId = id => /^(weekly|monthly)-\d{4}-\d{2}(-\d{2})?-r\d+-[a-f0-9]{8}$/.test(id || '');
const kindNames = { weekly: '週報', monthly: '月報' };
const num = (x, digits = 2, suffix = '') => finite(x) ? `${fmt(x, digits)}${suffix}` : '—';
const signedPct = x => finite(x) ? `<span class="${x > 0 ? 'positive' : x < 0 ? 'negative' : ''}">${x > 0 ? '+' : ''}${fmt(x, 2)}%</span>` : '—';
const plainPct = x => finite(x) ? `${x > 0 ? '+' : ''}${fmt(x, 2)}%` : '—';
const sideLabel = cp => cp === 'C' ? '認購' : cp === 'P' ? '認售' : esc(cp);
// Same thresholds the generator uses; a report carries its own copy in rules.readings.
const defaultThresholds = { move: { weekly: [1, 3, 7], monthly: [2, 5, 10] }, corr: [0.5, 0.8], consistent: [70, 90], liquidShare: [15, 30], concentration: [30, 50], change: 20 };
const level = (value, cuts, labels) => labels[cuts.filter(c => value >= c).length];

export function reportLabel(item) {
  const kind = kindNames[item.kind] || item.kind, revision = (item.id.match(/-r(\d+)-/) || [])[1] || '?';
  return item.kind === 'monthly' ? `${kind} ${item.periodEnd.slice(0, 7)} · 第 ${revision} 版` : `${kind} ${item.periodStart}～${item.periodEnd.slice(5)} · 第 ${revision} 版`;
}

// Find one underlying by code or name: exact code, exact name, then a unique prefix or substring match.
export function resolveStock(underlyings, query) {
  const q = String(query || '').trim();
  if (!q) return { error: '' };
  const upper = q.toUpperCase();
  const exact = underlyings.find(u => u.code.toUpperCase() === upper) || underlyings.find(u => u.name === q);
  if (exact) return { stock: exact };
  const prefix = underlyings.filter(u => u.code.toUpperCase().startsWith(upper) || u.name.startsWith(q));
  const matches = prefix.length ? prefix : underlyings.filter(u => u.code.toUpperCase().includes(upper) || u.name.includes(q));
  if (matches.length === 1) return { stock: matches[0] };
  if (!matches.length) return { error: `本期報告裡沒有「${q}」；可能該標的在期間內沒有掛牌權證。` };
  return { error: `「${q}」符合 ${matches.length} 個標的（${matches.slice(0, 5).map(u => `${u.code} ${u.name}`).join('、')}${matches.length > 5 ? '…' : ''}），請輸入完整代號。` };
}

// Readings for one underlying from the numbers the report already holds, with the report's own thresholds.
export function stockReadings(report, u) {
  const t = report.rules?.readings || defaultThresholds, kind = report.kind, m = report.market, out = [];
  const moveCuts = t.move[kind] || t.move.weekly;
  const r = u.underlyingReturnPct;
  if (finite(r)) {
    const size = Math.abs(r) < moveCuts[0] ? '接近持平' : level(Math.abs(r), moveCuts.slice(1), ['小幅', '明顯', '大幅']) + (r > 0 ? '上漲' : '下跌');
    const base = u.baseBeforePeriod ? `基準日 ${u.baseDate}` : `歷史不足，以期間內 ${u.baseDate} 為基準，報酬只涵蓋部分期間`;
    const market = finite(m.underlyingReturn?.medianPct) ? `；全市場標的中位數 ${plainPct(m.underlyingReturn.medianPct)}` : '';
    out.push(`標的走勢：期間報酬 ${plainPct(r)}，${size}（${base}）${market}。`);
  } else {
    out.push('標的走勢：期間內沒有足夠的標的收盤價，無法計算期間報酬。');
  }
  for (const [side, label, expect] of [['call', '認購', '同向'], ['put', '認售', '反向']]) {
    const s = u[side];
    if (!s || !s.liquid) { out.push(`${label}：期間內沒有常交易${label}權證。`); continue; }
    let text = `${label}：${s.liquid} 檔常交易`;
    if (kind === 'monthly' && finite(s.medianSlope)) {
      text += `，斜率中位數 ${fmt(s.medianSlope, 3)}（標的日報酬每 1%，${label}約 ${fmt(s.medianSlope, 2)}%）、相關中位數 ${fmt(s.medianCorr, 3)}（${level(Math.abs(s.medianCorr), t.corr, ['低', '中', '高'])}，${s.slopes} 檔）`;
    }
    if (finite(s.medianReturnPct)) {
      text += `，期間報酬中位數 ${plainPct(s.medianReturnPct)}（${s.returns} 檔）`;
      const marketSide = m[side]?.medianReturnPct;
      if (finite(marketSide)) text += `，全市場常交易${label}中位 ${plainPct(marketSide)}`;
      if (finite(r) && Math.abs(r) >= moveCuts[0] && !(kind === 'monthly' && finite(s.medianSlope))) {
        const ratio = s.medianReturnPct / r * (side === 'put' ? -1 : 1);
        text += ratio > 0 ? `，約為標的幅度的 ${fmt(ratio, 1)} 倍` : '，與標的預期方向相反（常見於時間價值衰減主導或樣本組成改變）';
      }
    }
    if (finite(s.consistentShare)) text += `；與標的${expect} ${fmt(s.consistentShare, 0)}%（${level(s.consistentShare, t.consistent, ['只有部分', '多數', '幾乎全部'])}）`;
    if (s.liquid < 5) text += '；檔數少，中位數不穩定';
    out.push(text + '。');
  }
  if (u.warrants) {
    const share = 100 * u.liquid / u.warrants;
    out.push(`流動性：${fmt(u.warrants)} 檔曾掛牌中 ${fmt(u.liquid)} 檔常交易（${fmt(share, 0)}%，${level(share, t.liquidShare, ['低', '中', '高'])}），${fmt(u.warrants - u.traded)} 檔整個期間沒有成交；成交量 ${fmt(u.volume)} 張。`);
  }
  out.push('以上依固定門檻自動產生，只說明這個標的的數字含義與相對大小；不是預測、不評價權證優劣，也不構成買賣建議。');
  return out;
}

export function createSeriesReports({ load, table, tr, select, metric, empty }) {
  const state = { id: '', stock: '', error: '' };

  function watchSection(report, monthly) {
    const watch = report.watch;
    if (!watch) return '';
    const headers = ['標的', '理由', '市場', '標的報酬', '常交易', '成交量（張）', '認購中位報酬／同向', '認售中位報酬／反向', ...(monthly ? ['認購斜率'] : [])];
    const rows = watch.items.map(i => tr([`<a class="text-link" href="#series-report" data-series-stock="${esc(i.code)}">${esc(i.code)} ${esc(i.name)}</a>`, `<span style="white-space:normal;display:inline-block;min-width:260px;max-width:520px">${i.reasons.map(esc).join('；')}</span>`, esc(i.market || '—'), signedPct(i.underlyingReturnPct), String(i.liquid), fmt(i.volume), `${signedPct(i.call.medianReturnPct)}／${num(i.call.consistentShare, 0, '%')}`, `${signedPct(i.put.medianReturnPct)}／${num(i.put.consistentShare, 0, '%')}`, ...(monthly ? [num(i.call.medianSlope, 3)] : [])]));
    const excluded = Object.entries(watch.excluded).map(([k, v]) => `${esc(k)} ${fmt(v)}`).join('；');
    return `<h3>本期值得細看的標的</h3><p class="muted">${esc(watch.basis)}</p><p class="muted">樣本足夠 ${fmt(watch.eligible)} 個；未列入：${excluded || '無'}。點標的可查看個股。</p>${table(headers, rows)}`;
  }

  function stockPanel(report, monthly) {
    const underlyings = report.underlyings;
    const options = underlyings.map(u => `<option value="${esc(u.code)}">${esc(u.name)}</option>`).join('');
    const form = `<form id="series-stock-form" class="filters"><label>標的代號或名稱<input id="series-stock" list="series-stock-list" value="${esc(state.stock)}" placeholder="例如 2330 或 台積電" autocomplete="off"></label><datalist id="series-stock-list">${options}</datalist><button type="submit">查看個股</button></form>`;
    const { stock, error } = resolveStock(underlyings, state.stock);
    let body = '';
    if (error) body = `<div class="notice warning">${esc(error)}</div>`;
    else if (stock) {
      const rank = underlyings.indexOf(stock) + 1;
      const tiles = `<div class="metrics">${metric('標的報酬', signedPct(stock.underlyingReturnPct), stock.baseDate ? `${stock.baseBeforePeriod ? '基準日' : '期間內基準'} ${stock.baseDate} → ${stock.endDate}` : '沒有期間報酬')}${metric('權證', `${fmt(stock.liquid)}<small>常交易／曾掛牌 ${fmt(stock.warrants)}</small>`, `曾成交 ${fmt(stock.traded)}・成交量 ${fmt(stock.volume)} 張・排名第 ${rank}`)}${metric('認購中位報酬／同向', `${signedPct(stock.call.medianReturnPct)}<i>/</i>${num(stock.call.consistentShare, 0, '%')}`, monthly ? `斜率中位數 ${num(stock.call.medianSlope, 3)}・相關 ${num(stock.call.medianCorr, 3)}（${stock.call.slopes} 檔）` : `${stock.call.liquid} 檔常交易、${stock.call.returns} 檔有期間報酬`)}${metric('認售中位報酬／反向', `${signedPct(stock.put.medianReturnPct)}<i>/</i>${num(stock.put.consistentShare, 0, '%')}`, monthly ? `斜率中位數 ${num(stock.put.medianSlope, 3)}・相關 ${num(stock.put.medianCorr, 3)}（${stock.put.slopes} 檔）` : `${stock.put.liquid} 檔常交易、${stock.put.returns} 檔有期間報酬`)}</div>`;
      const listed = (report.watch?.items || []).find(i => i.code === stock.code);
      const watchNote = listed ? `<div class="notice">本期列入「值得細看」：${listed.reasons.map(esc).join('；')}。名單只表示數字特別，不表示機會或風險。</div>` : '';
      const readings = watchNote + `<ul>${stockReadings(report, stock).map(t => `<li>${esc(t)}</li>`).join('')}</ul>`;
      const headers = ['權證', '權別', '履約價', '到期日', '成交日／掛牌日', '成交量（張）', '期間報酬', ...(monthly ? ['斜率', '相關', '配對段數'] : [])];
      const rows = (stock.topWarrants || []).map(w => tr([`${esc(w.code)} ${esc(w.name)}`, sideLabel(w.cp), num(w.strike), esc(w.expiry || '—'), `${w.tradedDays}／${w.listedDays}`, fmt(w.volume), signedPct(w.returnPct), ...(monthly ? [num(w.slope, 3), num(w.corr, 3), String(w.pairs)] : [])]));
      const detail = stock.topWarrants ? `<h4>成交量最大的權證（最多 ${report.rules.topWarrants} 檔）</h4>${table(headers, rows)}` : `<p class="muted">成交量未進前 ${report.rules.topUnderlyings} 名，報告沒有保存個別權證明細。</p>`;
      body = `<h4 id="series-stock-heading">${esc(stock.code)} ${esc(stock.name)}<span class="tag">${esc(stock.market || '—')}</span></h4>${tiles}${readings}${detail}<p><a class="text-link" href="#warrants?underlying=${encodeURIComponent(stock.code)}">查看這個標的的全部權證行情 →</a></p>`;
    } else body = `<p class="muted">輸入標的代號或名稱，或在下方排行表點選標的，顯示它在本期的數字、判讀與成交量最大的權證。</p>`;
    return `<h3>個股查詢</h3>${form}<div id="series-stock-panel">${body}</div>`;
  }

  async function page() {
    const index = await load('warrant-series-reports').catch(() => null);
    const items = (index?.items || []).filter(i => validSeriesReportId(i.id)).slice().reverse();
    const latest = index?.latest || {};
    const chosen = items.some(i => i.id === state.id) ? state.id : (latest.weekly && items.some(i => i.id === latest.weekly) ? latest.weekly : items[0]?.id || '');
    const picker = select('series-report-id', '報告', items.map(i => i.id), chosen, id => reportLabel(items.find(i => i.id === id)));
    const header = `<section class="panel series-report"><div class="panel-head"><div><span class="eyebrow">WARRANT SERIES REPORTS</span><h2>權證與標的收盤序列</h2><p class="muted">每週六整理週一至週五 · 每月第一個週六整理上一個月 · 同期間改版保留舊版 · 免費下載</p></div>${items.length ? picker : ''}</div>`;
    const warning = index?.status === 'retained' ? `<div class="notice warning">${esc(index.error || '本次報告未更新，以下保留舊版本。')}</div>` : '';
    if (!chosen) return header + warning + empty('尚無可讀取的週報或月報；等待週六排程產生。') + '</section>';
    const report = await load(`warrant-series-reports/${chosen}`).catch(() => null);
    if (!report || !validSeriesReportId(report.id)) return header + warning + empty('報告檔暫時無法讀取。') + '</section>';
    const m = report.market, monthly = report.kind === 'monthly';
    const base = `data/derivatives/warrant-series-reports/${report.id}`;
    const metrics = `<div class="metrics">${metric('資料日', `${m.dataDays.length}<small>／交易日曆 ${m.tradingDays.length} 天</small>`, m.missingDays.length ? `缺 ${m.missingDays.join('、')}` : '期間資料齊全')}${metric('常交易權證', fmt(m.liquid), `曾掛牌 ${fmt(m.warrants)}・曾成交 ${fmt(m.traded)}`)}${metric('標的報酬中位數', signedPct(m.underlyingReturn.medianPct), `${fmt(m.underlyingReturn.count)} 個標的有期間報酬`)}${metric('常交易認購／認售中位報酬', `${signedPct(m.call.medianReturnPct)}<i>/</i>${signedPct(m.put.medianReturnPct)}`, `${fmt(m.call.returns)}／${fmt(m.put.returns)} 檔有期間報酬`)}</div>`;
    const headers = ['標的', '市場', '標的報酬', '基準日', '掛牌／成交／常交易', '成交量（張）', '認購中位報酬', '認售中位報酬', '認購同向', '認售反向', ...(monthly ? ['認購斜率（檔）', '認售斜率（檔）'] : [])];
    const rows = report.underlyings.slice(0, 15).map(u => tr([`<a class="text-link" href="#series-report" data-series-stock="${esc(u.code)}">${esc(u.code)} ${esc(u.name)}</a>`, esc(u.market || '—'), signedPct(u.underlyingReturnPct), `${esc(u.baseDate || '—')}${u.baseDate && !u.baseBeforePeriod ? '<small>期間內</small>' : ''}`, `${u.warrants}／${u.traded}／${u.liquid}`, fmt(u.volume), signedPct(u.call.medianReturnPct), signedPct(u.put.medianReturnPct), num(u.call.consistentShare, 0, '%'), num(u.put.consistentShare, 0, '%'), ...(monthly ? [`${num(u.call.medianSlope, 3)}（${u.call.slopes}）`, `${num(u.put.medianSlope, 3)}（${u.put.slopes}）`] : [])]));
    const topHeaders = ['權證', '標的', '權別', '履約價', '到期日', '成交日／掛牌日', '成交量（張）', '期間報酬', ...(monthly ? ['斜率', '相關', '配對段數'] : [])];
    const topRows = report.topWarrants.map(w => tr([`${esc(w.code)} ${esc(w.name)}`, `<a class="text-link" href="#series-report" data-series-stock="${esc(w.underlying)}">${esc(w.underlying)} ${esc(w.underlyingName)}</a>`, sideLabel(w.cp), num(w.strike), esc(w.expiry || '—'), `${w.tradedDays}／${w.listedDays}`, fmt(w.volume), signedPct(w.returnPct), ...(monthly ? [num(w.slope, 3), num(w.corr, 3), String(w.pairs)] : [])]));
    const previous = report.previous ? `<p class="muted">上一期：${esc(report.previous.id)}（${esc(report.previous.period.start)}～${esc(report.previous.period.end)}）常交易 ${fmt(report.previous.market.liquid)} 檔、成交量 ${fmt(report.previous.market.volume)} 張。兩期樣本不同，差異不代表趨勢。</p>` : '';
    return header + warning + `<p>${esc(kindNames[report.kind] || report.kind)} · 期間 ${esc(report.period.start)}～${esc(report.period.end)} · 資料至 ${esc(m.dataDays.at(-1) || '尚無資料')} · ${esc(report.rules.version)}</p>${metrics}<h3>本期觀察</h3><ul>${report.observations.map(t => `<li>${esc(t)}</li>`).join('')}</ul><h3>本期判讀</h3><p class="muted">依固定門檻自動產生，說明數字的含義與相對大小；不是預測或建議。</p><ul>${(report.readings || []).map(t => `<li>${esc(t)}</li>`).join('')}</ul>${watchSection(report, monthly)}${previous}<p><a class="text-link" href="${base}.html" target="_blank" rel="noopener noreferrer">開啟完整報告 ↗</a> · <a href="${base}.html" download>下載 HTML 報告</a> · <a href="${base}.json" download>下載核對資料 JSON</a></p><p class="note">常交易：期間內成交日佔掛牌日 ${Math.round(report.rules.liquidShare * 100)}% 以上且至少 ${report.rules.liquidMinDays} 天。期間報酬以最後成交收盤對基準日（期間前最後一個資料日）收盤；標示「期間內」表示歷史不足。${monthly ? `斜率為權證日報酬對標的日報酬的最小平方法係數，配對段數至少 ${report.rules.minPairs} 段才計算。` : ''}同一標的的權證共用行情，不是獨立樣本。</p>${stockPanel(report, monthly)}<h3>標的比較（成交量前 15 名，完整 ${report.underlyings.length} 個見 JSON；點標的可查看個股）</h3>${table(headers, rows)}<h3>成交量最大的常交易權證</h3>${table(topHeaders, topRows)}<details><summary>報告限制與核對紀錄</summary><ul>${report.limitations.map(t => `<li>${esc(t)}</li>`).join('')}</ul><p class="note">生成時間 ${esc(report.generatedAt)} · 內容 SHA-256：<code>${esc(report.contentSha256)}</code></p></details></section>`;
  }

  function bind(rerender) {
    document.getElementById('series-report-id')?.addEventListener('change', event => { state.id = event.target.value; rerender(); });
    const form = document.getElementById('series-stock-form');
    form?.addEventListener('submit', event => { event.preventDefault(); state.stock = form.elements['series-stock'].value.trim(); rerender(); });
    document.querySelectorAll('[data-series-stock]').forEach(el => el.addEventListener('click', async event => {
      event.preventDefault(); state.stock = el.dataset.seriesStock; await rerender();
      document.getElementById('series-stock-heading')?.scrollIntoView({ block: 'start' });
    }));
  }

  return { page, bind };
}
