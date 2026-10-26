import { esc, fmt, finite, warrantUnderlyings, resolveWarrantUnderlying, warrantCp, warrantStyle, daysUntil, taipeiToday, toCSV } from './model.mjs';
import {loadWarrants} from './catalog.mjs';
import { change } from './watch.mjs';

const route = code => `#warrant-daily?underlying=${encodeURIComponent(code)}`;
const validDay = day => /^\d{4}-\d{2}-\d{2}$/.test(day || '') && Number.isFinite(Date.parse(day + 'T00:00:00Z')) && new Date(day + 'T00:00:00Z').toISOString().slice(0, 10) === day;
const positive = n => finite(n) && n > 0;
const nonnegative = n => finite(n) && n >= 0;
const signed = (value, unit = '', digits = 2) => finite(value) ? `<span class="${value > 0 ? 'positive' : value < 0 ? 'negative' : ''}">${value > 0 ? '+' : ''}${fmt(value, digits)}${unit}</span>` : '—';

// Only reuse strict pairs whose dated inputs still match the displayed snapshot.
export function dailyPeers(catalog, spreads, index) {
  const result = new Map(), byCode = new Map(catalog.map(r => [r.code, r]));
  if (index?.status !== 'ok' || spreads?.status !== 'ok') return result;
  for (const p of spreads.pairs || []) {
    const a = byCode.get(p.a.code), b = byCode.get(p.b.code);
    const point = p.points.find(r => r[0] === spreads.date);
    if (!a || !b || !point || p.latest?.date !== spreads.date || !finite(p.latest?.spread)) continue;
    if (![a, b].every((r, i) => r.date === spreads.date && r.termsDate === r.date && r.underlying === p.underlying && r.cp === p.cp && r.strike === p.strike && r.expiry === p.expiry && warrantStyle(r.style) === p.style && r.ratio === (i ? p.b : p.a).ratio && positive(r.ratio) && positive(r.price) && positive(r.volume) && r.price === point[i + 1] && r.volume === point[i + 3])) continue;
    const gap = 200 * (a.price / a.ratio - b.price / b.ratio) / (a.price / a.ratio + b.price / b.ratio);
    for (const [own, peer, spread] of [[a, b, gap], [b, a, -gap]]) {
      if (!result.has(own.code)) result.set(own.code, []);
      result.get(own.code).push({ code: peer.code, name: peer.name, spread, pair: p.id });
    }
  }
  for (const [code, peers] of result) result.set(code, peers.sort((a, b) => Math.abs(b.spread) - Math.abs(a.spread) || a.code.localeCompare(b.code)));
  return result;
}

