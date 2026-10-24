import { esc, fmt, finite, resolveWarrantUnderlying, warrantCp, daysUntil, toCSV } from './model.mjs';
import { trendChart } from './watch.mjs';

export function sortSpreadPairs(pairs, key = 'gap', historical = false) {
  const score = p => key === 'z' ? (finite(p.latest?.z) ? Math.abs(p.latest.z) : null) : key === 'history' ? p.points.length : finite(p.latest?.spread) ? Math.abs(p.latest.spread) : null;
  return pairs.filter(p => historical || finite(p.latest?.spread)).slice().sort((a, b) => {
    const av = score(a), bv = score(b);
    if (!finite(av)) return !finite(bv) ? a.id.localeCompare(b.id) : 1;
    if (!finite(bv)) return -1;
    return bv - av || a.id.localeCompare(b.id);
  });
}
const number = (value, suffix = '', digits = 2) => finite(value) ? `${fmt(value, digits)}${suffix}` : '—';
const signed = (value, suffix = '') => finite(value) ? `${value > 0 ? '+' : ''}${fmt(value, 3)}${suffix}` : '—';
const route = (underlying, pair) => `#warrant-spreads?underlying=${encodeURIComponent(underlying)}${pair ? `&pair=${encodeURIComponent(pair)}` : ''}`;
const label = p => `${p.a.code} / ${p.b.code} · ${warrantCp(p.cp)} · K ${fmt(p.strike, 3)} · ${p.expiry}`;

