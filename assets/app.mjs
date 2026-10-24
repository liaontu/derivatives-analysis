import { finite, fmt, esc, cpLabel, warrantCp, warrantStyle, warrantSortLabels, warrantUnderlyings, resolveWarrantUnderlying, taipeiToday, daysUntil, filterWarrants, oiDistribution, previousOi, toCSV, isStaleSource } from './model.mjs';
import { createUSPages } from './us.mjs';
import { createWarrantBranches } from './branches.mjs';
import { createDailyWatch } from './watch.mjs';
import { createWarrantDaily } from './daily-table.mjs';
import { createSpreadWatch } from './spreads.mjs';
import { governancePage } from './governance.mjs';

const $ = selector => document.querySelector(selector);
const cache = new Map();
let manifest, generation = 0, exportRows = [], warrantChoices = [];
const state = { page: 0, warrant: { active: true, minVolume: '', sort: 'volume' } };
const pages = {
  overview: ['市場總覽', '把行情、部位與交易熱度，放在同一個視野。'],
  futures: ['期貨籌碼追蹤', '法人多空部位、大額交易人與期貨商成交熱度。'],
  options: ['選擇權籌碼追蹤', '從買賣權部位、履約價與 Put/Call 比，觀察市場結構。'],
  warrants: ['權證行情篩選', '按個股查看權證，依到期日與履約價排序，分辨歐式及美式履約方式。'],
  'warrant-branches': ['權證分點排行', '富邦買超／賣超分點排行，每日更新；非全部分點。'],
  'daily-watch': ['每日變動觀察', '權證價格、標的股價與分點排行，逐日對照變化。'],
  'warrant-daily': ['個股權證每日觀察表', '同一標的的權證漲跌、量變、價差與資料完整度，一次對照。'],
  'warrant-spreads': ['權證價差異常觀察', '同條件權證的相對價差、歷史偏離與訊號事件回測。'],
  'us-futures': ['美國期貨', 'CFTC 每週持倉與 CFE 每日結算價，各自標示資料日期。'],
  'us-options': ['美國選擇權', 'Cboe 每日交易統計、P/C 比，以及 CFTC 每週期權合併籌碼。'],
  sources: ['資料與更新', '每項數據的來源、資料日期與更新狀態，都可以追溯。'],
  quality: ['資料品質與驗證', '核對來源、追蹤修訂，分清研究回測與正式績效。'],
};
const commonNames = { TX: '臺股期貨', TXF: '臺股期貨', MTX: '小型臺指', MXF: '小型臺指', TMF: '微型臺指', TXO: '臺指選擇權', TE: '電子期貨', TEF: '電子期貨', TF: '金融期貨', TFF: '金融期貨', TFO: '金融選擇權', TEO: '電子選擇權' };
const contractName = c => commonNames[c] ? `${c} · ${commonNames[c]}` : c;
const monthName = m => ({ '999912': '所有契約', '666666': '週契約合計' }[m] || m);
const signed = x => finite(x) ? `<span class="${x > 0 ? 'positive' : x < 0 ? 'negative' : ''}">${x > 0 ? '+' : ''}${fmt(x)}</span>` : '—';
const dateTag = (d, label = '資料日') => `<span class="tag">${label} ${esc(d || '尚無資料')}</span>`;
const empty = text => `<div class="empty">${esc(text)}</div>`;
const table = (headers, rows) => `<div class="table-wrap"><table><thead><tr>${headers.map((h, i) => `<th${i ? ' class="num"' : ''}>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.length ? rows.join('') : `<tr><td colspan="${headers.length}">${empty('此篩選條件沒有資料')}</td></tr>`}</tbody></table></div>`;
const tr = cells => `<tr>${cells.map((c, i) => `<td${i ? ' class="num"' : ''}>${c}</td>`).join('')}</tr>`;
const select = (id, label, values, chosen, labels = v => v) => `<label>${esc(label)}<select id="${id}">${values.map(v => `<option value="${esc(v)}" ${v === chosen ? 'selected' : ''}>${esc(labels(v))}</option>`).join('')}</select></label>`;
const unique = (rows, key) => [...new Set(rows.map(r => r[key]).filter(Boolean))].sort();
const choose = (values, desired) => values.includes(desired) ? desired : values[0];
const us = createUSPages({ load, state, select, choose, unique, table, tr, dateTag, signed, metric, lineChart, empty });

const branches = createWarrantBranches({ load, table, tr, select, dateTag, signed, metric, empty });
const dailyWatch = createDailyWatch({ load, table, tr, select, metric, dateTag, empty });
const warrantDaily = createWarrantDaily({ load, table, tr, select, metric, empty });
const spreadWatch = createSpreadWatch({ load, table, tr, select, metric, dateTag, empty });

async function load(name) {
  if (!cache.has(name)) cache.set(name, (async () => {
    const base = `data/derivatives/${name}.json`, options = { cache: 'no-cache' };
    if ('DecompressionStream' in window) {
      try {
        const response = await fetch(base + '.gz', options);
        if (!response.ok) throw new Error(String(response.status));
        return await new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).json();
      } catch { /* Older browsers and proxies can use the plain JSON copy. */ }
    }
    const response = await fetch(base, options);
    if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
    return response.json();
  })().catch(error => { cache.delete(name); throw error; }));
  return cache.get(name);
}

function lineChart(points, label, percent = false, digits = percent ? 1 : 0) {
  const valid = points.filter(p => finite(p.value));
  if (valid.length < 2) return empty(`已累積 ${valid.length} 個資料日；滿兩日後顯示趨勢。每日自動累積，最多保留 60 個資料日。`);
  const width = 700, height = 230, low = Math.min(...valid.map(p => p.value)), high = Math.max(...valid.map(p => p.value));
  const range = high - low || Math.max(1, Math.abs(high) * .02), x = i => 55 + i / (valid.length - 1) * 620, y = v => 178 - (v - low) / range * 145;
  const path = valid.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  return `<div class="chart"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label)}"><title>${esc(label)}</title>${[low, low + range / 2, low + range].map(v => `<line x1="55" x2="680" y1="${y(v)}" y2="${y(v)}" stroke="#2c4050"/><text x="48" y="${y(v) + 4}" text-anchor="end">${fmt(v, digits)}${percent ? '%' : ''}</text>`).join('')}<path d="${path}" stroke="#77e2d0" stroke-width="2.4" fill="none"/>${valid.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.value)}" r="2.7" fill="#77e2d0"><title>${esc(p.date)}：${fmt(p.value, Math.max(2, digits))}${percent ? '%' : ''}</title></circle>`).join('')}<text x="55" y="209">${esc(valid[0].date)}</text><text x="680" y="209" text-anchor="end">${esc(valid.at(-1).date)}</text></svg></div>`;
}