export function observationRows(catalog, history, spreads, spreadIndex, sources = [], today = taipeiToday()) {
  const peers = dailyPeers(catalog, spreads, spreadIndex);
  const prices = new Map((history?.underlyingPrices || []).map(r => [r[0], r[1]]));
  return catalog.map(r => {
    const saved = (history?.quotes?.[r.code]?.rows || []).filter(p => p[0] <= r.date).slice().sort((a, b) => a[0].localeCompare(b[0]));
    const previous = saved.filter(p => p[0] < r.date).at(-1), current = saved.find(p => p[0] === r.date);
    const price = change(nonnegative(r.price) ? r.price : null, nonnegative(previous?.[1]) ? previous[1] : null);
    const volume = change(nonnegative(r.volume) ? r.volume : null, nonnegative(previous?.[2]) ? previous[2] : null);
    const underlyingPrice = prices.get(r.date), underlyingPrevious = prices.get(previous?.[0]);
    const underlyingChange = change(positive(underlyingPrice) ? underlyingPrice : null, positive(underlyingPrevious) ? underlyingPrevious : null);
    const days = validDay(r.expiry) ? daysUntil(r.expiry, today) : null;
    const stopped = (finite(days) && days < 0) || (validDay(r.lastTrade) && daysUntil(r.lastTrade, today) < 0);
    const checks = [
      ['當筆權證價格', nonnegative(r.price)], ['前筆權證價格大於零', positive(previous?.[1])],
      ['當筆成交量', nonnegative(r.volume)], ['前筆成交量大於零，可算量變率', positive(previous?.[2])],
      ['同日標的價格', positive(underlyingPrice)], ['同比較日標的價格', positive(underlyingPrevious)],
      ['同日完整基本條款', r.termsDate === r.date && validDay(r.expiry) && positive(r.strike) && positive(r.ratio) && ['歐式', '美式'].includes(warrantStyle(r.style)) && ['C', 'P'].includes(r.cp)],
      ['歷史與本次價格／量一致', !!current && current[1] === r.price && current[2] === r.volume],
    ];
    const ids = r.market === 'TWSE' ? ['twse-warrants', r.cp === 'P' ? 'twse-volume-put' : 'twse-volume'] : ['tpex-warrants', 'tpex-terms', 'tpex-underlyings'];
    const warnings = ids.filter(id => !sources.some(s => s.id === id && s.status === 'ok')).map(id => `${id} 更新未確認／異常`);
    for (const id of ids) {
      const source = sources.find(s => s.id === id);
      if (source?.date && source.date !== r.date) warnings.push(`${id} 資料日 ${source.date} 與行情日不同`);
    }
    if (!validDay(r.date)) warnings.push('行情日期缺漏');
    else if (daysUntil(r.date, today) < -4) warnings.push('行情距今超過 4 天（可能含休市）');
    if (r.volume === 0) warnings.push('當筆零成交，收盤價不視為可成交價格');
    if (previous?.[2] === 0) warnings.push('前量為零：僅列張數增減，量變率不計');
    if (stopped) warnings.push('已到期／已過最後交易日');
    const matches = peers.get(r.code) || [], peer = matches[0];
    const spreadReason = peer ? '' : spreadIndex?.status !== 'ok' || !spreads || spreads.status !== 'ok' ? '價差資料未取得／本次更新不足' : '無同日有效配對（條件、成交量或資料不符）';
    return { ...r, style: warrantStyle(r.style), previousDate: previous?.[0] || '', priceDelta: price.delta, pricePct: price.pct,
      volumeDelta: volume.delta, volumePct: volume.pct, underlyingPrice, underlyingPrevious, underlyingDelta: underlyingChange.delta, underlyingPct: underlyingChange.pct,
      days, stopped, historyDays: saved.length, peer, peerCount: matches.length, spread: peer?.spread ?? null, spreadReason,
      checks, complete: checks.filter(c => c[1]).length, warnings };
  });
}

export const observationSorts = { volume: '成交量高至低', pricePct: '權證漲幅高至低', 'pricePct-asc': '權證漲幅低至高', volumePct: '量變率高至低', spread: '絕對價差大至小', days: '剩餘天數近至遠', strike: '履約價低至高', complete: '完整欄位多至少' };
export function filterObservations(rows, filters) {
  const query = (filters.search || '').trim().toLowerCase();
  const sort = Object.hasOwn(observationSorts, filters.sort) ? filters.sort : 'volume';
  const key = sort.replace('-asc', ''), asc = ['days', 'strike', 'pricePct-asc'].includes(sort);
  return rows.filter(r => (!filters.active || !r.stopped) && (!filters.cp || r.cp === filters.cp) && (!filters.paired || r.peer) && (!query || `${r.code} ${r.name}`.toLowerCase().includes(query))).sort((a, b) => {
    const av = key === 'spread' && finite(a.spread) ? Math.abs(a.spread) : a[key], bv = key === 'spread' && finite(b.spread) ? Math.abs(b.spread) : b[key];
    if (!finite(av)) return !finite(bv) ? a.code.localeCompare(b.code) : 1;
    if (!finite(bv)) return -1;
    return (av - bv) * (asc ? 1 : -1) || a.code.localeCompare(b.code);
  });
}

