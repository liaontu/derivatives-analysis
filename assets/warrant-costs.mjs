import {finite, esc, fmt, warrantStyle, tradePrice, daysUntil} from './model.mjs';

const positive = n => finite(n) && n > 0;
const fee = (base, percent, minimum) => base > 0 ? Math.max(minimum, base * percent / 100) : 0;
const WARRANT_TAX = 0.001, STOCK_TAX = 0.003;
const modes = {cash:'到期現金結算', early:'提前現金履約（美式）', sale:'到期前賣出權證', physical:'股票交割並完成現股買賣'};

export function matchedSpot(row, quotes) {
  const match = quotes?.rows?.find(q => ['code','market','name','underlying','date','expiry','strike','ratio','termsDate'].every(k => q[k] === row[k]));
  const close = match?.basis?.[4];
  return positive(close) ? close : null;
}

function standardTerms(r) {
  if (r.termsDate !== r.date || !r.termsDate || !positive(r.strike) || !positive(r.ratio) || !['C','P'].includes(r.cp)) return false;
  if (r.market === 'TWSE') return r.kind === '一般型';
  return r.market === 'TPEX' && r.kind === '一般型' && r.reset === 'N' && !(r.cap > 0 || r.floor > 0) && /^(?:\d{6}|\d{5}[PU])$/.test(r.code);
}

// Figures retain fractions of a dollar until display. Each fee minimum applies once per leg.
function exitAmounts(row, inputs, price) {
  const {mode, lots, rate, minimum, exerciseRate, exerciseMinimum, stockRate, stockMinimum} = inputs;
  const units = lots * 1000, shares = units * row.ratio;
  const call = row.cp === 'C';
  if (mode === 'sale') {
    const gross = price * units;
    return {gross, exitFee:fee(gross, rate, minimum), exitTax:gross * WARRANT_TAX, stockFee:0, stockTax:0};
  }
  const gross = Math.max(0, (call ? price - row.strike : row.strike - price) * shares);
  if (gross === 0) return {gross, exitFee:0, exitTax:0, stockFee:0, stockTax:0};
  if (mode !== 'physical') {
    const exitTax = gross * WARRANT_TAX;
    return {gross, exitTax, exitFee:Math.min(fee(gross, exerciseRate, exerciseMinimum), gross - exitTax), stockFee:0, stockTax:0};
  }
  const delivery = row.strike * shares, stock = price * shares;
  return {gross, exitFee:fee(delivery, exerciseRate, exerciseMinimum), exitTax:call ? 0 : delivery * STOCK_TAX,
    stockFee:fee(stock, stockRate, stockMinimum), stockTax:call ? stock * STOCK_TAX : 0};
}

// A put cannot break even below a zero stock price. Solve the same net-cash calculation used by the table.
function breakEven(net, row, mode) {
  const put = mode !== 'sale' && row.cp === 'P';
  let lo = 0, hi = mode === 'sale' ? 1 : row.strike;
  if (put && net(0) < 0) return null;
  if (!put) {
    while (net(hi) < 0 && hi < Number.MAX_SAFE_INTEGER) hi *= 2;
    if (net(hi) < 0) return null;
  }
  for (let i = 0; i < 75; i++) {
    const mid = (lo + hi) / 2;
    if ((net(mid) >= 0) === put) lo = mid;
    else hi = mid;
  }
  return put ? lo : hi;
}

export function estimate(row, inputs) {
  const {mode, lots, buy, exit, extra} = inputs;
  if (!Object.hasOwn(modes, mode) || !Number.isSafeInteger(lots) || lots < 1 || lots > 1000000 || !positive(buy))
    return {error:'請填入正數買進單價及 1 至 1,000,000 的整數張數。'};
  if (mode !== 'sale' && !standardTerms(row)) return {error:'條款為重設型、特殊型或資料不足，只提供市場賣出試算。'};
  if (mode === 'early' && warrantStyle(row.style) !== '美式') return {error:'提前履約情境限美式權證。'};
  if (mode === 'physical' && !inputs.confirmed) return {error:'請先核對發行條款，確認允許股票交割。'};
  const theoreticalShares = lots * 1000 * row.ratio;
  if (mode === 'physical' && Math.abs(theoreticalShares - Math.round(theoreticalShares)) > 1e-8)
    return {error:'本次換算有不足一股的尾數，請依說明書核對尾數處理後另行估算。'};
  const requiredFees = ['rate','minimum','extra'];
  if (mode !== 'sale') requiredFees.push('exerciseRate','exerciseMinimum');
  if (mode === 'physical') requiredFees.push('stockRate','stockMinimum');
  if (requiredFees.some(k => !finite(inputs[k]) || inputs[k] < 0 || (/rate/i.test(k) && inputs[k] >= 50)))
    return {error:'請填妥適用費用。費率以百分比輸入，最低費與其他費用以元輸入。'};
  const premium = buy * lots * 1000, entry = fee(premium, inputs.rate, inputs.minimum);
  const net = price => {
    const x = exitAmounts(row, inputs, price);
    return x.gross - x.exitFee - x.exitTax - x.stockFee - x.stockTax - premium - entry - extra;
  };
  const be = breakEven(net, row, mode);
  const validExit = finite(exit) && (mode === 'sale' ? exit > 0 : exit >= 0);
  const amounts = validExit ? exitAmounts(row, inputs, exit) : null;
  const totalFees = amounts ? entry + extra + amounts.exitFee + amounts.exitTax + amounts.stockFee + amounts.stockTax : null;
  return {premium, entry, be, amounts, totalFees, pnl:validExit ? net(exit) : null};
}

