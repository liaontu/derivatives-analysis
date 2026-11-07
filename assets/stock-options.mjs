import { esc, finite, fmt } from './model.mjs';

const difference = (a, b) => finite(a) && finite(b) ? a - b : null;
const signed = v => finite(v) ? (v > 0 ? '+' : '') + fmt(v, 2) : '—';
const series = row => row.month + '/' + row.strike;

export function compare(row, stock) {
  const gap = (call, put, stockPrice) => finite(call) && finite(put) && finite(stockPrice)
    ? row.strike + call - put - stockPrice : null;
  return {
    close: gap(row.C?.last, row.P?.last, stock.spot),
    sellSynthetic: gap(row.C?.bid, row.P?.ask, stock.ask),
    buySynthetic: difference(0, gap(row.C?.ask, row.P?.bid, stock.bid)),
  };
}

export function scenario(edge, adjustment, costs, direction) {
  if (![edge, adjustment, ...costs].every(finite) || !costs.length || costs.some(v => v < 0)) return null;
  const corrected = edge + (direction === 'sell' ? adjustment : -adjustment);
  return corrected * 2000 - costs.reduce((sum, cost) => sum + cost, 0);
}

function fields() {
  return [['carry', '每股平價修正（可為負數）'], ['tax', '全程交易與結算稅額（元／組）'],
    ['fees', '全程券商與結算手續費（元／組）'], ['funding', '借券及額外資金成本（元／組）'],
    ['slip', '滑價及其他費用（元／組）']]
    .map(([key, label]) => `<label>${label}<input name="${key}" required type="number" step="any" ${key === 'carry' ? '' : 'min="0"'}></label>`).join('');
}

