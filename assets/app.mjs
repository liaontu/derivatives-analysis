import { finite, fmt, esc, cpLabel, warrantCp, taipeiToday, daysUntil, filterWarrants, oiDistribution, previousOi, toCSV } from './model.mjs';

const $ = selector => document.querySelector(selector);
const cache = new Map();
let manifest, generation = 0, exportRows = [];
const state = { page: 0, warrant: { active: true, minVolume: '1', sort: 'volume' } };
const pages = {
  overview: ['市場總覽', '把行情、部位與交易熱度，放在同一個視野。'],
  futures: ['期貨籌碼追蹤', '法人多空部位、大額交易人與期貨商成交熱度。'],
  options: ['選擇權籌碼追蹤', '從買賣權部位、履約價與 Put/Call 比，觀察市場結構。'],
  warrants: ['權證行情篩選', '以標的、權別、價格、成交量與剩餘天數，縮小研究範圍。'],
  sources: ['資料與更新', '每項數據的來源、資料日期與更新狀態，都可以追溯。'],
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

function lineChart(points, label, percent = false) {
  const valid = points.filter(p => finite(p.value));
  if (valid.length < 2) return empty(`已累積 ${valid.length} 個資料日；滿兩日後顯示趨勢。每日自動累積，最多保留 60 個資料日。`);
  const width = 700, height = 230, low = Math.min(...valid.map(p => p.value)), high = Math.max(...valid.map(p => p.value));
  const range = high - low || Math.max(1, Math.abs(high) * .02), x = i => 55 + i / (valid.length - 1) * 620, y = v => 178 - (v - low) / range * 145;
  const path = valid.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' ');
  return `<div class="chart"><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label)}"><title>${esc(label)}</title>${[low, low + range / 2, low + range].map(v => `<line x1="55" x2="680" y1="${y(v)}" y2="${y(v)}" stroke="#2c4050"/><text x="48" y="${y(v) + 4}" text-anchor="end">${fmt(v, percent ? 1 : 0)}${percent ? '%' : ''}</text>`).join('')}<path d="${path}" stroke="#77e2d0" stroke-width="2.4" fill="none"/>${valid.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.value)}" r="2.7" fill="#77e2d0"><title>${esc(p.date)}：${fmt(p.value, 2)}${percent ? '%' : ''}</title></circle>`).join('')}<text x="55" y="209">${esc(valid[0].date)}</text><text x="680" y="209" text-anchor="end">${esc(valid.at(-1).date)}</text></svg></div>`;
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
  return `<div class="metrics">${metric('臺股期貨・近月結算', fmt(front?.price, 2), `${front?.month || '—'} · ${futures.date || '尚無資料'}`)}${metric('外資臺股期貨淨未平倉', signed(foreign?.netOi), `口 · ${inst.date || '尚無資料'}`)}${metric('臺指選擇權 P/C 未平倉比', finite(ratio?.oiRatio) ? fmt(ratio.oiRatio, 2) + '%' : '—', `${pcr.date || '尚無資料'} · 全契約`, 'teal')}${metric('上市櫃權證資料', fmt(manifest.warrantCount), '檔 · 篩選頁預設僅顯示未到期且有成交')}</div>
  <div class="split"><section class="panel"><div class="panel-head"><div><span class="eyebrow">POSITIONING TREND</span><h2>臺指選擇權 P/C 比趨勢</h2></div>${dateTag(pcr.date)}</div>${lineChart(points, '臺指選擇權未平倉 Put/Call 比', true)}<p class="note">P/C 比＝賣權未平倉量 ÷ 買權未平倉量 × 100%。每個選擇權部位都有買賣雙方，比例本身不等於市場方向。</p></section><section class="panel"><span class="eyebrow">INSTITUTIONAL POSITION</span><h2>臺股期貨法人淨未平倉</h2><p class="muted">${esc(inst.date || '尚無資料')} · 單位：口</p>${inst.rows.filter(r => r.contract === '臺股期貨').map(r => `<div class="bar-row"><span>${esc(r.investor)}</span><div class="bar-track"><span style="width:${Math.min(100, Math.abs(r.netOi || 0) / Math.max(...inst.rows.filter(i => i.contract === '臺股期貨').map(i => Math.abs(i.netOi || 0)), 1) * 100)}%;background:${r.netOi < 0 ? '#6fd7ac' : '#ff8e99'}"></span></div><strong>${signed(r.netOi)}</strong></div>`).join('')}<p class="note">三類法人分別呈現。淨額是類別內部位互抵的結果，不能代表每一家機構的策略。</p><a href="#futures" class="text-link">查看法人與大額交易人 →</a></section></div>
  <div class="cards"><a href="#futures" class="link-card"><h3>⌁ 期貨籌碼</h3><p>法人多空、前五／十大部位、期貨商成交量</p></a><a href="#options" class="link-card"><h3>◇ 選擇權籌碼</h3><p>買賣權分計、履約價未平倉分布、P/C 比</p></a><a href="#warrants" class="link-card"><h3>▤ 權證篩選</h3><p>上市與上櫃權證、條件交叉篩選、CSV 匯出</p></a></div>
  <section class="panel"><div class="panel-head"><h2>重點期貨行情</h2>${dateTag(futures.date)}</div>${table(['契約', '月份', '結算價', '最後成交價', '成交量（口）', '未平倉（口）'], watch.map(r => tr([esc(contractName(r.contract)), esc(r.month), fmt(r.price, 2), fmt(r.last, 2), fmt(r.volume), fmt(r.oi)])))}<p class="note">一般交易時段；結算價與最後成交價分別標示。價格單位依各商品契約規格。</p></section>`;
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
  const f = state.warrant, rows = filterWarrants(data.rows, f);
  exportRows = rows;
  state.page = Math.min(state.page, Math.max(0, Math.ceil(rows.length / 50) - 1));
  const page = state.page;
  return `<section class="panel"><div class="filters"><label class="search">搜尋代碼、名稱、標的或發行商關鍵字<input id="w-search" type="search" value="${esc(f.search || '')}" placeholder="例如 2330、台積電、元大"></label>${select('w-market', '市場', ['', 'TWSE', 'TPEX'], f.market || '', v => ({ '': '上市＋上櫃', TWSE: '上市', TPEX: '上櫃' }[v]))}${select('w-cp', '權別', ['', 'C', 'P'], f.cp || '', v => v ? warrantCp(v) : '全部')}<label>最低成交量（張）<input id="w-volume" type="number" min="0" step="any" value="${esc(f.minVolume || '')}" placeholder="不限"></label><label>至少剩餘天數<input id="w-days" type="number" min="0" step="1" value="${esc(f.minDays || '')}" placeholder="不限"></label><label>最高收盤價（元）<input id="w-price" type="number" min="0" step="0.01" value="${esc(f.maxPrice || '')}" placeholder="不限"></label>${select('w-sort', '排序', ['volume', 'value', 'expiry', 'price'], f.sort, v => ({ volume: '成交量高至低', value: '成交金額高至低', expiry: '到期日近至遠', price: '價格低至高' }[v]))}<label class="check"><input id="w-active" type="checkbox" ${f.active ? 'checked' : ''}> 排除已到期／已過最後交易日</label><button id="w-apply">套用篩選</button><button id="w-reset">重設</button></div><p class="muted">可搜尋標的股票名稱，但此頁只顯示權證。預設成交量至少 1 張；清空後可查看無成交權證。1 張＝1,000 單位。</p></section>
  <section class="panel"><div class="panel-head"><div><h2>權證篩選結果</h2><p class="muted">${manifest.sources.filter(s => ['twse-warrants', 'tpex-warrants'].includes(s.id)).map(s => `${s.id.startsWith('twse') ? '上市' : '上櫃'} ${esc(s.date || '無資料')}`).join(' · ')} · 剩餘天數以台北今日 ${taipeiToday()} 計算</p></div><button id="w-export" ${rows.length ? '' : 'disabled'}>匯出篩選結果 CSV</button></div>${table(['權證 / 代碼', '市場 / 權別', '標的', '收盤價（元）', '成交量（張）', '成交金額（元）', '履約價', '行使比例', '到期日 / 剩餘天數', '行情資料日'], rows.slice(page * 50, (page + 1) * 50).map(r => tr([`${esc(r.name)}<small>${esc(r.code)}</small>`, `${r.market === 'TWSE' ? '上市' : '上櫃'} / ${warrantCp(r.cp)}`, `${esc(r.underlyingName || '—')}<small>${esc(r.underlying || '')}</small>`, fmt(r.price, 3), fmt(r.volume, 3), fmt(r.value), fmt(r.strike, 3), fmt(r.ratio, 6), `${esc(r.expiry || '—')}<small>${fmt(daysUntil(r.expiry))} 天</small>`, `${esc(r.date)}${r.termsDate && r.termsDate !== r.date ? `<small>條款 ${esc(r.termsDate)}</small>` : ''}`])))}<div class="pagination"><span>${fmt(rows.length)} / ${fmt(data.rows.length)} 檔 · 第 ${page + 1} / ${Math.max(1, Math.ceil(rows.length / 50))} 頁</span><div><button data-w-page="-1" ${page === 0 ? 'disabled' : ''}>上一頁</button><button data-w-page="1" ${(page + 1) * 50 >= rows.length ? 'disabled' : ''}>下一頁</button></div></div><p class="note">「—」代表來源缺值，不代表零。上市成交量與收盤價日期不同時，成交量暫不混用。資料為盤後快照；剩餘天數不等於可交易天數，最後交易日可能早於到期日。</p></section>`;
}

function sources() {
  return `<section class="panel source-list"><span class="eyebrow">FREE DAILY UPDATES</span><h2>每天自動更新，開啟網站就能查</h2><p>台北時間每天 19:43 抓取，22:43 再次更新。休市日保留官方最近資料；排程可能延遲。上方按鈕只重載最近發布的資料，不會觸發額外抓取。</p><p>官方資料無需帳號或金鑰。網站使用免費 GitHub Pages 與公開儲存庫標準執行器，不使用付費行情、付費伺服器或自訂網域。</p><a href="https://github.com/liaontu/derivatives-analysis/actions/workflows/derivatives-daily.yml" target="_blank" rel="noopener noreferrer">查看每日更新執行紀錄 ↗</a><div class="coverage">涵蓋台灣期貨與選擇權官方每日行情、三大法人、大額交易人、各期貨商成交量，以及上市櫃權證。法人與期貨商統計均有各自口徑；沒有營業分點買賣明細。</div></section>
  <section class="panel"><div class="panel-head"><h2>各來源狀態</h2><span class="muted">最近檢查 ${esc(new Date(manifest.checkedAt).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false }))}（台北）</span></div>${table(['來源', '資料日', '筆數', '狀態'], manifest.sources.map(s => tr([`<a class="text-link" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.label)} ↗</a>${s.error ? '<small>來源暫時異常，等待下一次排程重試</small>' : ''}`, esc(s.date || '—'), fmt(s.count), `<span class="${s.status === 'ok' ? 'status-ok' : 'status-bad'}">${{ ok: '取得成功', retained: '更新失敗・保留舊值', unavailable: '暫無資料' }[s.status] || esc(s.status)}</span>`])))}<p class="note">資料日期取自來源；檢查時間不會代替資料日期。來源失敗會保留最後成功資料並在網站與工作流程標示。超過 4 個日曆日未更新會顯示提醒，連假也可能觸發。</p></section>
  <section class="panel source-list"><h2>資料口徑與使用說明</h2><p>期貨、選擇權來源：<a href="https://openapi.taifex.com.tw/" target="_blank" rel="noopener noreferrer">臺灣期貨交易所</a>。權證來源：<a href="https://openapi.twse.com.tw/" target="_blank" rel="noopener noreferrer">臺灣證券交易所</a>、<a href="https://www.tpex.org.tw/openapi/" target="_blank" rel="noopener noreferrer">證券櫃檯買賣中心</a>。本站為資料整理與計算結果。</p><p>法人歷史從本站啟用後累積最近 60 個資料日，P/C 比另含官方本次提供的歷史。一般交易時段的未平倉不與夜盤加總；前十大、前五大與特定法人數字也不可相加。</p><p>期貨商交易量反映交易通路的活躍度，無法據此推定多空方向。選擇權買權、賣權均有買賣雙方；本站不把賣權買方淨部位標成多單。</p><p>行情與籌碼均非即時。權證可能無成交或提前停止交易，篩選結果不構成交易建議。</p></section>`;
}

function bind(kind) {
  const rerender = () => render();
  for (const [id, key] of [['inst-contract', kind + 'Inst'], ['inst-date', kind + 'Date'], ['large-contract', kind + 'Large'], ['large-month', kind + 'Month'], ['fcm-contract', kind + 'Fcm'], ['quote-contract', kind + 'Quote'], ['option-expiry', 'optionExpiry']]) {
    const el = $('#' + id);
    if (el) el.addEventListener('change', () => { state[key] = el.value; state.quotePage = 0; rerender(); });
  }
  document.querySelectorAll('[data-quote-page]').forEach(el => el.addEventListener('click', () => { state.quotePage += Number(el.dataset.quotePage); rerender(); }));
  if (kind !== 'warrants') return;
  const apply = () => {
    state.warrant = { search: $('#w-search').value, market: $('#w-market').value, cp: $('#w-cp').value, minVolume: $('#w-volume').value,
      minDays: $('#w-days').value, maxPrice: $('#w-price').value, sort: $('#w-sort').value, active: $('#w-active').checked };
    state.page = 0; rerender();
  };
  $('#w-apply').addEventListener('click', apply);
  $('#w-search').addEventListener('keydown', event => { if (event.key === 'Enter') apply(); });
  for (const id of ['w-market', 'w-cp', 'w-sort', 'w-active']) $('#' + id).addEventListener('change', apply);
  $('#w-reset').addEventListener('click', () => { state.warrant = { active: true, minVolume: '1', sort: 'volume' }; state.page = 0; rerender(); });
  document.querySelectorAll('[data-w-page]').forEach(el => el.addEventListener('click', () => { state.page += Number(el.dataset.wPage); rerender(); }));
  $('#w-export').addEventListener('click', () => {
    const fields = [['code', '權證代號'], ['name', '名稱'], ['market', '市場'], ['cp', '權別'], ['underlying', '標的代號'], ['underlyingName', '標的名稱'], ['price', '收盤價'], ['volume', '成交量（張）'], ['value', '成交金額（元）'], ['strike', '履約價'], ['ratio', '行使比例'], ['expiry', '到期日'], ['lastTrade', '最後交易日'], ['date', '行情資料日'], ['termsDate', '條款資料日']];
    const url = URL.createObjectURL(new Blob([toCSV(exportRows, fields)], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a'); a.href = url; a.download = `warrants-${taipeiToday()}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}