export function createSpreadWatch({ load, table, tr, select, metric, dateTag, empty }) {
  const state = { underlying: '', sort: 'gap', historical: false, page: 0, period: '60' };
  let choices = [], listExport = [], historyExport = [], eventExport = [], currentCode = '', currentPair = '';
  async function page() {
    const index = await load('warrant-spreads');
    choices = index.items.map(i => ({ code: i.code, name: i.name, label: `${i.code} · ${i.name}` }));
    const params = new URLSearchParams(location.hash.split('?')[1] || '');
    const code = params.get('underlying') || (choices.some(i => i.code === '2330') ? '2330' : choices[0]?.code || '');
    const item = index.items.find(i => i.code === code);
    currentCode = code;
    if (state.underlying !== code) { state.underlying = code; state.page = 0; }
    const control = `<section class="panel"><div class="panel-head"><div><span class="eyebrow">WARRANT SPREAD RESEARCH</span><h2>按個股觀察權證價差</h2><p class="muted">同條件配對 · 來源資料日累積 · 每日免費更新</p></div><a class="text-link" href="#sources">資料與更新 →</a></div><div class="filters"><label class="search">標的代碼或完整名稱<input id="sp-underlying" list="sp-underlyings" value="${esc(code)}" placeholder="2330 或 台積電"><datalist id="sp-underlyings">${choices.map(i => `<option value="${esc(i.code)}">${esc(i.name)}</option>`).join('')}</datalist></label><button id="sp-find">觀察價差</button>${select('sp-sort', '配對排序', ['gap', 'z', 'history'], state.sort, v => ({ gap: '絕對價差由大到小', z: '絕對 Z 值由大到小', history: '累積資料由多到少' }[v]))}<label class="check"><input id="sp-historical" type="checkbox" ${state.historical ? 'checked' : ''}> 包含歷史配對</label></div><p id="sp-error" role="status" class="status-bad"></p><p class="note">只比較相同標的、認購／認售、履約價、到期日及歐式／美式的權證；行情與條款須同日，且兩檔都有成交。一般國內股票／ETF 權證納入；外國標的、特殊型商品、缺值及零成交不推估。</p></section>`;
    const caveat = `<div class="notice warning"><strong>收盤價差研究，尚未驗證可成交套利。</strong> 缺少同步買賣價、掛單量、完整結算條款與交易／避險成本。跨發行人差異亦未消除；不提供成本後報酬或買賣指令。</div>`;
    if (!item) return caveat + control + empty('找不到此標的；請輸入完整代碼或名稱。');
    const data = await load(`warrant-spreads/${code}`);
    const pairs = sortSpreadPairs(data.pairs, state.sort, state.historical);
    const requested = params.get('pair');
    const selected = data.pairs.find(p => p.id === requested) || pairs[0];
    state.page = Math.max(0, Math.min(state.page, Math.ceil(pairs.length / 25) - 1));
    listExport = pairs.map(p => ({ underlying: code, a: p.a.code, b: p.b.code, date: p.latest?.date,
      cp: warrantCp(p.cp), strike: p.strike, expiry: p.expiry, style: p.style, aRatio: p.a.ratio, bRatio: p.b.ratio,
      spread: p.latest?.spread, z: p.latest?.z, priorCount: p.latest?.priorCount, status: p.latest?.status,
      coverage: '收盤價差研究；非可成交套利；成本未評估' }));
    const current = data.pairs.filter(p => finite(p.latest?.spread));
    const signalCount = current.filter(p => p.latest.status === '異常待觀察').length;
    const ready = current.filter(p => finite(p.latest?.z)).length;
    const status = index.status === 'retained' || data.status !== 'ok' ? `<div class="notice warning">${esc(index.error || '部分行情來源更新異常；保留上次成功結果。')} 請核對資料日期。</div>` : '';
    const stale = daysUntil(data.date) < -4 ? '<div class="notice warning">資料距今超過 4 天，可能包含休市日；這些結果不代表今日可交易訊號。</div>' : '';
    const metrics = `<div class="metrics">${metric('最新資料日可比較', fmt(current.length), `組 · ${data.date || '尚無資料'}`)}${metric('已累積資料日', fmt(data.dates.length), '至少 21 個完整配對日才有 Z 值')}${metric('可計算 Z 值', fmt(ready), '組 · 需要前 20 日有效價差')}${metric('達異常門檻', ready ? fmt(signalCount) : '—', ready ? '組 · |Z| ≥ 2，非交易建議' : '資料或變異不足，尚未判定異常')}</div>`;
    const list = `<section class="panel"><div class="panel-head"><div><h2>${esc(code)} · ${esc(data.name)} 的配對觀察</h2><p class="muted">${state.historical ? '含曾收錄的歷史配對／條款版本' : '最新資料日有有效收盤資料的配對'} · ${fmt(pairs.length)} 組</p></div><button id="sp-export-list" ${pairs.length ? '' : 'disabled'}>匯出配對 CSV</button></div>${pairs.length ? table(['配對 A / B', '共同履約條件', '換算後 A / B 價格', '相對價差', 'Z 值 / 狀態', '前期有效日數'], pairs.slice(state.page * 25, (state.page + 1) * 25).map(p => {
      const point = p.points.find(r => r[0] === data.date);
      return tr([`<a class="text-link" href="${route(code, p.id)}">${esc(p.a.code)} · ${esc(p.a.name)}<small>${esc(p.b.code)} · ${esc(p.b.name)}</small></a>`, `${warrantCp(p.cp)} / ${esc(p.style)}<small>K ${fmt(p.strike, 3)} · ${esc(p.expiry)}</small>`, `${number(point ? point[1] / p.a.ratio : null, '', 4)}<small>${number(point ? point[2] / p.b.ratio : null, '', 4)}</small>`, number(p.latest?.spread, '%', 3), `${number(p.latest?.z)}<small class="${p.latest?.status === '異常待觀察' ? 'status-bad' : ''}">${esc(p.latest?.status || '尚無資料')}</small>`, `${fmt(p.latest?.priorCount)} / 20`]);
    })) : empty('目前沒有符合全部條件的有效配對。可能是履約條件不同、零成交或資料缺漏；不會放寬條件製造訊號。') }<div class="pagination"><span>第 ${state.page + 1} / ${Math.max(1, Math.ceil(pairs.length / 25))} 頁</span><div><button data-sp-page="-1" ${state.page === 0 ? 'disabled' : ''}>上一頁</button><button data-sp-page="1" ${(state.page + 1) * 25 >= pairs.length ? 'disabled' : ''}>下一頁</button></div></div><p class="note">換算價格＝每單位權證收盤價 ÷ 行使比例。相對價差＝200 ×（換算 A − 換算 B）÷（換算 A ＋ 換算 B）；正值表示 A 較高，負值表示 B 較高。代碼排序固定 A/B，不依價格重新排序；百分比不是預期報酬。</p></section>`;
    const e = data.eligibility;
    const quality = `<section class="panel"><h2>資料完整度</h2><p class="muted">官方清單 ${fmt(e.total)} 檔 · 本次符合基本條件 ${fmt(e.eligible)} 檔 · 來源更新不足 ${fmt(e.sourceUnavailable)} 檔</p><div class="spread-quality">${Object.entries(e.excluded).map(([reason, count]) => `<span class="tag">${esc(reason)} ${fmt(count)} 檔</span>`).join('')}</div><p class="note">符合基本條件仍須找到另一檔條件相同的權證才能配對。隱含波動率、Delta、買賣報價、掛單量、交易成本、借券／放空可行性及結算條款尚未完整取得，均標示未評估，不補成零。</p></section>`;
    let detail = '';
    historyExport = []; eventExport = []; currentPair = '';
    if (selected) {
      const p = selected, back = p.backtest.summary, latest = p.latest;
      currentPair = p.id;
      historyExport = p.observations.map(o => ({ underlying: code, a: p.a.code, b: p.b.code, ...o, model: data.rules.version }));
      eventExport = p.backtest.events.map(e => ({ underlying: code, a: p.a.code, b: p.b.code, ...e, model: data.rules.version, interpretation: '偏離收斂事件，非交易損益' }));
      const currentPoint = p.points.find(r => r[0] === data.date);
      const events = p.backtest.events.slice().reverse();
      const eventStatus = { evaluated: '已評估', pending: '等待後續資料', missing: '缺資料／條款變更' };
      detail = `<section class="panel"><div class="panel-head"><div><span class="eyebrow">PAIR HISTORY</span><h2>${esc(p.a.code)} / ${esc(p.b.code)} 價差時間序列</h2><p class="muted">${esc(label(p))} · ${esc(p.style)}</p></div>${dateTag(data.date)}</div><div class="filters compact">${select('sp-period', '圖表範圍', ['20', '60', '252'], state.period, v => `最近 ${v} 個資料日`)}<button id="sp-export-history">匯出價差歷史 CSV</button></div><div class="spread-pair-cards"><div><strong>A · ${esc(p.a.name)}</strong><p>收盤 ${number(currentPoint?.[1], ' 元', 3)} · 行使比例 ${fmt(p.a.ratio, 6)}<br>當日成交 ${number(currentPoint?.[3], ' 張', 3)}</p><a class="text-link" href="#daily-watch?code=${esc(p.a.code)}">價格與分點 →</a></div><div><strong>B · ${esc(p.b.name)}</strong><p>收盤 ${number(currentPoint?.[2], ' 元', 3)} · 行使比例 ${fmt(p.b.ratio, 6)}<br>當日成交 ${number(currentPoint?.[4], ' 張', 3)}</p><a class="text-link" href="#daily-watch?code=${esc(p.b.code)}">價格與分點 →</a></div></div><p class="note">已取得 ${fmt(p.points.length)} 個有效配對日；本期狀態：${esc(latest?.status || '尚無資料')}。除權息或行使比例等條款改變會建立新序列，重新累積，避免把今日條款套回過去。</p></section>
      <div class="split watch-charts"><section class="panel"><h2>相對價差歷史</h2><p class="muted">% · A/B 固定代碼順序</p>${trendChart(p.observations.map(o => [o.date, o.spread]), 1, '相對價差歷史', '%', Number(state.period))}</section><section class="panel"><h2>歷史偏離 Z 值</h2><p class="muted">僅使用當日前 20 個有效資料日</p>${finite(latest?.z) || p.observations.some(o => finite(o.z)) ? trendChart(p.observations.map(o => [o.date, o.z]), 1, '歷史偏離 Z 值', '倍標準差', Number(state.period)) : empty('歷史不足或變異不足，尚無 Z 值。至少需要前 20 個有效配對日，再加當日觀察；缺值會中斷累積。')}</section></div>
      <section class="panel"><div class="panel-head"><div><span class="eyebrow">SIGNAL EVENT BACKTEST</span><h2>訊號回測：價差偏離是否收斂</h2><p class="muted">每個配對獨立統計 · 非交易損益回測</p></div><button id="sp-export-events" ${events.length ? '' : 'disabled'}>匯出回測事件 CSV</button></div><div class="metrics">${metric('觸發事件', fmt(back.signals), `已評估 ${back.evaluated} · 待觀察 ${back.pending} · 缺值 ${back.missing}`)}${metric('偏離收斂比例', number(back.convergenceRate, '%', 1), '分母僅含已評估事件；不是交易勝率')}${metric('收斂幅度中位數', number(back.medianContraction, ' pp', 3), '百分點；正數表示更接近訊號前均值')}${metric('可評估樣本', fmt(back.evaluated), back.sampleInsufficient ? '少於 30 筆，僅供描述性觀察' : '歷史事件不代表未來表現')}</div>${back.sampleInsufficient ? '<div class="notice warning">回測樣本不足，尚不能推論策略有效或有獲利能力。最早需 27 個完整資料日且出現異常訊號，才可能評估第一筆事件。</div>' : ''}${events.length ? table(['訊號日 / Z 值', '次日觀察基準', '評估日', '起始偏離', '期末偏離', '收斂幅度（pp）', '狀態'], events.map(e => tr([`${esc(e.signalDate)}<small>Z ${number(e.signalZ)}</small>`, esc(e.referenceDate || '待累積'), esc(e.evaluationDate || '待累積'), number(e.referenceDeviation), number(e.endDeviation), signed(e.contraction), eventStatus[e.status] || '未知']))) : empty('尚無符合規則的回測事件；不顯示虛構報酬、勝率或獲利金額。')}<p class="note">訊號日 t：用前 20 日均值及樣本標準差計算 Z，|Z| ≥ 2 觸發。以 t+1 的收盤價差作觀察基準，t+6 評估，觀察 5 個已收錄資料日；不假設在訊號日收盤成交。偏離均以訊號日已知的均值固定計算。配對事件期間不重疊；中途缺值或條款變更列為缺值，不當作成功或延後補價。這是價差事件研究，尚未建模真實買賣、資金或避險。</p></section>`;
    }
    const method = `<section class="panel source-list"><h2>免費更新與回測口徑</h2><p>沿用每天台北時間 08:43、11:43、19:43、22:43 的免費更新。價差配對自功能啟用後按來源資料日累積，最多保留最近 252 個資料日（並清除超過 550 個日曆日的資料）；每日重跑同一日期只修正該日。歷史配對與舊條款版本保留在窗口內，不只回看今天仍有成交的權證。</p><p>不從只有收盤價、缺少當日條款的舊檔倒填回測。資料修正可能重算歷史結果；配對門檻固定，沒有用回測結果挑選最佳參數。20 日、2 倍、5 日是研究設定，並非已驗證最佳策略；30 筆只是樣本提示門檻，不是策略驗證標準；配對之間可能共享同一權證，不能把事件數或績效當成獨立投資組合。</p><p>已收錄資料日不保證連續交易日，來源停更時不虛構資料；零成交、不同日期、未知履約方式及缺價格不推估。Z 值只是歷史相對偏離，並非錯價證明；低估及價差收斂也不保證有可成交利潤。</p><p>來源與條款說明：<a href="https://www.twse.com.tw/zh/products/securities/warrant/educate/qa.html" target="_blank" rel="noopener noreferrer">證交所權證教育說明 ↗</a>。<a href="https://warrants.twse.com.tw/Default.aspx" target="_blank" rel="noopener noreferrer">官方權證揭露平台 ↗</a> 的盤後抽樣報價也不等於盤中即時可成交報價；本版未將其作為成交依據。</p></section>`;
    return caveat + control + status + stale + metrics + list + detail + quality + method;
  }
  function bind(render) {
    const $ = id => document.getElementById(id);
    const find = () => {
      const code = resolveWarrantUnderlying(choices, $('sp-underlying').value);
      if (!code) { $('sp-error').textContent = '找不到唯一標的，請輸入完整代碼或名稱。'; return; }
      state.page = 0;
      if (location.hash === route(code)) render(); else location.hash = route(code);
    };
    $('sp-find').addEventListener('click', find);
    $('sp-underlying').addEventListener('keydown', e => { if (e.key === 'Enter') find(); });
    $('sp-sort').addEventListener('change', () => { state.sort = $('sp-sort').value; state.page = 0; render(); });
    $('sp-historical').addEventListener('change', () => { state.historical = $('sp-historical').checked; state.page = 0; render(); });
    $('sp-period')?.addEventListener('change', () => { state.period = $('sp-period').value; render(); });
    document.querySelectorAll('[data-sp-page]').forEach(el => el.addEventListener('click', () => { state.page += Number(el.dataset.spPage); render(); }));
    const download = (rows, fields, suffix) => {
      const url = URL.createObjectURL(new Blob([toCSV(rows, fields)], { type: 'text/csv;charset=utf-8' }));
      const a = document.createElement('a'); a.href = url; a.download = `warrant-spreads-${currentCode}-${suffix}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    };
    const identity = [['underlying', '標的代碼'], ['a', '權證A'], ['b', '權證B']];
    $('sp-export-list')?.addEventListener('click', () => download(listExport, [...identity, ['date', '資料日'], ['cp', '權別'], ['strike', '履約價'], ['expiry', '到期日'], ['style', '履約方式'], ['aRatio', 'A行使比例'], ['bRatio', 'B行使比例'], ['spread', '相對價差%'], ['z', 'Z值'], ['priorCount', '前期有效日數'], ['status', '狀態'], ['coverage', '口徑']], 'pairs'));
    $('sp-export-history')?.addEventListener('click', () => download(historyExport, [...identity, ['date', '資料日'], ['spread', '相對價差%'], ['mean', '前20日均值'], ['z', 'Z值'], ['priorCount', '前期有效日數'], ['status', '狀態'], ['model', '模型版本']], currentPair));
    $('sp-export-events')?.addEventListener('click', () => download(eventExport, [...identity, ['signalDate', '訊號日'], ['signalZ', '訊號Z值'], ['baselineMean', '固定比較均值'], ['referenceDate', '觀察基準日'], ['evaluationDate', '評估日'], ['referenceDeviation', '起始偏離'], ['endDeviation', '期末偏離'], ['contraction', '收斂幅度百分點'], ['status', '狀態'], ['model', '模型版本'], ['interpretation', '口徑']], `${currentPair}-events`));
  }
  return { page, bind };
}