function metric(label, value, note, cls = '') {
  return `<div class="metric"><div class="label">${esc(label)}</div><div class="value ${cls}">${value}</div><small>${esc(note)}</small></div>`;
}

async function overview() {
  const [futures, inst, pcr] = await Promise.all(['futures', 'futures-institutional', 'pcr'].map(load));
  const front = futures.rows.filter(r => ['TX', 'TXF'].includes(r.contract) && r.session === '一般' && !r.month.includes('/')).sort((a, b) => a.month.localeCompare(b.month))[0];
  const foreign = inst.rows.find(r => r.contract === '臺股期貨' && r.investor?.startsWith('外資'));
  const ratio = pcr.rows[0];
  const points = pcr.history.map(d => ({ date: d.date, value: d.rows[0]?.oiRatio }));
  const watch = futures.rows.filter(r => ['TX', 'TXF', 'MTX', 'MXF', 'TMF', 'TE', 'TEF', 'TF', 'TFF'].includes(r.contract) && r.session === '一般' && !r.month.includes('/')).sort((a, b) => b.volume - a.volume).slice(0, 6);
  return `<div class="metrics">${metric('臺股期貨・近月結算', fmt(front?.price, 2), `${front?.month || '—'} · ${futures.date || '尚無資料'}`)}${metric('外資臺股期貨淨未平倉', signed(foreign?.netOi), `口 · ${inst.date || '尚無資料'}`)}${metric('臺指選擇權 P/C 未平倉比', finite(ratio?.oiRatio) ? fmt(ratio.oiRatio, 2) + '%' : '—', `${pcr.date || '尚無資料'} · 全契約`, 'teal')}${metric('上市櫃權證資料', fmt(manifest.warrantCount), '檔 · 可按個股查看，包含無成交權證')}</div>
  <div class="split"><section class="panel"><div class="panel-head"><div><span class="eyebrow">POSITIONING TREND</span><h2>臺指選擇權 P/C 比趨勢</h2></div>${dateTag(pcr.date)}</div>${lineChart(points, '臺指選擇權未平倉 Put/Call 比', true)}<p class="note">P/C 比＝賣權未平倉量 ÷ 買權未平倉量 × 100%。每個選擇權部位都有買賣雙方，比例本身不等於市場方向。</p></section><section class="panel"><span class="eyebrow">INSTITUTIONAL POSITION</span><h2>臺股期貨法人淨未平倉</h2><p class="muted">${esc(inst.date || '尚無資料')} · 單位：口</p>${inst.rows.filter(r => r.contract === '臺股期貨').map(r => `<div class="bar-row"><span>${esc(r.investor)}</span><div class="bar-track"><span style="width:${Math.min(100, Math.abs(r.netOi || 0) / Math.max(...inst.rows.filter(i => i.contract === '臺股期貨').map(i => Math.abs(i.netOi || 0)), 1) * 100)}%;background:${r.netOi < 0 ? '#6fd7ac' : '#ff8e99'}"></span></div><strong>${signed(r.netOi)}</strong></div>`).join('')}<p class="note">三類法人分別呈現。淨額是類別內部位互抵的結果，不能代表每一家機構的策略。</p><a href="#futures" class="text-link">查看法人與大額交易人 →</a></section></div>
  <div class="cards"><a href="#futures" class="link-card"><h3>⌁ 期貨籌碼</h3><p>法人多空、前五／十大部位、期貨商成交量</p></a><a href="#options" class="link-card"><h3>◇ 選擇權籌碼</h3><p>買賣權分計、履約價未平倉分布、P/C 比</p></a><a href="#warrants" class="link-card"><h3>▤ 權證篩選</h3><p>上市與上櫃權證、條件交叉篩選、CSV 匯出</p></a></div>
  <section class="panel"><p><a class="text-link" href="#warrant-daily">個股權證每日觀察表 → 漲跌、量變、同條件價差與完整度</a></p><a class="text-link" href="#daily-watch">每日變動觀察 → 權證價格、標的股價與分點排行變化</a><p><a class="text-link" href="#warrant-spreads">權證價差異常觀察 → 同條件比較與訊號回測</a></p></section><div class="cards us-entry"><a href="#us-futures" class="link-card"><h3>US · 美國期貨</h3><p>CFTC 每週持倉 · CFE 每日結算價</p></a><a href="#us-options" class="link-card"><h3>US · 美國選擇權</h3><p>Cboe 每日 P/C 比 · 期權合併持倉</p></a></div><section class="panel"><div class="panel-head"><h2>重點期貨行情</h2>${dateTag(futures.date)}</div>${table(['契約', '月份', '結算價', '最後成交價', '成交量（口）', '未平倉（口）'], watch.map(r => tr([esc(contractName(r.contract)), esc(r.month), fmt(r.price, 2), fmt(r.last, 2), fmt(r.volume), fmt(r.oi)])))}<p class="note">一般交易時段；結算價與最後成交價分別標示。價格單位依各商品契約規格。</p></section>`;
}

