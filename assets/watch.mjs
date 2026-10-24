import { esc, fmt, finite, warrantUnderlyings, resolveWarrantUnderlying, warrantCp, warrantStyle, toCSV, daysUntil } from './model.mjs';

export function change(current, previous) {
  const delta = finite(current) && finite(previous) ? current - previous : null;
  return { delta, pct: finite(delta) && previous > 0 ? delta / previous * 100 : null };
}
export function quoteComparison(rows, day) {
  const sorted = [...rows].sort((a, b) => a[0].localeCompare(b[0]));
  const index = sorted.findIndex(r => r[0] === day);
  const current = sorted[index], previous = index > 0 ? sorted[index - 1] : null;
  return { current, previous, ...change(current?.[1], previous?.[1]), volume: change(current?.[2], previous?.[2]) };
}
export function compareBranches(current, previous) {
  const a = new Map((current?.rows || []).map(r => [r.branchCode, r]));
  const b = new Map((previous?.rows || []).map(r => [r.branchCode, r]));
  return [...new Set([...a.keys(), ...b.keys()])].map(code => {
    const now = a.get(code), before = b.get(code), both = !!now && !!before;
    return { code, name: (now || before).branchName, now, before,
      delta: both ? change(now.net, before.net).delta : null,
      rankChange: both && now.side === before.side ? before.rank - now.rank : null,
      status: !previous ? '尚無前筆' : !before ? '前期未列入排行' : !now ? '本期未列入排行' : now.side !== before.side ? '排行方向轉換' : '兩期皆上榜' };
  });
}
const deltaText = (n, suffix = '', digits = 3) => finite(n) ? `${n > 0 ? '+' : ''}${fmt(n, digits)}${suffix}` : '—';
const colored = (n, suffix = '', digits = 3) => `<span class="${n > 0 ? 'positive' : n < 0 ? 'negative' : ''}">${deltaText(n, suffix, digits)}</span>`;
const link = code => `#daily-watch?code=${encodeURIComponent(code)}`;

export function trendChart(rows, column, title, unit, limit) {
  const points = rows.slice(-limit), valid = points.filter(r => finite(r[column]));
  if (valid.length < 2) return `<div class="empty">目前有 ${valid.length} 個有效資料日；累積滿兩日後顯示趨勢。<br>只保留真實來源資料，不回填示範走勢。</div>`;
  const low = Math.min(...valid.map(r => r[column])), high = Math.max(...valid.map(r => r[column]));
  const span = high - low || Math.max(1, Math.abs(low) * .02);
  const x = i => 65 + i / Math.max(1, points.length - 1) * 590, y = n => 170 - (n - low) / span * 130;
  let connected = false;
  const path = points.map((r, i) => {
    if (!finite(r[column])) { connected = false; return ''; }
    const command = connected ? 'L' : 'M'; connected = true;
    return `${command}${x(i)},${y(r[column])}`;
  }).join(' ');
  return `<div class="chart"><svg viewBox="0 0 700 220" role="img" aria-label="${esc(title)}"><title>${esc(title)}（${esc(unit)}），缺值中斷連線</title>${[low, low + span / 2, low + span].map(v => `<line x1="65" x2="655" y1="${y(v)}" y2="${y(v)}" stroke="#2c4050"/><text x="58" y="${y(v) + 4}" text-anchor="end">${fmt(v, 3)}</text>`).join('')}<path d="${path}" stroke="#77e2d0" stroke-width="2.5" fill="none"/>${points.map((r, i) => finite(r[column]) ? `<circle cx="${x(i)}" cy="${y(r[column])}" r="3" fill="#77e2d0"><title>${esc(r[0])}：${fmt(r[column], 3)} ${esc(unit)}</title></circle>` : '').join('')}<text x="65" y="205">${esc(points[0][0])}</text><text x="655" y="205" text-anchor="end">${esc(points.at(-1)[0])}</text></svg></div>`;
}