const sources = '<a href="https://twse-regulation.twse.com.tw/m/LawContent.aspx?FID=FL007277">證交所買賣辦法第 14 條</a> · <a href="https://twse-regulation.twse.com.tw/TW/int/DAT01_print.aspx?FLCODE=FE243953">財政部交易稅函釋</a> · <a href="https://www.twse.com.tw/zh/products/securities/warrant/educate/qa.html">履約說明</a> · <a href="https://www.tpex.org.tw/storage/warrant/%E6%8A%95%E8%B3%87%E4%BA%BA/%E6%8A%95%E8%B3%87%E6%AC%8A%E8%AD%89QA.pdf">櫃買中心權證問答</a>';
const numberField = (name, label, value, step='any') => `<label>${esc(label)}<input name="${name}" type="number" min="0" step="${step}" value="${esc(value)}" placeholder="請輸入"></label>`;

export function costPanel(row, spot) {
  const styled = warrantStyle(row.style), days = daysUntil(row.expiry);
  const reference = tradePrice(row.price, row.volume);
  const allowed = Object.entries(modes).filter(([key]) => key === 'sale' || (standardTerms(row) && (key !== 'early' || styled === '美式')));
  const model = Object.fromEntries(['code','market','cp','style','expiry','ratio','strike','termsDate','date','kind','reset','cap','floor'].map(k => [k,row[k]]));
  const timing = styled === '歐式' ? '到期日才可履約' : styled === '美式' ? '可在約定履約期間申請，亦須完成交割' : '履約時點待核對';
  const direction = row.cp === 'C' ? '認購' : '認售';
  const gap = positive(spot) && positive(row.strike) ? (row.cp === 'C' ? spot-row.strike : row.strike-spot) : null;
  return `<section class="panel" id="warrant-cost"><h2>履約價、現股與費稅試算</h2>
    <p><strong>${esc(row.code)} · ${direction} · ${esc(styled)}</strong>　${timing}。履約開始日 ${esc(row.exerciseStart || '未提供')}，最後交易日 ${esc(row.lastTrade || '未提供')}，到期日 ${esc(row.expiry || '未提供')}（${finite(days) ? (days >= 0 ? '距台北今日 '+days+' 個日曆天' : '已到期') : '天數待確認'}）。</p>
    <p>行情日 ${esc(row.date)}　${esc(row.underlyingName)}收盤 ${fmt(spot,4)} 元 · 履約價 ${fmt(row.strike,4)} 元 · ${finite(gap) ? (gap>0?'價內':gap<0?'價外':'價平')+' '+fmt(Math.abs(gap),4)+' 元／股' : '價內外待確認'}。每張 1,000 單位 × 行使比例 ${fmt(row.ratio,6)} ＝ <strong>${fmt(positive(row.ratio)?1000*row.ratio:null,6)} 股</strong>。</p>
    <form data-row="${esc(JSON.stringify(model))}" data-reference="${esc(reference)}" data-spot="${esc(spot)}">
    <div class="filters"><label>試算情境<select name="mode">${allowed.map(([k,label])=>`<option value="${k}">${esc(label)}</option>`).join('')}</select></label>
    ${numberField('lots','權證張數',1,1)}${numberField('buy','買進單價（元／單位）',reference)}
    <label><span data-exit-label>假設標的結算價（元／股）</span><input name="exit" type="number" step="any" min="0" placeholder="請輸入"></label></div>
    <p class="note">${reference === null ? '當日無可核實的權證成交價，請填實際買價。' : '預填當日權證收盤價，可改填你的實際成交價。'} <span data-mode-note></span></p>
    <label data-physical hidden><input type="checkbox" name="confirmed"> 已核對本檔條款允許證券給付及交割股數</label>
    <details><summary>券商手續費與其他成本</summary><p>請填入折扣後費率與每筆最低費。若另有代辦、匯費或滑價，填在其他成本。已反映在輸入價的買賣價差可免再計一次。</p>
    <div class="filters">${numberField('rate','權證買賣手續費（%）',0.1425)}${numberField('minimum','權證買賣每筆最低費（元）',20)}
    ${numberField('exerciseRate','履約手續費（%）',0.1425)}${numberField('exerciseMinimum','履約最低費（元，假設）',20)}
    ${numberField('stockRate','交割後賣股／交割前買股手續費（%）',0.1425)}${numberField('stockMinimum','現股每筆最低費（元）',20)}
    ${numberField('extra','本次其他成本合計（元）',0)}</div></details>
    <div data-result aria-live="polite"></div></form>
    <p class="note">以個人一般交易、各買賣腿各一筆估算，先保留小數再顯示。實收金額依券商拆單、取整及帳戶收費而定。證交稅按各情境的課稅金額計算。適用規則核對日 2026/11/07。</p><p class="note">${sources}</p></section>`;
}