async function positioning(kind) {
  const [inst, large, fcm, quotes, pcr] = await Promise.all([`${kind}-institutional`, `${kind}-large`, `${kind}-fcm`, kind, 'pcr'].map(load));
  const isOption = kind === 'options';
  const instContracts = unique(inst.rows, 'contract');
  state[kind + 'Inst'] = choose(instContracts, state[kind + 'Inst'] || (isOption ? '臺指選擇權' : '臺股期貨'));
  const selected = inst.rows.filter(r => r.contract === state[kind + 'Inst']);
  const dates = (inst.history || []).map(d => d.date);
  state[kind + 'Date'] = choose(dates, state[kind + 'Date'] || inst.date);
  const historical = inst.history.find(d => d.date === state[kind + 'Date'])?.rows || inst.rows;
  const shown = historical.filter(r => r.contract === state[kind + 'Inst']);
  const currentDate = state[kind + 'Date'] || inst.date;
  const largeContracts = unique(large.rows, 'contract');
  state[kind + 'Large'] = choose(largeContracts, state[kind + 'Large'] || (isOption ? 'TXO' : largeContracts.includes('TX') ? 'TX' : 'TXF'));
  const largeMonths = unique(large.rows.filter(r => r.contract === state[kind + 'Large']), 'month');
  state[kind + 'Month'] = choose(largeMonths, state[kind + 'Month'] || '999912');
  const largeRows = large.rows.filter(r => r.contract === state[kind + 'Large'] && r.month === state[kind + 'Month']);
  const fcmContracts = unique(fcm.rows, 'contract');
  state[kind + 'Fcm'] = choose(fcmContracts, state[kind + 'Fcm'] || (isOption ? 'TXO' : 'TXF'));
  const fcmRows = fcm.rows.filter(r => r.contract === state[kind + 'Fcm']).sort((a, b) => (b.volume || 0) - (a.volume || 0));
  const points = inst.history.map(d => ({ date: d.date, value: d.rows.find(r => r.contract === state[kind + 'Inst'] && r.investor?.startsWith('外資') && (!isOption || r.cp === 'C'))?.netOi }));
  let html = `<section class="panel"><div class="panel-head"><div><span class="eyebrow">INSTITUTIONAL FLOW</span><h2>${isOption ? '法人買權／賣權部位' : '三大法人多空部位'}</h2></div>${dateTag(currentDate)}</div><div class="filters compact">${select('inst-contract', '商品', instContracts, state[kind + 'Inst'])}${select('inst-date', '資料日', [...dates].reverse(), currentDate)}</div>${table([isOption ? '法人 / 權別' : '法人', isOption ? '買方成交' : '多方成交', isOption ? '賣方成交' : '空方成交', '成交淨額', isOption ? '買方未平倉' : '多方未平倉', isOption ? '賣方未平倉' : '空方未平倉', '淨未平倉', '較前筆資料增減'], shown.map(r => { const prev = previousOi(inst.history, currentDate, r); return tr([`${esc(r.investor)}${isOption ? `<small>${cpLabel(r.cp)}</small>` : ''}`, fmt(r.buy), fmt(r.sell), signed(r.net), fmt(r.longOi), fmt(r.shortOi), signed(r.netOi), `${signed(prev.change)}${prev.date ? `<small>比較 ${esc(prev.date)}</small>` : ''}`]); }))}<p class="note">單位：口。${isOption ? '買權、賣權分開統計；賣權買方淨部位不能直接當成看多部位。' : '多空淨額＝多方減空方；外資、投信、自營商各自列示。'} 歷史從啟用日起每日累積，保留最近 60 個資料日；缺少前筆時不計算增減。</p></section>
  <section class="panel"><div class="panel-head"><h2>${esc(state[kind + 'Inst'])}・外資${isOption ? '買權' : ''}淨未平倉趨勢</h2>${dateTag(inst.date)}</div>${lineChart(points, '外資淨未平倉口數趨勢')}</section>
  <section class="panel"><div class="panel-head"><div><span class="eyebrow">LARGE TRADERS</span><h2>前五大／前十大交易人部位</h2></div>${dateTag(large.date)}</div><div class="filters compact">${select('large-contract', '契約', largeContracts, state[kind + 'Large'], c => `${c} · ${large.rows.find(r => r.contract === c)?.name || ''}`)}${select('large-month', '月份 / 統計範圍', largeMonths, state[kind + 'Month'], monthName)}</div>${table(['類別' + (isOption ? ' / 權別' : ''), '前五大買方', '前五大賣方', '前十大買方', '前十大賣方', '全市場未平倉'], largeRows.map(r => tr([`${r.traderType === '0' ? '全部大額交易人' : r.traderType === '1' ? '其中：特定法人' : esc(r.traderType)}${isOption ? `<small>${cpLabel(r.cp)}</small>` : ''}`, fmt(r.top5Buy), fmt(r.top5Sell), fmt(r.top10Buy), fmt(r.top10Sell), fmt(r.oi)])))}<p class="note">單位：口。特定法人是大額交易人中的子集合，不能相加。不同月份的排行也不能相加推算所有契約排行。${isOption ? '' : '臺股期貨合計口徑包含依規模折算的小型、微型契約，依期交所說明。'}</p></section>`;
  if (isOption) {
    const ratio = pcr.rows[0];
    html += `<div class="metrics">${metric('P/C 未平倉比', finite(ratio?.oiRatio) ? fmt(ratio.oiRatio, 2) + '%' : '—', `${pcr.date || '—'} · 臺指全契約`, 'teal')}${metric('P/C 成交量比', finite(ratio?.volumeRatio) ? fmt(ratio.volumeRatio, 2) + '%' : '—', `${pcr.date || '—'} · 臺指全契約`)}${metric('買權未平倉', fmt(ratio?.callOi), '口 · 臺指全契約')}${metric('賣權未平倉', fmt(ratio?.putOi), '口 · 臺指全契約')}</div>`;
  }
  html += quotePanel(quotes, kind);
  html += `<section class="panel"><div class="panel-head"><div><span class="eyebrow">BROKER ACTIVITY</span><h2>期貨商成交量排行</h2></div>${dateTag(fcm.date)}</div><div class="filters compact">${select('fcm-contract', '商品', fcmContracts, state[kind + 'Fcm'], contractName)}</div>${table(['期貨商', '代號', '成交量（口）'], fcmRows.map(r => tr([esc(r.name), esc(r.code), fmt(r.volume)])))}<p class="note">期貨商公司層級的交易量，涵蓋買賣成交；不提供營業分點、買賣淨額或期貨商自己的持倉方向。</p></section>`;
  return html;
}

