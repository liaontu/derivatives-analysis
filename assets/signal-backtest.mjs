import {esc,finite,fmt,taipeiTime,toCSV} from './model.mjs';

export const statusLabels={ok:'可比較',pending:'待觀察',calendar:'交易日曆未驗證',identity:'權證身分未核對',expired:'超過最後交易／到期日','missing-base':'缺基準日資料','missing-target':'缺觀察日資料',duplicate:'日期重複，已排除',adjusted:'履約條件改變，已排除','no-trade-base':'基準日無成交／有效價','no-trade-target':'觀察日無成交／有效價','missing-stock':'缺現股價格'};
const signed=v=>finite(v)?(v>0?'+':'')+fmt(v,2)+'%':'—';
const rate=(n,d)=>d?fmt(n/d*100,1)+'%':'—';
export function filterSignals(items,{mode='research',group='all',scope='representative',query=''}={}) {
  const q=query.trim().toLocaleLowerCase();
  return items.filter(e=>e.mode===mode&&(group==='all'||e.group===group)&&(scope==='all'||e.representative)&&
    (!q||[e.underlying,e.underlyingName,e.hint.branchCode,e.hint.branchName,...e.legs.flatMap(l=>[l.code,l.name])].some(v=>String(v||'').toLocaleLowerCase().includes(q))));
}
export const horizonResult=(event,horizon)=>event.results.find(r=>r.horizon===Number(horizon));
const roleName=(group,key)=>key==='stock'?'現股':group==='both'?(key==='a'?'認購腿':'認售腿'):group.startsWith('roll-')?(key==='a'?'賣超舊權證':'買超新權證'):'買超權證';
const termLabel=v=>v==='same'?'端點條款相同':v==='changed'?'條款有變更':'端點條款未核對';
const evidenceLabel={same:'原線索仍吻合',changed:'來源線索已修訂，保留首次證據','no-longer-observed':'現有排行已不見原線索，保留首次證據','outside-window':'已超出近月清單，保留首次證據'};

export function exportSignals(items,horizon,groups,modes) {
  return items.map(e=>{
    const r=horizonResult(e,horizon),a=r.legs.a,b=r.legs.b;
    return {id:e.id,signalDate:e.signalDate,group:groups[e.group],mode:modes[e.mode],underlying:e.underlying,underlyingName:e.underlyingName,
      branch:e.hint.branchName,branchCode:e.hint.branchCode,representative:e.representative?'納入':'重疊未納入',recordedAt:e.recordedAt,
      base:e.baselineDate,target:r.date,horizon,stock:r.stock.pct,stockStatus:statusLabels[r.stock.status],
      a:e.legs[0].code,aNet:e.legs[0].net,aBase:a.basePrice,aTarget:a.targetPrice,aPct:a.pct,aStatus:statusLabels[a.status],aTerms:termLabel(a.termsStatus),
      b:e.legs[1]?.code,bNet:e.legs[1]?.net,bBase:b?.basePrice,bTarget:b?.targetPrice,bPct:b?.pct,bStatus:b?statusLabels[b.status]:'不適用',bTerms:b?termLabel(b.termsStatus):'不適用',rollGap:r.rollGap};
  });
}