export function observationExport(rows, today = taipeiToday()) {
  return rows.map(r => ({ ...r, asOf: today, cp: warrantCp(r.cp), peerCode: r.peer?.code, pairId: r.peer?.pair,
    completeness: `${r.complete}/8`, missing: r.checks.filter(c => !c[1]).map(c => c[0]).join('；'), warnings: r.warnings.join('；'),
    comparison: '較前筆已保存資料；非保證連續交易日；未還原收盤價', spreadBasis: '同日基本條件配對；行使比例換算；選絕對價差最大對象；非套利報酬' }));
}
export const observationFields = [['underlying', '標的代碼'], ['code', '權證代碼'], ['name', '權證名稱'], ['market', '市場'], ['date', '行情日'], ['previousDate', '比較日'], ['price', '權證收盤元'], ['priceDelta', '權證價變元'], ['pricePct', '權證漲跌%'], ['underlyingPrice', '同日標的收盤元或點'], ['underlyingPrevious', '同比較日標的收盤'], ['underlyingDelta', '標的價變'], ['underlyingPct', '標的漲跌%'], ['volume', '權證成交量張'], ['volumeDelta', '成交量增減張'], ['volumePct', '成交量變化%'], ['cp', '權別'], ['strike', '履約價'], ['expiry', '到期日'], ['lastTrade', '最後交易日'], ['style', '履約方式'], ['ratio', '行使比例'], ['termsDate', '條款日'], ['asOf', '天數計算基準日'], ['days', '剩餘日曆天數'], ['peerCode', '比較權證'], ['peerCount', '有效配對數'], ['spread', '本檔相對價差%'], ['pairId', '配對識別'], ['spreadReason', '無價差原因'], ['historyDays', '已保存資料日數'], ['completeness', '可用欄位數'], ['missing', '不足欄位'], ['warnings', '資料提醒'], ['comparison', '漲跌口徑'], ['spreadBasis', '價差口徑']];