function quotePanel(payload, kind) {
  const opt = kind === 'options', contracts = unique(payload.rows, 'contract');
  state[kind + 'Quote'] = choose(contracts, state[kind + 'Quote'] || (opt ? 'TXO' : contracts.includes('TX') ? 'TX' : 'TXF'));
  const contract = state[kind + 'Quote'], rows = payload.rows.filter(r => r.contract === contract && r.session === '一般');
  const months = unique(rows, 'month');
  state.optionExpiry = choose(months, state.optionExpiry);
  const chosen = opt ? rows.filter(r => r.month === state.optionExpiry) : rows;
  const page = Math.min(state.quotePage || 0, Math.max(0, Math.ceil(chosen.length / 40) - 1));
  state.quotePage = page;
  const sorted = chosen.slice().sort((a, b) => opt ? a.strike - b.strike || a.cp.localeCompare(b.cp) : a.month.localeCompare(b.month));
  let chart = '';
  if (opt) {
    const distribution = oiDistribution(payload.rows, contract, state.optionExpiry);
    const peak = distribution.reduce((best, r, i) => (r.C || 0) + (r.P || 0) > (distribution[best]?.C || 0) + (distribution[best]?.P || 0) ? i : best, 0);
    const start = Math.max(0, Math.min(distribution.length - 21, peak - 10));
    const windowRows = distribution.slice(start, start + 21);
    const max = Math.max(...windowRows.flatMap(r => [r.C || 0, r.P || 0]), 1);
    chart = `<div class="chart-legend"><span>買權 Call</span><span>賣權 Put</span></div><div class="chart"><svg viewBox="0 0 800 250" role="img" aria-label="履約價未平倉分布"><title>履約價未平倉分布</title>${[0, .5, 1].map(p => `<line x1="50" x2="785" y1="${195 - 170 * p}" y2="${195 - 170 * p}" stroke="#2c4050"/><text x="43" y="${199 - 170 * p}" text-anchor="end">${fmt(max * p)}</text>`).join('')}${windowRows.map((r, i) => { const step = 720 / Math.max(1, windowRows.length), x = 55 + i * step, w = Math.max(2, step * .34); return `<rect x="${x}" y="${195 - (r.C || 0) / max * 170}" width="${w}" height="${(r.C || 0) / max * 170}" fill="#77e2d0"><title>${r.strike} 買權：${fmt(r.C)} 口</title></rect><rect x="${x + w + 1}" y="${195 - (r.P || 0) / max * 170}" width="${w}" height="${(r.P || 0) / max * 170}" fill="#b3a2ff"><title>${r.strike} 賣權：${fmt(r.P)} 口</title></rect>${i % 2 === 0 ? `<text x="${x + w}" y="220" text-anchor="middle">${fmt(r.strike)}</text>` : ''}`; }).join('')}</svg></div><p class="muted">顯示未平倉量最大履約價附近最多 21 檔；完整履約價見下表。缺值在表中顯示「—」。</p>`;
  }
  return `<section class="panel"><div class="panel-head"><div><span class="eyebrow">${opt ? 'OPEN INTEREST BY STRIKE' : 'CONTRACT QUOTES'}</span><h2>${opt ? '履約價未平倉分布與行情' : '期貨契約行情'}</h2></div>${dateTag(payload.date)}</div><div class="filters compact">${select('quote-contract', '商品', contracts, contract, contractName)}${opt ? select('option-expiry', '到期月份 / 週別', months, state.optionExpiry) : ''}</div>${chart}${table(opt ? ['履約價 / 權別', '結算價', '最後成交價', '成交量（口）', '未平倉（口）', '最後買價', '最後賣價'] : ['月份', '結算價', '最後成交價', '成交量（口）', '未平倉（口）', '最後買價', '最後賣價'], sorted.slice(page * 40, (page + 1) * 40).map(r => tr([opt ? `${fmt(r.strike, 2)}<small>${cpLabel(r.cp)}</small>` : esc(r.month), fmt(r.price, 3), fmt(r.last, 3), fmt(r.volume), fmt(r.oi), fmt(r.bid, 3), fmt(r.ask, 3)])))}<div class="pagination"><span>${fmt(chosen.length)} 筆 · 第 ${page + 1} / ${Math.max(1, Math.ceil(chosen.length / 40))} 頁</span><div><button data-quote-page="-1" ${page === 0 ? 'disabled' : ''}>上一頁</button><button data-quote-page="1" ${(page + 1) * 40 >= chosen.length ? 'disabled' : ''}>下一頁</button></div></div><p class="note">一般交易時段，日夜盤未平倉不重複加總。結算價可在沒有成交時存在；最後成交價、買賣價缺值不補成零。</p></section>`;
}