export function createSignalBacktest({load,table,tr,select,metric}) {
  const state={version:'current',mode:'research',group:'all',scope:'representative',query:'',horizon:'5',page:0};let data,exported=[];
  function statRows(summary){
    return Object.entries(summary.metrics).map(([key,s])=>tr([roleName(summary.group,key),`${s.n}／${summary.mature}`,
      `${signed(s.median)}<small>平均 ${signed(s.mean)}</small>`,`${signed(s.p25)} ～ ${signed(s.p75)}`,
      `${rate(s.positive,s.n)}<small>${s.positive}／${s.n} 筆價格上漲</small>`,
      `${Object.entries(s.statuses).filter(([k])=>k!=='ok').map(([k,v])=>esc(statusLabels[k]||k)+' '+v).join('；')||'無已到期觀察缺值'}${s.unknownTerms?`<small>${s.unknownTerms} 筆可比較價格缺端點條款</small>`:''}`]));
  }
  function card(s){
    return `<article class="panel signal-card"><div class="panel-head"><h3>${esc(data.groups[s.group])}</h3><span class="tag">${s.enough?'可繼續驗證':'樣本不足'}</span></div>
      <p>原始 ${s.raw} 筆 → 去重 ${s.selected} 筆；已到觀察日 ${s.mature}、待觀察／日曆待核對 ${s.pending}。</p>
      <p><strong>現股與所需權證皆有結果：${s.usable}／${s.mature}（${rate(s.usable,s.mature)}）</strong>；來自 ${s.dates} 個訊號日、${s.stocks} 個股。</p>
      ${table(['比較對象','有效／已到觀察日','漲跌中位數','中間50%漲跌範圍','價格上漲比例','缺值與條款'],statRows(s))}
      ${s.group.startsWith('roll-')?`<p>新權證減舊權證漲跌差：中位數 ${finite(s.rollGap.median)?fmt(s.rollGap.median,2)+' 個百分點':'—'}，${s.rollGap.n} 組雙腿完整樣本。這不是等曝險組合損益。</p>`:s.group==='both'?'<p>認購與認售分開觀察，不把兩腿相加成跨式策略報酬。</p>':`<p>現股方向符合權別：${rate(s.stockDirection.positive,s.stockDirection.n)}（${s.stockDirection.positive}／${s.stockDirection.n}）；認購看漲、認售看跌，平盤不計符合。</p>`}
      <p class="note">${esc(s.assessment)}。不同標的、天期與槓桿尚未控制，不能直接按中位數高低選最佳交易法。</p></article>`;
  }
  function legCell(event,result,key){
    const leg=event.legs.find(l=>l.key===key),r=result.legs[key];if(!leg)return '不適用';
    return `<strong>${esc(leg.code)} · ${signed(r.pct)}</strong><small>${esc(roleName(event.group,key))} · 淨 ${fmt(leg.net)} 張</small><small>${fmt(r.basePrice,3)} → ${fmt(r.targetPrice,3)} 元</small><small>${esc(statusLabels[r.status]||r.status)}；${esc(termLabel(r.termsStatus))}</small><details><summary>訊號日發行條件</summary><p>${esc(leg.name||'身分未核對')}<br>履約價 ${fmt(leg.strike,3)}；行使比例 ${esc(leg.ratio??'缺資料')}<br>到期 ${esc(leg.expiry||'缺歷史條款')}；最後交易 ${esc(leg.lastTrade||'缺資料')}<br>${esc(leg.style||'履約方式缺資料')}；條款日 ${esc(leg.termsDate||'缺資料')}</p></details>`;
  }
  async function page(){
    const versions=await load('warrant-signal-backtest/versions').catch(()=>({archived:[]}));
    const archive=versions.archived.find(v=>v.version===state.version);
    if(!archive)state.version='current';
    const prefix='warrant-signal-backtest/'+(archive?archive.path+'/':'');
    data=await load(prefix+'outcomes');
    if(!data.rules)return `<p class="notice warning">線索回測尚未完成：${esc(data.error||'等待來源資料')}。</p>`;
    const rows=filterSignals(data.items,state).slice().sort((a,b)=>b.signalDate.localeCompare(a.signalDate)||a.id.localeCompare(b.id));
    const summaries=data.summary.filter(s=>s.mode===state.mode&&s.horizon===Number(state.horizon)&&(state.group==='all'||s.group===state.group)&&s.raw>0);
    const modeItems=data.items.filter(e=>e.mode===state.mode),reps=modeItems.filter(e=>e.representative);
    const mature=reps.filter(e=>{const r=horizonResult(e,state.horizon);return r.date&&r.date<=data.asOf;});
    state.page=Math.min(state.page,Math.max(0,Math.ceil(rows.length/10)-1));
    exported=exportSignals(rows,state.horizon,data.groups,data.modes).map(e=>({studyVersion:data.rules.version,...e}));
    return `<div class="daily-report signal-backtest"><section class="panel"><div class="panel-head"><div><span class="eyebrow">BROKER SIGNAL EVENT STUDY</span><h2>分點線索的後續表現</h2><p>結果截至 ${esc(data.asOf)}；${archive?'此版本已封存，保留當時研究紀錄。':'使用固定門檻，每日沿用免費行情與部分分點排行更新。'}</p></div><button id="signal-print">列印／PDF</button></div>
      <div class="filters">${select('signal-version','研究版本',['current',...versions.archived.map(v=>v.version)],state.version,v=>v==='current'?'目前版本 · '+(versions.active||data.rules.version):'已封存 · '+v)}</div>
      ${archive?`<p class="notice warning">${esc(archive.reason)} 封存日期 ${esc(taipeiTime(archive.archivedAt))}，${fmt(archive.events)} 筆原始線索；不再更新此版本。</p>`:data.status!=='ok'?`<p class="notice warning">最近計算未完成：${esc(data.error)}。顯示上次保存的結果。</p>`:''}
      ${!archive?'<p>本版排除發行商總公司分點的線索（依名稱比對推定為造市通路）；同券商其他分點仍另行標示。這不是已確認的造市帳戶身分。</p>':''}
      ${data.sourceCoverage?.excludedRecords?`<p class="notice warning">${fmt(data.sourceCoverage.excludedRecords)} 筆個股日紀錄、${fmt(data.sourceCoverage.excludedHints)} 筆線索未符合本版來源規則，已排除。等待同版歷史重建或後續更新。</p>`:''}
      <div class="filters">${select('signal-mode','樣本來源',Object.keys(data.modes),state.mode,v=>data.modes[v])}${select('signal-horizon','基準日後交易日',['1','3','5','10'],state.horizon,v=>v+' 日')}${select('signal-group','訊號分類',['all',...Object.keys(data.groups)],state.group,v=>v==='all'?'所有訊號':data.groups[v])}</div>
      <p class="notice">${esc(data.rules.baseline)} 例如訊號日後第1個交易日是基準；「其後5日」的觀察日是訊號後第6個交易日。</p>
      <div class="metrics">${metric('此來源原始線索',fmt(modeItems.length),'各腿淨張數至少100；不是大戶人數')}${metric('去除重疊後',fmt(reps.length),'同個股、同類權別11交易日只取首筆')}${metric('已到所選觀察日',fmt(mature.length),'其餘待觀察，不算失敗或零漲跌')}${metric('啟用後封存',fmt(data.items.filter(e=>e.mode==='forward').length),'與歷史重建分開；非實際成交')}</div>
      <p>啟用時間：${esc(taipeiTime(data.activatedAt))}。${state.mode==='research'?'這些資料是事後重建或遲到資料，不能當成當時已發出的交易訊號。':'只有啟用後的新資料日、且基準日開盤前封存的線索才列在此；產生時間不是已驗證的上線或成交時間。'}</p>
      <p>現股價格可獨立追蹤，不因權證缺成交價而消失。漲跌為未還原收盤變動，沒有扣除手續費、稅、滑價與買賣價差。</p>
      <p><a href="#warrant-relationship">近月價量與原始線索 →</a> · <a href="#warrant-report">每日追蹤報告 →</a></p></section>
      <section class="panel mobile-secondary" data-mobile-label="樣本資格與限制：未達門檻不可評選策略"><h2>目前哪些線索較有參考價值？</h2><p>${summaries.some(s=>s.enough)?'部分分類已達最低資料門檻，可繼續做前瞻驗證；這仍不是預測力或扣費後獲利的證明。':'目前所選分類尚未達最低資料門檻，不能可靠評選最佳訊號。下方先呈現實際漲跌與缺值，繼續累積。'}</p><p class="note">最低門檻：每組30筆完整代表樣本、10個訊號日期、已到觀察日樣本完整率70%。沒有無訊號對照組；市場整體漲跌、槓桿與天期差異仍可能主導結果。</p></section>
      <section aria-label="全市場分類結果"><h2>全市場分類比較 · 基準後 ${esc(state.horizon)} 日</h2>${summaries.map(card).join('')||'<p class="empty">此來源模式尚無符合固定門檻的線索；不以歷史回填充作啟用後紀錄。</p>'}</section>
      <section class="panel"><div class="panel-head"><div><h2>逐筆追蹤與證據</h2><p>以下搜尋只篩選明細；上方保持全市場分類比較。</p></div><button id="signal-export">匯出明細 CSV</button></div>
      <form id="signal-search" class="filters"><label>個股／權證／分點<input name="query" value="${esc(state.query)}" placeholder="例如 3583、元大、061455"></label><button>搜尋</button>${select('signal-scope','樣本範圍',['representative','all'],state.scope,v=>v==='representative'?'納入統計的代表樣本':'全部線索（含重疊）')}<button id="signal-reset" type="button">清除搜尋</button></form>
      ${table(['訊號日／個股','分點／訊號','基準日 → 觀察日','現股漲跌','權證 A','權證 B','樣本與證據'],rows.slice(state.page*10,(state.page+1)*10).map(e=>{const r=horizonResult(e,state.horizon);return tr([
        `<a href="#warrant-relationship?underlying=${esc(e.underlying)}&date=${esc(e.signalDate)}">${esc(e.signalDate)}</a><small>${esc(e.underlying)} ${esc(e.underlyingName)}</small>`,
        `${esc(e.hint.branchName)}<small>${esc(e.hint.branchCode)} · ${esc(data.groups[e.group])}</small>`,`${esc(e.baselineDate||'日曆待驗證')}<small>→ ${esc(r.date||'日曆待驗證')}</small>`,
        `${signed(r.stock.pct)}<small>${fmt(r.stock.basePrice,2)} → ${fmt(r.stock.targetPrice,2)} 元</small><small>${esc(statusLabels[r.stock.status])}</small>`,legCell(e,r,'a'),legCell(e,r,'b'),
        `${e.representative?'納入統計':'重疊訊號，未納入'}<small>${esc(evidenceLabel[e.evidenceState])}</small><details><summary>封存依據</summary><p>${esc(taipeiTime(e.recordedAt))}<br>${esc(e.modeReason)}<br>事件 ${esc(e.id)}<br>線索摘要 ${esc(e.hintHash)}</p>${e.overlapsWith?`<p>代表事件：${esc(e.overlapsWith)}</p>`:''}</details>`]);}))}
      <div class="pagination"><span>${rows.length} 筆 · 第 ${state.page+1}／${Math.max(1,Math.ceil(rows.length/10))} 頁</span><div><button data-signal-page="-1" ${state.page===0?'disabled':''}>上一頁</button><button data-signal-page="1" ${(state.page+1)*10>=rows.length?'disabled':''}>下一頁</button></div></div></section>
      <details class="panel"><summary>研究規則、資料缺口與原始紀錄</summary><p>研究版本 ${esc(data.rules.version)}；來源線索版本 ${esc(data.signalRules.version)}。</p><p>${esc(data.rules.sampling)}</p>${data.limits.map(s=>'<p>'+esc(s)+'</p>').join('')}<p>保存價格修訂 ${fmt(data.priceRevisionCount)} 筆；同一事件首次訊號證據不覆寫，後續價格可依來源修訂重算。資料不足比例以上述已到觀察日樣本為分母。</p><p><a href="data/derivatives/${esc(prefix)}cohort.json" download>首次封存線索與分點證據 JSON</a> · <a href="data/derivatives/${esc(prefix)}prices.json" download>日期價格與修訂紀錄 JSON</a> · <a href="data/derivatives/${esc(prefix)}outcomes.json" download>完整研究結果 JSON</a></p>${archive?`<p><a href="data/derivatives/${esc(prefix)}manifest.json">封存原因與 SHA-256 校驗碼</a></p>`:''}<p><a href="https://investoredu.twse.com.tw/pages/TWSE_InvestmentQA.aspx?ID=10&Page=2" target="_blank" rel="noopener noreferrer">證交所：權證價格也受波動率、時間、行使比例與流動性影響</a></p></details></div>`;
  }
  function bind(rerender){
    document.getElementById('signal-print')?.addEventListener('click',()=>window.print());
    for(const [id,key] of [['signal-version','version'],['signal-mode','mode'],['signal-horizon','horizon'],['signal-group','group'],['signal-scope','scope']])document.getElementById(id)?.addEventListener('change',e=>{state[key]=e.target.value;state.page=0;rerender();});
    document.getElementById('signal-search')?.addEventListener('submit',e=>{e.preventDefault();state.query=e.target.elements.query.value;state.page=0;rerender();});
    document.getElementById('signal-reset')?.addEventListener('click',()=>{state.query='';state.page=0;rerender();});
    document.querySelectorAll('[data-signal-page]').forEach(b=>b.addEventListener('click',()=>{state.page+=Number(b.dataset.signalPage);rerender();}));
    document.getElementById('signal-export')?.addEventListener('click',()=>{
      const fields=[['studyVersion','研究版本'],['id','事件ID'],['signalDate','訊號日'],['group','訊號分類'],['mode','來源模式'],['underlying','標的代碼'],['underlyingName','標的名稱'],['branch','分點'],['branchCode','分點代碼'],['representative','樣本'],['recordedAt','首次封存UTC'],['base','基準日'],['target','觀察日'],['horizon','基準後交易日'],['stock','現股漲跌百分比'],['stockStatus','現股狀態'],['a','權證A'],['aNet','A淨張數'],['aBase','A基準價'],['aTarget','A觀察價'],['aPct','A漲跌百分比'],['aStatus','A狀態'],['aTerms','A條款'],['b','權證B'],['bNet','B淨張數'],['bBase','B基準價'],['bTarget','B觀察價'],['bPct','B漲跌百分比'],['bStatus','B狀態'],['bTerms','B條款'],['rollGap','新減舊漲跌差百分點非損益']];
      const url=URL.createObjectURL(new Blob([toCSV(exported,fields)],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`branch-signals-${data.rules.version}-${state.mode}-${state.horizon}d.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
  }
  return {page,bind};
}
