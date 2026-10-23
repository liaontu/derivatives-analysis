import { finite, fmt, esc, filterUSMarkets, cotTrend } from './model.mjs';

const categoryNames = { dealer: '交易商／中介機構', assetManager: '資產管理／機構', leveraged: '槓桿基金', other: '其他申報交易人', nonreportable: '非申報部位', producer: '生產商／商家／加工商／使用者', swap: '交換交易商', managedMoney: '資金管理者' };
const optionNames = { 'SUM OF ALL PRODUCTS': '全部選擇權（Cboe 報表）', 'INDEX OPTIONS': '指數選擇權', 'EXCHANGE TRADED PRODUCTS': 'ETF／ETP 選擇權', 'EQUITY OPTIONS': '個股選擇權', 'CBOE VOLATILITY INDEX (VIX)': 'VIX 選擇權', 'SPX + SPXW': 'SPX + SPXW 選擇權' };
const cfeNames = { VX: 'VIX 期貨', VXM: '迷你 VIX 期貨', VA: 'S&P 500 變異數期貨', IBHY: '高收益公司債指數期貨', IBIG: '投資級公司債指數期貨', IEMD: '新興市場債券指數期貨', XBTF: 'FTSE 比特幣期貨', FBT: '現金結算比特幣期貨', FET: '現金結算以太幣期貨', PBT: '比特幣連續期貨', PET: '以太幣連續期貨', MGTN: 'Magnificent 10 指數期貨' };
const ratio = x => finite(x) ? `${fmt(x, 3)} 倍` : '—';