async function warrants() {
  const data = await load('warrants');
  const underlying = new URLSearchParams(location.hash.split('?')[1] || '').get('underlying') || '';
  if (state.warrantUnderlying !== underlying) {
    state.warrant = { underlying, active: !underlying, minVolume: '', sort: underlying ? 'expiry' : 'volume' };
    state.warrantUnderlying = underlying;
    state.page = 0;
  }
  warrantChoices = warrantUnderlyings(data.rows);
  const selected = warrantChoices.find(r => r.code === underlying);
  const f = state.warrant, rows = filterWarrants(data.rows, f);
  exportRows = rows.map(r => ({ ...r, exerciseStyle: warrantStyle(r.style) }));
  state.page = Math.min(state.page, Math.max(0, Math.ceil(rows.length / 50) - 1));
  const page = state.page;
  const title = underlying ? `${selected?.label || underlying} 的權證` : '權證篩選結果';
  return `<section class="panel">
    <div class="panel-head"><div><span class="eyebrow">WARRANTS BY UNDERLYING</span><h2>依個股查看全部權證</h2><p class="muted">以標的代碼精確分組，包含認購、認售與無成交權證；只顯示權證。</p></div></div>
    <div class="filters compact"><label class="search">標的個股代碼或名稱<input id="w-underlying" type="search" list="w-underlying-list" value="${esc(selected?.label || underlying)}" placeholder="例如 2330、台積電，或從建議清單選取" aria-describedby="w-underlying-error"></label><datalist id="w-underlying-list">${warrantChoices.map(r => `<option value="${esc(r.label)}"></option>`).join('')}</datalist><button id="w-underlying-apply">查看此標的全部權證</button><button id="w-all-market">回到全部標的</button></div>
    <p id="w-underlying-error" class="status-bad" role="status"></p><p class="note">「全部權證」會清除其他篩選，包含來源所列已到期或停止交易的權證；可再勾選下方排除條件。行情清單不受分點排行前 300 檔限制。</p>
  </section>
  <section class="panel"><div class="filters"><label class="search">搜尋代碼、名稱、標的或發行商關鍵字<input id="w-search" type="search" value="${esc(f.search || '')}" placeholder="例如 元大、認購權證代碼"></label>${select('w-market', '市場', ['', 'TWSE', 'TPEX'], f.market || '', v => ({ '': '上市＋上櫃', TWSE: '上市', TPEX: '上櫃' }[v]))}${select('w-cp', '權別', ['', 'C', 'P'], f.cp || '', v => v ? warrantCp(v) : '全部')}${select('w-style', '履約方式', ['', '歐式', '美式', 'unknown'], f.style || '', v => ({ '': '全部履約方式', unknown: '未提供／未識別' }[v] || v))}<label>最低成交量（張）<input id="w-volume" type="number" min="0" step="any" value="${esc(f.minVolume || '')}" placeholder="不限，含無成交"></label><label>至少剩餘天數<input id="w-days" type="number" min="0" step="1" value="${esc(f.minDays || '')}" placeholder="不限"></label><label>最高收盤價（元）<input id="w-price" type="number" min="0" step="0.01" value="${esc(f.maxPrice || '')}" placeholder="不限"></label>${select('w-sort', '排序', Object.keys(warrantSortLabels), f.sort, v => warrantSortLabels[v])}<label class="check"><input id="w-active" type="checkbox" ${f.active ? 'checked' : ''}> 排除已到期／已過最後交易日</label><button id="w-apply">套用篩選</button><button id="w-reset">重設篩選</button></div><p class="muted">${underlying ? `目前限定標的：${esc(selected?.label || underlying)}。` : ''}成交量預設不限，包含零成交及成交量缺值；「—」表示缺值。1 張＝1,000 單位。</p></section>
  <section class="panel"><p><a class="text-link" href="#warrant-daily${underlying ? `?underlying=${encodeURIComponent(underlying)}` : ''}">查看個股權證每日觀察表 →</a></p><a class="text-link" href="#warrant-spreads${underlying ? `?underlying=${encodeURIComponent(underlying)}` : ''}">查看同標的權證價差異常與訊號回測 →</a></section><section class="panel source-list warrant-rules"><h2>歐式／美式履約規則</h2><p><strong>歐式</strong>：僅到期日可申請履約。<strong>美式</strong>：可在權證約定的履約期間內申請履約。這是申請履約時點的區別；市場買賣與最後交易日另依條款。</p><p class="note">到期日排序採官方的履約截止日（上市）或到期日（上櫃），與最後交易日分開顯示；實際申請時間、結算方式依發行條款及券商規定。<a href="https://www.twse.com.tw/zh/products/securities/warrant/educate/qa.html" target="_blank" rel="noopener noreferrer">證交所履約說明 ↗</a></p></section>
  <section class="panel"><div class="panel-head"><div><h2>${esc(title)}</h2><p class="muted">${fmt(rows.length)} 檔符合目前條件 · ${esc(warrantSortLabels[f.sort])} · ${f.active ? '排除已到期／停止交易' : '含來源所列已到期／停止交易'}</p><p class="muted">${manifest.sources.filter(s => ['twse-warrants', 'tpex-warrants'].includes(s.id)).map(s => `${s.id.startsWith('twse') ? '上市' : '上櫃'} ${esc(s.date || '無資料')}`).join(' · ')} · 剩餘天數以台北今日 ${taipeiToday()} 計算</p></div><button id="w-export" ${rows.length ? '' : 'disabled'}>匯出篩選結果 CSV</button></div>${table(['權證 / 代碼', '到期日 / 剩餘天數', '履約價', '履約方式', '市場 / 權別', '標的', '收盤價（元）', '成交量（張）', '成交金額（元）', '行使比例', '最後交易日', '行情資料日'], rows.slice(page * 50, (page + 1) * 50).map(r => tr([`<a class="text-link" href="#warrant-branches?code=${esc(r.code)}">${esc(r.name)}<small>${esc(r.code)} · 查看分點排行 →</small></a><small><a class="text-link" href="#daily-watch?code=${esc(r.code)}">每日價格與籌碼變化 →</a></small>${(daysUntil(r.expiry) !== null && daysUntil(r.expiry) < 0) || (daysUntil(r.lastTrade) !== null && daysUntil(r.lastTrade) < 0) ? '<small class="status-bad">已到期／停止交易</small>' : ''}`, `${esc(r.expiry || '—')}<small>${fmt(daysUntil(r.expiry))} 天</small>`, fmt(r.strike, 3), esc(warrantStyle(r.style)), `${r.market === 'TWSE' ? '上市' : '上櫃'} / ${warrantCp(r.cp)}`, `${esc(r.underlyingName || '—')}<small>${esc(r.underlying || '')}</small>`, fmt(r.price, 3), fmt(r.volume, 3), fmt(r.value), fmt(r.ratio, 6), esc(r.lastTrade || '—'), `${esc(r.date)}${r.termsDate && r.termsDate !== r.date ? `<small>條款 ${esc(r.termsDate)}</small>` : ''}`])))}<div class="pagination"><span>${fmt(rows.length)} / ${fmt(data.rows.length)} 檔 · 第 ${page + 1} / ${Math.max(1, Math.ceil(rows.length / 50))} 頁</span><div><button data-w-page="-1" ${page === 0 ? 'disabled' : ''}>上一頁</button><button data-w-page="1" ${(page + 1) * 50 >= rows.length ? 'disabled' : ''}>下一頁</button></div></div><p class="note">日期與履約價缺值一律排在最後。所有權證行情依官方來源清單呈現；分點排行仍僅限每日追蹤清單，每檔買超／賣超各最多 15 名，非全部分點。</p></section>`;
}