export function createDailyWatch({ load, table, tr, select, metric, dateTag, empty }) {
  const state = { day: '', branchDay: '', code: '', limit: '20', branch: '', message: '' };
  let catalog = [], choices = [], code = '', csv = [];
  function saved() {
    try { const value = JSON.parse(localStorage.getItem('derivatives-daily-watch') || '[]'); return Array.isArray(value) ? [...new Set(value.filter(c => /^\d{5}[0-9A-Z]$/.test(c)))].slice(0, 20) : []; } catch { return []; }
  }
  async function page() {
    const [all, index] = await Promise.all([load('warrants'), load('warrant-history')]);
    let rankingIndex = null;
    try { rankingIndex = await load('warrant-branches'); } catch { /* Price observations still work. */ }
    catalog = all.rows; choices = warrantUnderlyings(catalog);
    const params = new URLSearchParams(location.hash.split('?')[1] || '');
    const requested = params.get('code');
    const target = params.get('underlying');
    const selected = requested ? catalog.find(r => r.code === requested) : [...catalog].filter(r => !target || r.underlying === target).sort((a, b) => (b.volume || 0) - (a.volume || 0))[0];
    code = selected?.code || '';
    if (state.code !== code) { state.code = code; state.day = ''; state.branchDay = ''; state.branch = ''; state.message = ''; }
    const pins = saved();
    const controls = `<section class="panel"><span class="eyebrow">DAILY OBSERVATION</span><h2>選擇標的與權證</h2><div class="filters watch-controls"><label>標的代碼或完整名稱<input id="dw-underlying" list="dw-underlyings" value="${esc(selected?.underlying || target || '')}" placeholder="2330 或 台積電"><datalist id="dw-underlyings">${choices.map(c => `<option value="${esc(c.code)}">${esc(c.name)}</option>`).join('')}</datalist></label><button id="dw-by-underlying">查看標的權證</button><label>權證代碼或完整名稱<input id="dw-code" list="dw-codes" value="${esc(selected?.code || requested || '')}" placeholder="輸入權證代碼"><datalist id="dw-codes">${catalog.filter(r => r.underlying === selected?.underlying).map(r => `<option value="${esc(r.code)}">${esc(r.name)}</option>`).join('')}</datalist></label><button id="dw-go">查看每日變動</button></div><p id="dw-message" role="status" class="status-bad">${esc(state.message)}</p><div class="watch-pins">${pins.map(c => `<a class="tag ${c === code ? 'selected' : ''}" href="${link(c)}">${esc(c)} ${esc(catalog.find(r => r.code === c)?.name || '已不在最新清單')}</a>`).join('') || '<span class="muted">尚未加入自選。選擇權證後可加入觀察。</span>'}</div><p class="note">自選清單儲存在目前瀏覽器，最多 20 檔；不會跨裝置同步。所有權證行情自動累積；加入自選不會擴大伺服器的分點抓取清單。</p></section>`;
    if (!selected) return controls + empty('找不到此權證。請從標的清單選擇，或輸入完整權證代碼。');
    let history = null, rankings = null, rankingError = false;
    const tracked = rankingIndex?.items.find(i => i.code === code);
    await Promise.all([
      (async () => { if (index.items.some(i => i.code === selected.underlying)) { try { history = await load(`warrant-history/${selected.underlying}`); } catch { /* Show explicit no-history state. */ } } })(),
      (async () => { if (tracked) { try { rankings = await load(`warrant-branches/${code}`); } catch { rankingError = true; } } })(),
    ]);
    const rows = history?.quotes?.[code]?.rows || [];
    const dates = rows.map(r => r[0]).sort().reverse();
    state.day = dates.includes(state.day) ? state.day : dates[0] || '';
    const q = quoteComparison(rows, state.day);
    const prices = history?.underlyingPrices || [];
    const priceRow = prices.filter(r => r[0] <= state.day).at(-1);
    const u = quoteComparison(prices, priceRow?.[0]);
    const branchDays = [...(rankings?.history || [])].sort((a, b) => b.date.localeCompare(a.date));
    state.branchDay = branchDays.some(d => d.date === state.branchDay) ? state.branchDay : branchDays[0]?.date || '';
    const dayIndex = branchDays.findIndex(d => d.date === state.branchDay);
    const day = branchDays[dayIndex], prev = dayIndex >= 0 ? branchDays[dayIndex + 1] : null;
    const comparisons = compareBranches(day, prev).filter(r => !state.branch || `${r.code} ${r.name}`.toLowerCase().includes(state.branch.toLowerCase()));
    const limit = Number(state.limit);
    csv = rows.filter(r => r[0] <= state.day).slice(-limit).map(r => {
      const prior = rows[rows.findIndex(d => d[0] === r[0]) - 1];
      const d = branchDays.find(d => d.date === r[0]);
      return { code, date: r[0], price: r[1], volume: r[2], priceChange: change(r[1], prior?.[1]).delta, pricePct: change(r[1], prior?.[1]).pct,
        underlying: selected.underlying, underlyingPrice: prices.find(p => p[0] === r[0])?.[1], branchDate: d?.date,
        branchCount: d?.rows.length, coverage: '買超／賣超各最多15名，非全部分點' };
    });
    const comparisonNote = q.previous ? `較 ${q.previous[0]}：${deltaText(q.delta)} 元` : '尚無前筆價格可比較';
    const selectedTitle = `<section class="panel"><div class="panel-head"><div><span class="eyebrow">${esc(code)} · ${warrantCp(selected.cp)} · ${warrantStyle(selected.style)}</span><h2>${esc(selected.name)}</h2><p class="muted">標的 ${esc(selected.underlying)} · ${esc(selected.underlyingName)}　履約價 ${fmt(selected.strike, 3)}　到期 ${esc(selected.expiry || '—')}</p></div><button id="dw-pin">${pins.includes(code) ? '移除自選觀察' : '＋ 加入自選觀察'}</button></div><div class="filters compact">${select('dw-day', '行情資料日', dates, state.day)}${select('dw-limit', '趨勢與明細範圍', ['5', '20', '60'], state.limit, v => `最近 ${v} 個資料日`)}<button id="dw-export" ${rows.length ? '' : 'disabled'}>匯出每日明細 CSV</button></div><a class="text-link" href="#warrants?underlying=${encodeURIComponent(selected.underlying)}">查看此標的全部權證 →</a> · <a class="text-link" href="#warrant-branches?code=${code}">查看分點原始排行 →</a><p class="note">行情自啟用日起累積，最多 60 個資料日；歷史以來源交易日期記錄，休市或抓取失敗不新增假資料。價格為未還原收盤價；與前一筆已保存資料比較，日期間隔可能跨休市或缺漏日。${daysUntil(state.day) < -4 ? '<span class="status-bad">行情距今超過 4 天，可能包含休市日。</span>' : ''}</p></section>`;
    const metrics = `<div class="metrics">${metric('權證收盤價', fmt(q.current?.[1], 3), `${state.day || '尚無資料'} · 元`)}${metric('權證漲跌幅', colored(q.pct, '%', 2), comparisonNote)}${metric('標的收盤價／指數', fmt(u.current?.[1], 3), `${u.current?.[0] || '尚無資料'} · 較 ${u.previous?.[0] || '—'} ${deltaText(u.pct, '%', 2)}`)}${metric('權證成交量', fmt(q.current?.[2], 3), `${state.day || '—'} · 張；較前筆 ${deltaText(q.volume.delta)} 張`)}</div>`;
    const chartRows = rows.filter(r => r[0] <= state.day), underlyingRows = prices.filter(r => r[0] <= state.day);
    const charts = `<div class="split watch-charts"><section class="panel"><h2>權證價格趨勢</h2><p class="muted">元 · 選定日期以前</p>${trendChart(chartRows, 1, '權證收盤價趨勢', '元', limit)}</section><section class="panel"><h2>標的價格趨勢</h2><p class="muted">股票／ETF 為元，指數為點 · 日期獨立標示</p>${trendChart(underlyingRows, 1, '標的收盤價或指數趨勢', '元／點', limit)}</section></div><section class="panel"><h2>權證成交量趨勢</h2>${trendChart(chartRows, 2, '權證成交量趨勢', '張', limit)}</section>`;
    let branchPanel = '';
    if (!day) branchPanel = `<section class="panel"><h2>權證分點每日變化</h2>${empty(!rankingIndex || rankingError ? '分點資料暫時無法載入，請重載資料。' : tracked ? '此權證已在追蹤清單，尚無成功取得的分點資料。' : '此權證未在本次成交量前 300 檔＋固定追蹤清單中，沒有每日分點資料。')}<p class="note">價格觀察可使用全部權證；分點仍僅收錄排行，並非全部分點。</p></section>`;
    else branchPanel = `<section class="panel"><div class="panel-head"><div><span class="eyebrow">BROKER RANKING CHANGES</span><h2>權證分點每日變化</h2><p class="muted">${esc(day.date)} 對照 ${esc(prev?.date || '尚無前筆')} · 張 · 非全部分點</p></div>${dateTag(day.date, '分點')}</div><div class="filters compact">${select('dw-branch-day', '分點資料日', branchDays.map(d => d.date), state.branchDay)}<label>分點名稱或代碼<input id="dw-branch" value="${esc(state.branch)}" placeholder="例如 元大"></label><button id="dw-branch-apply">篩選分點變化</button></div>${day.date !== state.day ? `<div class="notice warning">行情 ${esc(state.day || '尚無資料')} 與分點 ${esc(day.date)} 日期不同，分開呈現，不視為同日訊號。</div>` : ''}${rankings.status !== 'ok' || daysUntil(day.date) < -4 ? '<div class="notice warning">分點更新異常或資料較舊；以下保留最後成功資料，請確認日期。</div>' : ''}${table(['分點', '本期買賣超', '前期買賣超', '買賣超增減', '本期排行', '前期排行', '排行變化 / 狀態'], comparisons.map(r => tr([`${esc(r.name)}<small>${esc(r.code)}</small>`, colored(r.now?.net), colored(r.before?.net), colored(r.delta), r.now ? `${r.now.side === 'buy' ? '買超' : '賣超'} #${r.now.rank}` : '未列入', r.before ? `${r.before.side === 'buy' ? '買超' : '賣超'} #${r.before.rank}` : prev ? '未列入' : '—', `${finite(r.rankChange) ? r.rankChange > 0 ? `上升 ${r.rankChange} 名` : r.rankChange < 0 ? `下降 ${-r.rankChange} 名` : '名次不變' : '—'}<small>${esc(r.status)}</small>`])))}<p class="note">每檔買超／賣超各最多 15 名；只有兩期都出現在排行的分點才計算買賣超增減，同方向才比較名次。未上榜不等於零，也不代表新進或退出；買賣超是當日交易流量，不是庫存或單一投資人持倉。</p></section>`;
    const daily = `<section class="panel"><div class="panel-head"><h2>每日價格與籌碼資料對照</h2><span class="tag">最近 ${limit} 個資料日</span></div>${table(['資料日', '權證收盤', '價格變動', '漲跌幅', '成交量（張）', '同日標的價格', '同日分點資料'], csv.slice().reverse().map(r => tr([esc(r.date), fmt(r.price, 3), colored(r.priceChange), colored(r.pricePct, '%', 2), fmt(r.volume, 3), fmt(r.underlyingPrice, 3), r.branchDate ? `${r.branchCount} 個排行分點` : '未取得／未收錄'])))}<p class="note">同日對照只匹配完全相同的來源日期；缺值保留「—」，不帶入其他日期的價格或籌碼。</p></section>`;
    return controls + selectedTitle + metrics + (rows.length ? charts : empty('歷史行情尚未建立或暫時無法載入，請稍後重載資料。')) + branchPanel + daily;
  }
  function bind(render) {
    const $ = id => document.getElementById(id);
    const message = text => { state.message = text; $('dw-message').textContent = text; };
    const navigate = hash => { if (location.hash === hash) render(); else location.hash = hash; };
    const go = () => {
      const q = $('dw-code').value.trim().toLowerCase();
      const matches = catalog.filter(r => r.code.toLowerCase() === q || r.name.toLowerCase() === q);
      if (matches.length !== 1) return message('請輸入完整權證代碼或唯一名稱。');
      navigate(link(matches[0].code));
    };
    const underlying = () => {
      const u = resolveWarrantUnderlying(choices, $('dw-underlying').value);
      if (!u) return message('請輸入完整標的代碼／名稱，或從建議清單選擇。');
      navigate(`#daily-watch?underlying=${encodeURIComponent(u)}`);
    };
    $('dw-go').addEventListener('click', go); $('dw-code').addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
    $('dw-by-underlying').addEventListener('click', underlying); $('dw-underlying').addEventListener('keydown', e => { if (e.key === 'Enter') underlying(); });
    for (const [id, key] of [['dw-day', 'day'], ['dw-limit', 'limit'], ['dw-branch-day', 'branchDay']]) {
      $(id)?.addEventListener('change', () => { state[key] = $(id).value; render(); });
    }
    const filter = () => { state.branch = $('dw-branch').value.trim(); render(); };
    $('dw-branch-apply')?.addEventListener('click', filter);
    $('dw-branch')?.addEventListener('keydown', e => { if (e.key === 'Enter') filter(); });
    $('dw-pin')?.addEventListener('click', () => {
      const list = saved();
      if (!list.includes(code) && list.length >= 20) return message('已達 20 檔，請先移除一檔自選。');
      try { localStorage.setItem('derivatives-daily-watch', JSON.stringify(list.includes(code) ? list.filter(c => c !== code) : [...list, code])); state.message = ''; render(); }
      catch { message('此瀏覽器無法儲存自選，請允許網站儲存空間後重試。'); }
    });
    $('dw-export')?.addEventListener('click', () => {
      const fields = [['code', '權證代碼'], ['date', '行情資料日'], ['price', '權證收盤價'], ['priceChange', '較前筆價格變動'], ['pricePct', '較前筆漲跌幅%'], ['volume', '成交量（張）'], ['underlying', '標的代碼'], ['underlyingPrice', '同日標的收盤價或指數'], ['branchDate', '同日分點資料日'], ['branchCount', '同日排行分點數'], ['coverage', '分點範圍']];
      const url = URL.createObjectURL(new Blob([toCSV(csv, fields)], { type: 'text/csv;charset=utf-8' }));
      const a = document.createElement('a'); a.href = url; a.download = `warrant-daily-${code}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  return { page, bind };
}