export function createUSPages(ui) {
  const { load, state, select, choose, unique, table, tr, dateTag, signed, metric, lineChart, empty } = ui;
  const health = data => data.status !== 'ok' ? '<p class="notice warning">此來源本次更新失敗，以下保留最後成功資料；請參閱資料與更新頁。</p>' : '';

  async function cotPanel(combined) {
    const scope = combined ? 'combined' : 'futures';
    const key = 'cot-' + scope;
    const group = state[key + 'Group'] || 'financial';
    const data = await load(`us-cot-${group}${combined ? '-combined' : ''}`);
    const dates = data.history.map(d => d.date).sort().reverse();
    state[key + 'Date'] = choose(dates, state[key + 'Date'] || data.date);
    const day = state[key + 'Date'];
    const rows = data.history.find(d => d.date === day)?.rows || data.rows;
    const markets = filterUSMarkets(rows, state[key + 'Search'] || '');
    const preferred = group === 'financial' ? markets.find(r => /E-MINI S&P 500/.test(r.name)) : markets.find(r => r.shortName === 'GOLD');
    state[key + 'Contract'] = choose(markets.map(r => r.contract), state[key + 'Contract'] || preferred?.contract);
    const current = markets.find(r => r.contract === state[key + 'Contract']);
    const categories = current?.categories || [];
    state[key + 'Category'] = choose(categories.map(r => r.category), state[key + 'Category'] || (group === 'financial' ? 'assetManager' : 'managedMoney'));
    const points = cotTrend(data.history, current?.contract, state[key + 'Category']);
    return `<section class="panel"><div class="panel-head"><div><span class="eyebrow">CFTC · WEEKLY POSITIONING</span><h2>${combined ? '期貨＋選擇權合併持倉' : '美國期貨 COT 持倉'}</h2></div>${dateTag(day, '部位基準日')}</div>${health(data)}
      <div class="coverage">${combined ? '期權合併報告將選擇權依 Delta 折算成期貨等值部位；這不是選擇權單獨持倉，也沒有 Call／Put 或履約價分解。' : '金融與商品期貨分開查詢。這是 CFTC 交易人分類統計，無個別機構或券商分點明細。'} CFTC 通常週五公布截至週二的部位，本站每天檢查；資料仍是週頻。</div>
      <div class="filters compact">${select('us-cot-group', '報告類型', ['financial', 'commodity'], group, v => v === 'financial' ? '金融：股指／利率／匯率' : '商品：能源／金屬／農產品')}${select('us-cot-date', '部位基準日（每週）', dates, day)}<label>商品搜尋<input id="us-cot-search" value="${esc(state[key + 'Search'] || '')}" placeholder="S&P、NASDAQ、GOLD 或代碼"></label><button id="us-cot-apply">搜尋商品</button></div>
      <div class="filters">${select('us-cot-contract', `商品與交易所 · ${markets.length} 項`, markets.map(r => r.contract), current?.contract, c => `${c} · ${markets.find(r => r.contract === c).name}`)}</div>
      ${current ? `<p class="section-intro">${esc(current.name)}<br>未平倉 ${fmt(current.oi)} 口 · 較前週 ${signed(current.oiChange)} 口<br>契約規格：${esc(current.units)}</p>${table(['交易人分類', '多方', '空方', '價差部位', '淨部位', '淨部位週增減'], categories.map(r => tr([esc(categoryNames[r.category] || r.category), fmt(r.long), fmt(r.short), fmt(r.spread), signed(r.net), signed(r.change)])))}` : empty('沒有符合搜尋的商品，請修改搜尋文字。')}
      <p class="note">單位：${combined ? '期貨等值口數' : '口'}，依該契約規格。淨部位＝多方減空方；價差部位在多空兩側皆有貢獻，不計為淨方向。週增減取自官方變動欄位，缺值不補零；不同商品口數不可直接相加。</p>
      <div class="panel-head trend-heading"><h2>分類淨部位・每週趨勢</h2><div class="filters compact">${select('us-cot-category', '追蹤分類', categories.map(r => r.category), state[key + 'Category'], c => categoryNames[c] || c)}</div></div>${lineChart(points, 'CFTC 每週淨部位')}
      <p class="note">已載入官方最近約 4 個月週報，之後累積最多 26 個基準日。歷史圖依各週實際日期繪製；切換表格日期不會隱藏較新的歷史點。</p></section>`;
  }

  async function futures() {
    const data = await load('us-futures');
    const contracts = unique(data.rows, 'contract');
    state.usFuture = choose(contracts, state.usFuture || 'VX');
    const quotes = data.rows.filter(r => r.contract === state.usFuture).sort((a, b) => a.expiry.localeCompare(b.expiry));
    const monthly = data.rows.filter(r => r.contract === 'VX' && /^VX\/[A-Z]\d+$/.test(r.symbol)).sort((a, b) => a.expiry.localeCompare(b.expiry));
    const cot = await cotPanel(false);
    return `<div class="coverage">免費涵蓋 CFTC 金融與商品期貨籌碼，以及 CFE 公開期貨結算價。S&P 500、NASDAQ、黃金、原油等可查 COT 籌碼；此版本未提供 CME 全商品每日行情。</div>
      <div class="metrics">${metric('VIX 期貨・近月月契約', fmt(monthly[0]?.price, 4), `${monthly[0]?.symbol || '—'} · ${data.date || '尚無資料'}`)}${metric('CFE 結算價', fmt(data.rows.length), '個契約 · 交易所每日結算')}${metric('CFTC 籌碼', '每週', '每天自動檢查新報告')}${metric('美國盤後抓取', '08:43', '台北時間 · 11:43 再次更新')}</div>
      ${cot}<section class="panel"><div class="panel-head"><div><span class="eyebrow">CFE · DAILY SETTLEMENT</span><h2>美國期貨每日結算價</h2></div>${dateTag(data.date, '美國資料日')}</div>${health(data)}<div class="filters compact">${select('us-future-product', '商品', contracts, state.usFuture, c => `${c} · ${cfeNames[c] || c}`)}</div>${table(['契約', '到期日', '每日結算價'], quotes.map(r => tr([esc(r.symbol), esc(r.expiry), fmt(r.price, 4)])))}<p class="note">Cboe Futures Exchange（CFE）公布的每日結算價；不是最後成交價或即時報價。價格單位依商品規格，部分結算價由交易所裁量決定，詳見<a class="text-link" href="https://www.cboe.com/markets/us/futures/market-statistics/settlement/futures/daily" target="_blank" rel="noopener noreferrer">官方原表</a>。</p></section>`;
  }

  async function options() {
    const data = await load('us-options');
    const contracts = data.rows.map(r => r.contract);
    state.usOption = choose(contracts, state.usOption || 'SPX + SPXW');
    const current = data.rows.find(r => r.contract === state.usOption);
    const cot = await cotPanel(true);
    const points = data.history.map(d => ({ date: d.date, value: d.rows.find(r => r.contract === state.usOption)?.volumeRatio }));
    return `<section class="panel"><div class="panel-head"><div><span class="eyebrow">CBOE · DAILY OPTIONS STATISTICS</span><h2>美國選擇權交易熱度</h2></div>${dateTag(data.date, '美國資料日')}</div>${health(data)}
      <div class="coverage">Cboe 每日報表口徑，涵蓋指數、ETF／ETP 與個股選擇權統計；不是全美所有交易所成交量，也不是逐履約價選擇權鏈。各分類與單一產品可能重疊，不能相加。</div>
      <div class="filters">${select('us-option-product', '統計分類／商品', contracts, state.usOption, c => optionNames[c] || c)}</div>
      <div class="metrics">${metric('Call 成交量', fmt(current?.callVolume), '口 · 所選分類')}${metric('Put 成交量', fmt(current?.putVolume), '口 · 所選分類')}${metric('成交量 P/C 比', ratio(current?.volumeRatio), 'Put 成交量 ÷ Call 成交量', 'teal')}${metric('未平倉 P/C 比', ratio(current?.oiRatio), 'Put 未平倉 ÷ Call 未平倉')}</div>
      ${table(['統計項目', 'Call', 'Put', '合計'], current ? [tr(['成交量（口）', fmt(current.callVolume), fmt(current.putVolume), fmt(current.totalVolume)]), tr(['未平倉（口）', fmt(current.callOi), fmt(current.putOi), fmt(current.totalOi)])] : [])}
      <p class="note">比值使用倍數，例如 0.94 倍＝94%。由原表口數計算；Call 為零時顯示缺值。未平倉依 Cboe 報表提供的範圍與時點，不能與台灣未平倉數量直接比較。</p>
      <h2 class="trend-heading">所選分類・成交量 P/C 比趨勢</h2>${lineChart(points, 'Cboe 每日成交量 P/C 比（倍）', false, 3)}
      <p class="note">每日統計歷史從本功能啟用後累積。CFTC 合併籌碼另列於下方，來源、頻率與單位各自標示。</p></section>
      <section class="panel"><div class="panel-head"><h2>各類選擇權每日比較</h2>${dateTag(data.date)}</div>${table(['分類／商品', 'Call 成交', 'Put 成交', '成交 P/C（倍）', 'Call 未平倉', 'Put 未平倉'], data.rows.map(r => tr([esc(optionNames[r.contract] || r.contract), fmt(r.callVolume), fmt(r.putVolume), fmt(r.volumeRatio, 3), fmt(r.callOi), fmt(r.putOi)])))}</section>${cot}`;
  }

  function bind(render, combined) {
    const key = 'cot-' + (combined ? 'combined' : 'futures');
    const bindings = [['us-cot-group', key + 'Group'], ['us-cot-date', key + 'Date'], ['us-cot-contract', key + 'Contract'], ['us-cot-category', key + 'Category'], ['us-future-product', 'usFuture'], ['us-option-product', 'usOption']];
    for (const [id, key] of bindings) {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', () => { state[key] = el.value; render(); });
    }
    const input = document.getElementById('us-cot-search');
    const search = () => { state[key + 'Search'] = input.value; render(); };
    document.getElementById('us-cot-apply')?.addEventListener('click', search);
    input?.addEventListener('keydown', event => { if (event.key === 'Enter') search(); });
  }
  return { futures, options, bind };
}