async function sources() {
  return `<section class="panel"><a class="text-link" href="#quality">資料品質與驗證 → 來源校驗碼、品質警示及回測使用資格</a></section><section class="panel source-list"><span class="eyebrow">FREE DAILY UPDATES</span><h2>每天自動更新，開啟網站就能查</h2><p>台北時間每天 08:43、11:43、19:43、22:43 檢查並發布。上午接收美國盤後資料，晚上接收台灣盤後資料。CFTC 籌碼為每週公布，日排程不會把週報變成日資料。休市日保留官方最近資料；排程可能延遲。上方按鈕只重載最近發布的資料，不會觸發額外抓取。</p><p>官方資料無需帳號或金鑰。網站使用免費 GitHub Pages 與公開儲存庫標準執行器，不使用付費行情、付費伺服器或自訂網域。</p><a href="https://github.com/liaontu/derivatives-analysis/actions/workflows/derivatives-daily.yml" target="_blank" rel="noopener noreferrer">查看每日更新執行紀錄 ↗</a><div class="coverage">涵蓋台灣期貨與選擇權官方每日行情、三大法人、大額交易人、各期貨商成交量，以及上市櫃權證；美國加入 CFTC 金融／商品期貨與期權合併週報、Cboe 每日選擇權統計、CFE 每日結算價。未提供 CME 全商品行情或美股完整選擇權鏈，每日觀察另包含權證標的股票／ETF 收盤價及指數；不抓現股分點。另提供富邦權證分點排行，非全部分點。</div></section>
  ${await branches.summary()}<section class="panel"><div class="panel-head"><h2>各來源狀態</h2><span class="muted">最近檢查 ${esc(new Date(manifest.checkedAt).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false }))}（台北）</span></div>${table(['來源', '原始頻率', '資料日', '筆數', '狀態'], manifest.sources.map(s => tr([`<a class="text-link" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.label)} ↗</a>${s.error ? '<small>來源暫時異常，等待下一次排程重試</small>' : ''}`, s.frequency === 'weekly' ? '每週 · 日排程檢查' : '每日盤後', esc(s.date || '—'), fmt(s.count), `<span class="${s.status === 'ok' ? 'status-ok' : 'status-bad'}">${{ ok: '取得成功', retained: '更新失敗・保留舊值', unavailable: '暫無資料' }[s.status] || esc(s.status)}</span>`])))}<p class="note">資料日期取自來源；檢查時間不會代替資料日期。來源失敗會保留最後成功資料並在網站與工作流程標示。日資料超過 4 天、CFTC 週報基準日超過 14 天會顯示提醒，連假或官方延後發布也可能觸發。</p></section>
  <section class="panel source-list"><h2>資料口徑與使用說明</h2><p>期貨、選擇權來源：<a href="https://openapi.taifex.com.tw/" target="_blank" rel="noopener noreferrer">臺灣期貨交易所</a>。權證來源：<a href="https://openapi.twse.com.tw/" target="_blank" rel="noopener noreferrer">臺灣證券交易所</a>、<a href="https://www.tpex.org.tw/openapi/" target="_blank" rel="noopener noreferrer">證券櫃檯買賣中心</a>。美國來源：<a href="https://www.cftc.gov/MarketReports/CommitmentsofTraders/index.htm" target="_blank" rel="noopener noreferrer">CFTC COT</a>、<a href="https://www.cboe.com/markets/us/options/market-statistics/daily" target="_blank" rel="noopener noreferrer">Cboe 每日統計</a>、<a href="https://www.cboe.com/markets/us/futures/market-statistics/settlement/futures/daily" target="_blank" rel="noopener noreferrer">CFE 結算價</a>。本站為資料整理與計算結果。</p><p>法人歷史從本站啟用後累積最近 60 個資料日，P/C 比另含官方本次提供的歷史。一般交易時段的未平倉不與夜盤加總；前十大、前五大與特定法人數字也不可相加。</p><p>期貨商交易量反映交易通路的活躍度，無法據此推定多空方向。選擇權買權、賣權均有買賣雙方；本站不把賣權買方淨部位標成多單。</p><p>CFTC 通常美東週五 15:30 公布截至週二的部位，假期可能延後。金融 TFF 與商品 Disaggregated 分類不同；期權合併採期貨等值口數，不是選擇權單獨持倉。Cboe P/C 比以倍數呈現，台灣 P/C 比以百分比呈現；各自保留口徑。</p><p>行情與籌碼均非即時。權證可能無成交或提前停止交易，篩選結果不構成交易建議。</p></section>`;
}