export function createWarrantDaily({ load, table, tr, select, metric, empty }) {
  const state = { underlying: '', page: 0, sort: 'volume', cp: '', search: '', active: true, paired: false };
  let choices = [], exported = [];
  async function page() {
    const requested = new URLSearchParams(location.hash.split('?')[1] || '').get('underlying') || '';
    const [all, manifest, index] = await Promise.all([loadWarrants(load, {underlying: requested}), load('manifest'), load('warrant-spreads').catch(() => null)]);
    choices = all.choices;
    const code = new URLSearchParams(location.hash.split('?')[1] || '').get('underlying') || (choices.some(c => c.code === '2330') ? '2330' : choices[0]?.code || '');
    const selected = choices.find(c => c.code === code), catalog = all.rows.filter(r => r.underlying === code);
    if (state.underlying !== code) Object.assign(state, { underlying: code, page: 0, cp: '', search: '', paired: false });
    const controls = `<section class="panel"><div class="panel-head"><div><span class="eyebrow">DAILY WARRANT MONITOR</span><h2>一張表，對照同標的權證</h2><p class="muted">最新收盤快照 · 較前筆已保存資料 · 每日免費更新</p></div><a class="text-link" href="#warrant-focus">全市場每日重點 →</a></div><div class="filters compact"><label>標的代碼或完整名稱<input id="wd-underlying" list="wd-underlyings" value="${esc(code)}" placeholder="2330 或 台積電"><datalist id="wd-underlyings">${choices.map(c => `<option value="${esc(c.label)}"></option>`).join('')}</datalist></label><button id="wd-find">查看個股權證</button></div><p id="wd-error" class="status-bad" role="status"></p><p class="note">整合目前官方清單所列的上市櫃權證，不限分點排行前 300 檔。選擇標的只用來比較權證與其標的價格；點選權證可查看逐日趨勢及已有的分點排行。</p></section>`;
    if (!selected) { exported = []; return controls + empty('找不到此標的，請輸入完整代碼或名稱。'); }
    const [history, spreads] = await Promise.all([load(`warrant-history/${code}`).catch(() => null), index?.items?.some(i => i.code === code) ? load(`warrant-spreads/${code}`).catch(() => null) : null]);
    const today = taipeiToday(), allRows = observationRows(catalog, history, spreads, index, manifest.sources, today), rows = filterObservations(allRows, state);
    exported = observationExport(rows, today);
    state.page = Math.max(0, Math.min(state.page, Math.ceil(rows.length / 40) - 1));
    const dates = [...new Set(catalog.map(r => r.date))].sort();
    const filters = `<section class="panel"><div class="panel-head"><div><h2>${esc(selected.label)}</h2><p class="muted">行情日 ${dates.map(esc).join('、')} · 今日 ${today} · 清單 ${fmt(catalog.length)} 檔</p></div><a class="text-link" href="#warrant-spreads?underlying=${encodeURIComponent(code)}">同條件價差與回測 →</a></div><div class="filters"><label>權證代碼或名稱<input id="wd-search" value="${esc(state.search)}" placeholder="搜尋本標的權證"></label>${select('wd-cp', '權別', ['', 'C', 'P'], state.cp, v => v ? warrantCp(v) : '全部')}${select('wd-sort', '排序', Object.keys(observationSorts), state.sort, v => observationSorts[v])}<label class="check"><input id="wd-active" type="checkbox" ${state.active ? 'checked' : ''}> 排除已到期／停止交易</label><label class="check"><input id="wd-paired" type="checkbox" ${state.paired ? 'checked' : ''}> 只看有效配對</label><button id="wd-apply">套用篩選</button></div></section>`;
    const metrics = `<div class="metrics">${metric('符合目前條件', fmt(rows.length), `檔 · 全清單 ${catalog.length} 檔`)}${metric('兩者漲跌可比較', fmt(rows.filter(r => finite(r.pricePct) && finite(r.underlyingPct)).length), '權證與標的使用相同起訖日')}${metric('有同日有效價差', fmt(rows.filter(r => r.peer).length), '檔 · 相同基本履約條件')}${metric('8 項欄位齊備', fmt(rows.filter(r => r.complete === 8).length), '不代表報價、成本或套利資料完整')}</div>`;
    const issues = !history ? '<div class="notice warning">歷史資料暫時無法載入；仍列當筆行情，漲跌與歷史比較保留缺值。</div>' : '';
    const dataTable = `<section class="panel daily-observation"><div class="panel-head"><div><h2>個股權證每日觀察表</h2><p class="muted">${fmt(rows.length)} 檔 · ${esc(observationSorts[state.sort])} · 可左右捲動完整欄位</p></div><button id="wd-export" ${rows.length ? '' : 'disabled'}>匯出觀察表 CSV</button></div>${table(['權證 / 代碼', '行情日 / 比較日', '權證收盤 / 漲跌', '標的收盤 / 漲跌', '成交量 / 增減', '量變率', '同條件價差', '到期 / 剩餘天數', '履約條件', '資料完整度'], rows.slice(state.page * 40, (state.page + 1) * 40).map(r => tr([
      `<a class="text-link" href="#daily-watch?code=${encodeURIComponent(r.code)}">${esc(r.name)}<small>${esc(r.code)} · ${r.market === 'TWSE' ? '上市' : '上櫃'} / ${warrantCp(r.cp)}</small></a>${r.stopped ? '<small class="status-bad">已到期／停止交易</small>' : ''}`,
      `${esc(r.date)}<small>較 ${esc(r.previousDate || '尚無前筆')}</small>`,
      `${fmt(r.price, 3)}<small>${signed(r.pricePct, '%')} · ${signed(r.priceDelta, ' 元', 3)}</small>`,
      `${fmt(r.underlyingPrice, 3)}<small>${signed(r.underlyingPct, '%')} · ${signed(r.underlyingDelta, '', 3)}</small>`,
      `${fmt(r.volume, 3)} 張<small>${signed(r.volumeDelta, ' 張', 3)}</small>`, signed(r.volumePct, '%'),
      r.peer ? `<a class="text-link" href="#warrant-spreads?underlying=${encodeURIComponent(code)}&pair=${encodeURIComponent(r.peer.pair)}">${signed(r.spread, '%', 3)}<small>對 ${esc(r.peer.code)} · 共 ${r.peerCount} 組</small></a>` : `—<small class="wd-reason">${esc(r.spreadReason)}</small>`,
      `${esc(r.expiry || '—')}<small>${fmt(r.days)} 天 · 最後交易 ${esc(r.lastTrade || '未提供')}</small>`,
      `K ${fmt(r.strike, 3)} · ${esc(r.style)}<small>比例 ${fmt(r.ratio, 6)} · 條款 ${esc(r.termsDate || '未提供')}</small>`,
      `<details class="wd-quality"><summary class="${r.complete === 8 && !r.warnings.length ? 'status-ok' : 'status-bad'}">${r.complete}/8 項 · ${r.warnings.length ? '有提醒' : r.complete === 8 ? '欄位齊備' : '有缺項'}<small>歷史 ${r.historyDays} 個資料日 · 展開原因</small></summary><ul>${r.checks.map(([name, ok]) => `<li>${ok ? '✓' : '—'} ${esc(name)}</li>`).join('')}${r.warnings.map(w => `<li class="status-bad">${esc(w)}</li>`).join('')}</ul></details>`,
    ])))}<div class="pagination"><span>第 ${state.page + 1} / ${Math.max(1, Math.ceil(rows.length / 40))} 頁 · 每頁 40 檔</span><div><button data-wd-page="-1" ${state.page === 0 ? 'disabled' : ''}>上一頁</button><button data-wd-page="1" ${(state.page + 1) * 40 >= rows.length ? 'disabled' : ''}>下一頁</button></div></div></section>`;
    const method = `<section class="panel source-list"><h2>如何讀這張表</h2><p><strong>漲跌與成交量：</strong>比較該權證前一筆已保存資料，表中列出起訖日期，可能跨休市或缺漏日。標的價格只取這兩個相同日期，缺值不挪用其他日期。價格未還原，除權息及條款調整可能影響比較。前價／前量為零不計百分比；前量為零仍可列張數增減。</p><p><strong>同條件價差：</strong>沿用同日、同標的、同權別、同履約價、同到期日、同歐式／美式的有效配對，兩檔價格及成交量須大於零。換算價格＝權證收盤價 ÷ 行使比例；價差＝200 ×（本檔換算價 − 對照檔換算價）÷ 兩者換算價合計。有多個對象時列出<strong>絕對價差最大</strong>的一組；正值為本檔較高、負值為本檔較低。點選可看完整配對研究。這不是折溢價、公允價或可成交套利報酬。</p><p><strong>剩餘天數：</strong>到期日減台北今日 ${today} 的日曆天數，並列最後交易日；不是剩餘交易日數。預設排除已到期或已過最後交易日，取消勾選即可看來源清單內的全部權證。</p><p><strong>資料完整度：</strong>8 項檢查涵蓋本筆／前筆權證價格與成交量、相同日期的標的價格、當日基本條款及歷史快照一致性。展開可看缺項及來源更新提醒；8/8 不代表有同條件配對或具備交易所需的全部資料。買賣報價、掛單量、IV／Delta、完整結算條款及成本仍未評估。</p><p>免費日排程於台北 08:43、11:43、19:43、22:43 檢查官方來源；平台排程可能延遲。此表直接讀取同一批行情、最近 60 個資料日價格歷史及既有價差資料；來源失敗沿用最後成功資料，日期及提醒會保留。<a href="#sources">查看來源與時間 →</a></p></section>`;
    return controls + filters + issues + metrics + dataTable + method;
  }
  function bind(render) {
    const $ = id => document.getElementById(id);
    const find = () => {
      const code = resolveWarrantUnderlying(choices, $('wd-underlying').value);
      if (!code) { $('wd-error').textContent = '請輸入完整標的代碼或唯一名稱。'; return; }
      if (location.hash === route(code)) render(); else location.hash = route(code);
    };
    $('wd-find').addEventListener('click', find);
    $('wd-underlying').addEventListener('keydown', e => { if (e.key === 'Enter') find(); });
    const apply = () => { Object.assign(state, { search: $('wd-search').value.trim(), cp: $('wd-cp').value, sort: $('wd-sort').value, active: $('wd-active').checked, paired: $('wd-paired').checked, page: 0 }); render(); };
    $('wd-apply')?.addEventListener('click', apply);
    $('wd-search')?.addEventListener('keydown', e => { if (e.key === 'Enter') apply(); });
    document.querySelectorAll('[data-wd-page]').forEach(el => el.addEventListener('click', () => { state.page += Number(el.dataset.wdPage); render(); }));
    $('wd-export')?.addEventListener('click', () => {
      const url = URL.createObjectURL(new Blob([toCSV(exported, observationFields)], { type: 'text/csv;charset=utf-8' }));
      const a = document.createElement('a'); a.href = url; a.download = `warrant-observation-${state.underlying}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  return { page, bind };
}