export function bindCostPanel(table, tr) {
  const form = document.querySelector('#warrant-cost form');
  if (!form) return;
  const row = JSON.parse(form.dataset.row), controls = form.elements;
  const results = form.querySelector('[data-result]');
  let lastMode;
  const update = () => {
    const mode = controls.mode.value;
    if (mode !== lastMode) {
      controls.exit.value = mode === 'sale' ? form.dataset.reference : form.dataset.spot;
      controls.confirmed.checked = false;
      lastMode = mode;
    }
    form.querySelector('[data-physical]').hidden = mode !== 'physical';
    form.querySelector('[data-exit-label]').textContent = mode === 'sale' ? '假設權證賣價（元／單位）' : mode === 'physical' ? '假設現股交易價（元／股）' : '假設標的結算價（元／股）';
    const notes = {cash:'有同日現股收盤時會預填情境。國內個股到期結算通常用最後 60 分鐘成交價的算術平均，實際給付依發行條款。',
      early:'僅試算美式於可履約日按當日現股收盤現金結算的情境。可申請日期與給付方式須依說明書核對。',
      sale:'市場賣出採輸入的權證成交單價。損平價亦以每單位權證表示。',
      physical:'認購假設取得股票後以情境價賣出，認售假設先以情境價買股再交付。只在價內行使，按一般現股費稅估算。'};
    form.querySelector('[data-mode-note]').textContent = notes[mode] + (!standardTerms(row)?' 特殊或未核實條款只提供市場賣出試算。':'');
    const args = Object.fromEntries([...form.querySelectorAll('input[type=number]')].map(el => [el.name,el.valueAsNumber]));
    Object.assign(args,{mode,confirmed:controls.confirmed.checked});
    const out = estimate(row,args);
    if (out.error) { results.textContent = out.error; return; }
    const x = out.amounts;
    const fees = [['買進權利金',out.premium],['買進權證手續費',out.entry],['其他成本',args.extra]];
    if (mode === 'sale') fees.push(['賣出權證收入',x?.gross],['賣出手續費',x?.exitFee],['賣出證交稅（成交額 × 0.1%）',x?.exitTax]);
    else {
      if (mode === 'physical' && x?.gross > 0) {
        const shares = args.lots * 1000 * row.ratio;
        fees.push([row.cp === 'C' ? '履約支付股款' : '交股收取履約股款',row.strike * shares],
          [row.cp === 'C' ? '另行賣股收款' : '另行買股支出',args.exit * shares]);
      }
      fees.push(['履約價差總額',x?.gross],[mode === 'physical' ? '履約手續費（按履約總價）' : '履約手續費（按價差）',x?.exitFee],
        [mode === 'physical' ? (row.cp === 'P' ? '交付股票證交稅（履約總價 × 0.3%）' : '持有人取得股票的證交稅') : '現金履約證交稅（價差 × 0.1%）',x?.exitTax]);
      if (mode === 'physical') fees.push(['配套現股買賣手續費',x?.stockFee],['配套賣股證交稅（賣股金額 × 0.3%）',x?.stockTax]);
    }
    fees.push(['費稅及其他成本合計',out.totalFees],['扣費稅後損益',out.pnl]);
    const beLabel = mode === 'sale' ? '權證損平賣價（元／單位）' : '標的損平價（元／股）';
    results.innerHTML = `<p><strong>${beLabel}　${out.be === null ? '非負價格範圍內無解' : '約 '+fmt(out.be,4)}</strong> · ${fmt(args.lots*1000)} 單位，對應 ${fmt(positive(row.ratio)?args.lots*1000*row.ratio:null,6)} 股。</p>` +
      (x ? '' : '<p>請填情境價格以計算實際金額。</p>') + table(['費稅明細','新臺幣（元）'],fees.map(([k,v])=>tr([esc(k),fmt(v,2)])));
  };
  form.addEventListener('input',update);
  form.addEventListener('submit',event=>{event.preventDefault();update();});
  update();
}