function bind(kind) {
  const rerender = () => render();
  if (kind === 'daily-watch') { dailyWatch.bind(rerender); return; }
  if (kind === 'warrant-daily') { warrantDaily.bind(rerender); return; }
  if (kind === 'warrant-spreads') { spreadWatch.bind(rerender); return; }
  if (kind === 'warrant-branches') { branches.bind(rerender); return; }
  if (kind.startsWith('us-')) { us.bind(rerender, kind === 'us-options'); return; }
  for (const [id, key] of [['inst-contract', kind + 'Inst'], ['inst-date', kind + 'Date'], ['large-contract', kind + 'Large'], ['large-month', kind + 'Month'], ['fcm-contract', kind + 'Fcm'], ['quote-contract', kind + 'Quote'], ['option-expiry', 'optionExpiry']]) {
    const el = $('#' + id);
    if (el) el.addEventListener('change', () => { state[key] = el.value; state.quotePage = 0; rerender(); });
  }
  document.querySelectorAll('[data-quote-page]').forEach(el => el.addEventListener('click', () => { state.quotePage += Number(el.dataset.quotePage); rerender(); }));
  if (kind !== 'warrants') return;
  const apply = () => {
    state.warrant = { underlying: state.warrant.underlying, style: $('#w-style').value, search: $('#w-search').value, market: $('#w-market').value, cp: $('#w-cp').value, minVolume: $('#w-volume').value,
      minDays: $('#w-days').value, maxPrice: $('#w-price').value, sort: $('#w-sort').value, active: $('#w-active').checked };
    state.page = 0; rerender();
  };
  const selectUnderlying = () => {
    const code = resolveWarrantUnderlying(warrantChoices, $('#w-underlying').value);
    if (!code) {
      $('#w-underlying-error').textContent = '在官方權證清單中找不到唯一標的，請輸入完整代碼／名稱，或從建議清單選擇。';
      $('#w-underlying').setAttribute('aria-invalid', 'true'); return;
    }
    state.warrant = { underlying: code, active: false, minVolume: '', sort: 'expiry' }; state.page = 0;
    const next = `#warrants?underlying=${encodeURIComponent(code)}`;
    if (location.hash !== next) location.hash = next; else rerender();
  };
  $('#w-underlying-apply').addEventListener('click', selectUnderlying);
  $('#w-underlying').addEventListener('keydown', e => { if (e.key === 'Enter') selectUnderlying(); });
  $('#w-all-market').addEventListener('click', () => {
    state.warrant = { active: true, minVolume: '', sort: 'volume' }; state.page = 0;
    if (location.hash !== '#warrants') location.hash = '#warrants'; else rerender();
  });
  $('#w-apply').addEventListener('click', apply);
  $('#w-search').addEventListener('keydown', event => { if (event.key === 'Enter') apply(); });
  for (const id of ['w-market', 'w-cp', 'w-style', 'w-sort', 'w-active']) $('#' + id).addEventListener('change', apply);
  $('#w-reset').addEventListener('click', () => { state.warrant = { underlying: state.warrant.underlying, active: !state.warrant.underlying, minVolume: '', sort: state.warrant.underlying ? 'expiry' : 'volume' }; state.page = 0; rerender(); });
  document.querySelectorAll('[data-w-page]').forEach(el => el.addEventListener('click', () => { state.page += Number(el.dataset.wPage); rerender(); }));
  $('#w-export').addEventListener('click', () => {
    const fields = [['code', '權證代號'], ['name', '名稱'], ['market', '市場'], ['cp', '權別'], ['underlying', '標的代號'], ['underlyingName', '標的名稱'], ['price', '收盤價'], ['volume', '成交量（張）'], ['value', '成交金額（元）'], ['strike', '履約價'], ['ratio', '行使比例'], ['exerciseStyle', '履約方式'], ['expiry', '到期日／履約截止日'], ['lastTrade', '最後交易日'], ['date', '行情資料日'], ['termsDate', '條款資料日']];
    const url = URL.createObjectURL(new Blob([toCSV(exportRows, fields)], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a'); a.href = url; a.download = `warrants-${taipeiToday()}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

async function render() {
  const token = ++generation;
  let route = location.hash.slice(1).split('?')[0];
  if (!(route in pages)) route = 'overview';
  const [title, description] = pages[route];
  $('#page-title').innerHTML = `${title}<span>.</span>`;
  $('#page-description').textContent = description;
  $('#breadcrumb').textContent = title;
  document.querySelector('.topbar .tag').textContent = route === 'warrant-branches' ? '分點每日 20:13 更新' : '行情每日 4 次更新';
  document.querySelectorAll('[data-nav]').forEach(a => { a.classList.toggle('active', a.dataset.nav === route); if (a.dataset.nav === route) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  try {
    if (!manifest) manifest = await load('manifest');
    const scope = route.startsWith('us-') ? manifest.sources.filter(s => s.region === 'US') : ['futures', 'options', 'warrants', 'warrant-branches', 'daily-watch', 'warrant-daily', 'warrant-spreads'].includes(route) ? manifest.sources.filter(s => s.region !== 'US') : manifest.sources;
    const old = scope.filter(s => isStaleSource(s));
    const failed = scope.filter(s => s.status !== 'ok');
    $('#notice').classList.toggle('warning', !!(failed.length || old.length));
    $('#notice').innerHTML = failed.length || old.length ? `${failed.length ? `${failed.length} 個來源更新異常。` : ''}${old.length ? `${old.length} 個來源資料較舊（日資料超過 4 天／週報超過 14 天，可能含休市日）。` : ''} 已保留最近成功值。<a href="#sources">查看資料狀態</a>` : `官方資料已載入。日資料與 CFTC 週報依原始頻率呈現；各表分別標示資料日期。`;
    if (route === 'warrant-branches') { $('#notice').textContent = '富邦證券公開排行／嘉實資訊 · 非全部分點 · 每日台北時間 20:13 更新'; $('#notice').classList.remove('warning'); }
    $('#page-region').textContent = route.startsWith('us-') ? 'UNITED STATES DERIVATIVES' : ['futures', 'options', 'warrants', 'warrant-branches', 'daily-watch', 'warrant-daily', 'warrant-spreads'].includes(route) ? 'TAIWAN DERIVATIVES' : 'TAIWAN + UNITED STATES';
    const html = route === 'quality' ? governancePage(...await Promise.all([load('data-quality').catch(() => null), load('warrant-spreads').catch(() => null)])) : route === 'overview' ? await overview() : ['futures', 'options'].includes(route) ? await positioning(route) : route === 'us-futures' ? await us.futures() : route === 'us-options' ? await us.options() : route === 'warrants' ? await warrants() : route === 'warrant-branches' ? await branches.page() : route === 'warrant-daily' ? await warrantDaily.page() : route === 'daily-watch' ? await dailyWatch.page() : route === 'warrant-spreads' ? await spreadWatch.page() : await sources();
    if (token !== generation) return;
    $('#content').innerHTML = html;
    bind(route);
  } catch (error) {
    if (token !== generation) return;
    $('#content').innerHTML = empty('資料暫時無法載入，請點選「載入最新資料」重試。');
    $('#notice').textContent = '連線或資料檔暫時異常；沒有以示範行情替代。';
    $('#notice').classList.add('warning');
    console.error(error);
  }
}
window.addEventListener('hashchange', () => { state.quotePage = 0; window.scrollTo(0, 0); render(); });
$('#refresh').addEventListener('click', async () => { $('#refresh').disabled = true; cache.clear(); manifest = null; state.futuresDate = undefined; state.optionsDate = undefined; delete state['cot-futuresDate']; delete state['cot-combinedDate']; await render(); $('#refresh').disabled = false; });
render();