async function render() {
  const token = ++generation;
  let route = location.hash.slice(1);
  if (!(route in pages)) route = 'overview';
  const [title, description] = pages[route];
  $('#page-title').innerHTML = `${title}<span>.</span>`;
  $('#page-description').textContent = description;
  $('#breadcrumb').textContent = title;
  document.querySelectorAll('[data-nav]').forEach(a => { a.classList.toggle('active', a.dataset.nav === route); if (a.dataset.nav === route) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current'); });
  try {
    if (!manifest) manifest = await load('manifest');
    const old = manifest.sources.filter(s => s.date && daysUntil(s.date) < -4);
    const failed = manifest.sources.filter(s => s.status !== 'ok');
    $('#notice').classList.toggle('warning', !!(failed.length || old.length));
    $('#notice').innerHTML = failed.length || old.length ? `${failed.length ? `${failed.length} 個來源更新異常。` : ''}${old.length ? `${old.length} 個來源資料超過 4 天（可能含休市日）。` : ''} 已保留最近成功值。<a href="#sources">查看資料狀態</a>` : `官方盤後資料已載入。最新資料日 ${esc([...manifest.sources.map(s => s.date).filter(Boolean)].sort().at(-1) || '—')}；各表分別標示日期。`;
    const html = route === 'overview' ? await overview() : ['futures', 'options'].includes(route) ? await positioning(route) : route === 'warrants' ? await warrants() : sources();
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
window.addEventListener('hashchange', () => { state.quotePage = 0; render(); });
$('#refresh').addEventListener('click', async () => { $('#refresh').disabled = true; cache.clear(); manifest = null; state.futuresDate = undefined; state.optionsDate = undefined; await render(); $('#refresh').disabled = false; });
render();