export function createStockOptions({load, table, tr, select, metric}) {
  const state = {day: '', symbol: 'CDO', all: false, selected: ''};
  let activeRow;
  async function page() {
    const index = await load('stock-option-watch');
    if (!index.latest) return '<p class="notice warning">等待第一份通過核對的個股選擇權行情。</p>';
    const day = index.dates.includes(state.day) ? state.day : index.latest;
    const before = index.dates[index.dates.indexOf(day) - 1];
    const [now, prev] = await Promise.all([load('stock-option-watch/' + day),
      before ? load('stock-option-watch/' + before) : null]);
    const symbols = Object.keys(now.stocks);
    const marketPairs = Object.values(now.stocks).flatMap(s => s.pairs.map(pair => compare(pair, s)));
    const marketPriced = marketPairs.filter(p => finite(p.close)).length;
    const marketQuoted = marketPairs.filter(p => finite(p.buySynthetic) || finite(p.sellSynthetic)).length;
    if (!now.stocks[state.symbol]) state.symbol = symbols[0];
    const stock = now.stocks[state.symbol], prior = prev?.stocks[state.symbol];
    const previous = new Map((prior?.code === stock.code ? prior.pairs : []).map(r => [series(r), r]));
    const spot = stock.spot;
    const label = s => now.stocks[s].code + ' ' + now.stocks[s].name + ' · ' + s;
    const rows = stock.pairs.map(r => ({...r, ...compare(r, stock)}));
    const observed = rows.filter(r => finite(r.close));
    const choices = (state.all ? rows : observed).slice().sort((a, b) =>
      (Math.abs(b.close ?? 0) - Math.abs(a.close ?? 0)) || a.month.localeCompare(b.month) || a.strike - b.strike);
    activeRow = choices.find(r => series(r) === state.selected) || choices[0];
    const optionChange = (r, priorRow, side) => signed(difference(r[side]?.last, priorRow?.[side]?.last));
    const rowHtml = r => {
      const old = previous.get(series(r));
      return tr([esc(r.month) + '<small>履約價 ' + fmt(r.strike, 2) + '</small>',
        fmt(r.C?.last, 2) + '<small>變動 ' + optionChange(r, old, 'C') + '</small>',
        fmt(r.P?.last, 2) + '<small>變動 ' + optionChange(r, old, 'P') + '</small>',
        fmt(r.C?.volume) + '／' + fmt(r.P?.volume), signed(r.close),
        signed(difference(r.close, old && compare(old, prior).close)),
        signed(r.sellSynthetic), signed(r.buySynthetic)]);
    };
    const calc = activeRow ? `<section class="panel"><h2>一組契約的成本情境</h2>
      <div class="filters">${select('so-series','到期別／履約價',choices.map(series),series(activeRow))}</div>
      <p>每組使用同一履約價的買權、賣權各一口與 2,000 股現股。賣合成採買權買價、賣權賣價與現股賣價。買合成採買權賣價、賣權買價與現股買價。兩個方向請分別填入適用的成本。</p>
      <form id="so-form"><div class="filters"><label>方向<select name="direction"><option value="sell">賣合成、買現股</option><option value="buy">買合成、賣空現股</option></select></label>${fields()}</div>
      <p class="note">平價修正＝履約價折現後與原價的差額＋未由契約調整抵銷的股利現值。請依這組契約填寫，避免重複列入利息或股利。若採零假設請明填 0。</p>
      <p aria-live="polite" data-so-result></p></form></section>` : '';
    const lastStatus = index.status === 'retained' ? '<p class="notice warning">最近收集失敗，顯示最後一次保存的資料。</p>' : '';
    return `${lastStatus}<section class="panel"><div class="filters">${select('so-symbol','標的',symbols,state.symbol,label)}${select('so-day','行情日期',index.dates.slice().reverse(),day)}
      <label><input type="checkbox" id="so-all" ${state.all ? 'checked' : ''}>顯示缺成交價的組合</label></div>
      <p class="coverage">全市場 ${esc(day)} · ${fmt(symbols.length)} 檔標的、${fmt(marketPairs.length)} 組契約；${fmt(marketPriced)} 組可算收盤價差，${fmt(marketQuoted)} 組有至少一個方向的三邊盤末報價。</p>
      <div class="metrics">${metric('現股收盤（元）',fmt(spot,2),stock.name + ' · ' + day)}
      ${metric('現股變動（元）',signed(difference(spot,prior?.code === stock.code ? prior.spot : null)),before ? '與 ' + before + ' 比較' : '等待前一收錄日')}
      ${metric('可算成交價差',fmt(observed.length) + '／' + fmt(rows.length),'當日兩邊均成交且有同日現股價')}
      ${metric('有單向盤末報價',fmt(rows.filter(r => finite(r.buySynthetic) || finite(r.sellSynthetic)).length),'至少一種方向有三邊所需的盤末買賣價')}</div>
      <p>價格變動與價差變動皆比前一收錄日。每股收盤價差＝履約價＋買權成交價－賣權成交價－現股收盤價，採零利率、零淨股息基準。正值表示合成價格較高。</p>
      ${table(['到期別／履約價','買權收盤／變動','賣權收盤／變動','成交量 C／P（口）','收盤價差（元／股）','價差變動','賣合成報價差','買合成報價差'],choices.map(rowHtml))}
      </section>${calc}<section class="panel source-list"><h2>資料範圍與結算</h2><p>只查期交所名單的台灣普通股標準契約、一般時段。上市現股優先取證交所同日收盤與最後揭示買賣價。缺少現股報表列時只查同日標的收盤歷史。缺價留白，結算價不代替成交價。歷史日期依目前可核對的標的清單整理。</p>
      <p>股票選擇權為歐式現金結算，到期採現股最後 60 分鐘均價。盤末報價時間可能不同，缺少同步三邊報價、可成交數量、可借券數與結算均價避險成本，成本情境僅供盤後研究。</p>
      <p><a href="${esc(now.roster?.url || index.rosterUrl || 'https://openapi.taifex.com.tw/v1/SSOLists')}">期交所標的清單</a> · <a href="https://openapi.taifex.com.tw/v1/DailyMarketReportOpt">選擇權行情</a> · <a href="${esc(now.spotUrl)}">證交所同日行情</a> · <a href="https://www.taifex.com.tw/cht/2/sSO">交易及結算規格</a> · <a href="data/derivatives/stock-option-watch/${esc(day)}.json">本日觀察資料</a></p></section>`;
  }
  function bind(rerender) {
    for (const [id, key] of [['so-day','day'],['so-symbol','symbol'],['so-all','all'],['so-series','selected']]) {
      document.getElementById(id)?.addEventListener('change', event => {
        state[key] = key === 'all' ? event.target.checked : event.target.value;
        if (key !== 'selected') state.selected = '';
        rerender();
      });
    }
    const form = document.getElementById('so-form');
    if (!form || !activeRow) return;
    const update = () => {
      const values = new FormData(form), dir = values.get('direction');
      const numeric = key => values.get(key) === '' ? null : Number(values.get(key));
      const edge = dir === 'sell' ? activeRow.sellSynthetic : activeRow.buySynthetic;
      const net = scenario(edge,numeric('carry'),['tax','fees','funding','slip'].map(numeric),dir);
      form.querySelector('[data-so-result]').textContent = !finite(edge) ? '此方向缺少必要的盤末買權、賣權或現股報價，無法試算。' :
        finite(net) ? '依輸入假設的每組成本後價差：' + signed(net) + ' 元。' : '填妥平價修正與各項成本後顯示每組情境結果。';
    };
    form.addEventListener('submit', event => event.preventDefault());
    form.addEventListener('input', update);
    form.elements.direction.addEventListener('change', () => {
      for (const key of ['tax','fees','funding','slip']) form.elements[key].value = '';
      update();
    });
    update();
  }
  return {page, bind};
}
