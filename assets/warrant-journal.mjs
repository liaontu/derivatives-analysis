import {esc, finite, fmt, taipeiTime, toCSV} from './model.mjs';

export const signalLabels = {volume:'成交量放大',spread:'Z 值異常',buy:'分點持續買超',sell:'分點持續賣超','to-buy':'分點賣超轉買超','to-sell':'分點買超轉賣超'};
const signed = v => finite(v) ? (v>0?'+':'')+fmt(v,2)+'%' : '—';
const side = cp => cp==='C'?'認購':cp==='P'?'認售':'未確認';
const rate = (n,d) => d>0?fmt(n/d*100,2)+'%':'—';
const labels = tags => (tags||[]).map(t=>signalLabels[t]||t).join('、');
export function filterJournal(rows, query='', tag='all', cp='all') {
  const q=query.trim().toLocaleLowerCase();
  return rows.filter(r=>(cp==='all'||r.cp===cp) && (tag==='all'||r.tags?.includes(tag)) &&
    (!q || [r.code,r.name,r.underlying,r.underlyingName,r.branchCode,r.branchName].some(v=>String(v||'').toLocaleLowerCase().includes(q))));
}
export function heatRanking(stocks) {
  return stocks.filter(s=>s.eligible>=10 && s.call+s.put>0)
    .sort((a,b)=>(b.call+b.put)/b.eligible-(a.call+a.put)/a.eligible || b.call+b.put-a.call-a.put || a.code.localeCompare(b.code));
}

export function createWarrantJournal({load,table,tr,select,metric}) {
  const state={query:'',branch:'',tag:'all',cp:'all',page:0,bpage:0,opage:0,mode:'recorded',horizon:'5'};
  let exported=[], currentDay='', selectedRevision='';
  const pageSize=10;
  function pager(key,count,size=pageSize) {
    const pages=Math.max(1,Math.ceil(count/size));
    state[key]=Math.min(state[key],pages-1);
    return `<div class="pagination"><span>共 ${fmt(count)} 筆 · ${state[key]+1}／${pages} 頁</span><div><button data-journal-page="${key}" value="-1" ${state[key]===0?'disabled':''}>上一頁</button><button data-journal-page="${key}" value="1" ${state[key]+1>=pages?'disabled':''}>下一頁</button></div></div>`;
  }
  function card(r) {
    const expiry=r.remainingDays===null?'到期日缺資料':r.remainingDays<0?'已到期':`剩餘 ${r.remainingDays} 日`;
    return `<article class="journal-card"><div class="panel-head"><div><strong>${esc(r.code)} ${esc(r.name)}</strong><small>${esc(r.underlying)} ${esc(r.underlyingName)} · ${side(r.cp)} · ${r.market==='TWSE'?'上市':'上櫃'}</small></div><span class="tag ${r.remainingDays!==null&&r.remainingDays<=30?'status-bad':''}">${expiry}</span></div>
      <p class="journal-tags">${r.tags.map(t=>`<span class="tag">${esc(signalLabels[t])}</span>`).join(' ')}</p>
      <dl class="journal-values"><div><dt>權證漲跌</dt><dd>${signed(r.warrantPct)}</dd></div><div><dt>標的漲跌</dt><dd>${signed(r.stockPct)}</dd></div><div><dt>均量倍數</dt><dd>${finite(r.multiple)?fmt(r.multiple,2)+' 倍':'—'}</dd></div></dl>
      <p class="muted">成交 ${fmt(r.volume)} 張 · 比較起日 ${esc(r.previousDate||'缺資料')} · ${r.branchAvailable?`同日分點已取得，${r.branchSignals} 筆持續／反轉訊號`:'分點未收錄／待更新'}</p>
      <details><summary>價格、發行條件與資料完整度</summary><dl class="journal-terms">
      ${[['權證收盤',fmt(r.price,3)],['標的收盤',fmt(r.underlyingPrice,2)],['履約價',fmt(r.strike,3)],['行使比例',fmt(r.ratio,5)],['履約方式',r.style||'缺資料'],['最後交易日',r.lastTrade||'缺資料'],['到期日',r.expiry||'缺資料'],['條款資料日',r.termsDate||'缺資料']].map(([k,v])=>`<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join('')}</dl>
      <p>量能：${esc(r.volumeStatus)}；前五日均量 ${fmt(r.averageVolume,2)} 張。</p>
      <p>同條件價差：${r.spread?`對照 ${esc(r.spread.peerCode)}，換算行使比例後價差 ${signed(r.spread.spread)}，Z ${fmt(r.spread.z,2)}；基準截至 ${esc(r.spread.trainingEndDate||'資料不足')}`:'沒有可核對配對／歷史不足，無法判定 Z 異常'}。</p>
      <p>條款缺欄：${r.missingTerms.length?esc(r.missingTerms.join('、')):'無'}。無成交日不填價格 0；非連續成交日的漲跌需依比較起日解讀。</p>
      <p><a href="#daily-watch?code=${esc(r.code)}">每日價格與分點 →</a> · <a href="#warrant-directory?underlying=${esc(r.underlying)}">發行資料 →</a> · <a href="#warrant-branches?code=${esc(r.code)}">分點原始排行 →</a></p></details></article>`;
  }
  function shortlist(report) {
    const rows=filterJournal(report.shortlist,state.query,state.tag,state.cp);
    exported=rows;
    const pagination=pager('page',rows.length);
    return `<section class="panel"><div class="panel-head"><div><h2>每日重點清單</h2><p>${fmt(report.summary.candidates)} 檔有訊號，選列 ${report.shortlist.length} 檔；每個標的最多 ${report.rules.maxPerUnderlying} 檔。</p></div><button id="journal-export">匯出篩選清單 CSV</button></div>
      <form id="journal-search" class="filters"><label>權證／標的<input name="query" value="${esc(state.query)}" placeholder="代碼或名稱"></label><button>搜尋</button>${select('journal-tag','訊號',['all',...Object.keys(signalLabels)],state.tag,v=>v==='all'?'全部訊號':signalLabels[v])}${select('journal-cp','權別',['all','C','P'],state.cp,v=>v==='all'?'認購＋認售':side(v))}<button type="button" id="journal-reset">清除條件</button></form>
      <p class="note">按訊號種類數、均量倍數、成交量排序，不代表獲利機率。14 日內到期與 30 日內到期均提示；目前選列 ${report.shortlist.filter(r=>r.remainingDays!==null&&r.remainingDays>=0&&r.remainingDays<=14).length} 檔在 14 日內、${report.shortlist.filter(r=>r.remainingDays>14&&r.remainingDays<=30).length} 檔在 15–30 日到期。</p>
      <div class="journal-grid">${rows.slice(state.page*pageSize,(state.page+1)*pageSize).map(card).join('')||'<p class="empty">沒有符合條件的重點項目；可查看完整量價日報。</p>'}</div>${pagination}</section>`;
  }
  function branches(report) {
    const rows=filterJournal(report.branches,state.branch,'all',state.cp);
    const pagination=pager('bpage',rows.length);
    return `<section class="panel"><h2>分點持續買賣與反轉</h2><p>連續至少 ${report.rules.branchStreak} 個交易日同方向入榜；缺資料或未入榜即中斷。${report.summary.branchCovered}／${report.summary.branchRequested} 檔追蹤權證取得同日分點。</p>
      <form id="journal-branch-search" class="filters"><label>分點／權證／標的<input name="branch" value="${esc(state.branch)}" placeholder="例如 國泰、元大、2330"></label><button>篩選分點</button></form>
      ${table(['權證／分點','觀察訊號','當日買／賣（張）','當日淨額（張）','連續／先前反向日數','五日已觀測淨額','完整度／每日證據'],rows.slice(state.bpage*pageSize,(state.bpage+1)*pageSize).map(r=>tr([
        `${esc(r.code)} ${esc(r.name)}<small>${esc(r.branchName)} · ${esc(r.branchCode)} · ${side(r.cp)}</small>`,esc(labels(r.tags)),`${fmt(r.buy)}／${fmt(r.sell)}`,fmt(r.net),`${r.streakCapped?'≥':''}${r.streak}／${r.priorOppositeCapped?'≥':''}${r.priorOppositeStreak}`,fmt(r.observedNet),
        `${r.observedDays}／5 日<details><summary>查看每日紀錄</summary>${r.points.map(([d,n,s])=>`<p>${esc(d)}：${s==='observed'?fmt(n)+' 張':s==='unranked'?'未入榜，無法觀測':'未取得排行'}</p>`).join('')}<p>入榜前三買超／賣超占成交量：${fmt(r.top3BuyPct,2)}%／${fmt(r.top3SellPct,2)}%。僅為已觀測交易比重，非持倉集中度。</p></details>`])))}${pagination}
      <p class="note">五日數字只加總有觀測值的日期，不是庫存；連續日數回看最多11日，達上限以 ≥ 顯示。來源張數取整，比重可能有誤差。分點不等於單一投資人；買超轉賣超也不能確認同一人出貨。${Object.entries(report.branchIssues).map(([k,v])=>esc(k)+' '+v+' 檔').join('；')}</p></section>`;
  }
  function tracking(data) {
    if (!data) return '<section class="panel"><h2>訊號後續觀察</h2><p>追蹤結果尚未產生，請稍後載入最新資料。</p></section>';
    const selected=data.items.filter(r=>r.mode===state.mode).slice().reverse();
    const summary=data.summary.filter(r=>r.mode===state.mode&&r.horizon===Number(state.horizon));
    const pagination=pager('opage',selected.length);
    return `<section class="panel"><h2>訊號後續觀察</h2><p>評估截至 ${esc(data.asOf)}；${esc(data.method)}</p>
      <div class="filters">${select('journal-mode','樣本來源',['recorded','research'],state.mode,v=>v==='recorded'?'啟用後封存觀察':'歷史放量研究（非即時）')}${select('journal-horizon','基準日後交易日',['1','3','5','10'],state.horizon,v=>v+' 日')}</div>
      <p class="notice">${esc(data.basis[state.mode])}每版重點清單前 20 檔封存，日內依權證去重；歷史研究每個資料日前 20 檔、每標的最多 2 檔。不同訊號與重複日期可能互相關聯。</p>
      ${table(['權別','樣本數／可比較','價格上漲筆數','價格變動中位數','不足原因'],summary.map(r=>tr([side(r.cp),`${r.total}／${r.comparable}`,`${r.positive}／${r.comparable}`,signed(r.medianPct),Object.entries(r.statuses).map(([k,v])=>esc(k)+' '+v).join('；')])))}
      ${table(['訊號日／權證','觀察依據','基準日／收盤','觀察日','權證／標的價格變化','狀態'],selected.slice(state.opage*pageSize,(state.opage+1)*pageSize).map(r=>{
        const result=r.results.find(p=>p.horizon===Number(state.horizon));
        const link=r.mode==='research'?`#warrant-volume-report?date=${r.signalDate}`:`#warrant-report?date=${r.signalDate}&revision=${r.revision}`;
        return tr([`<a href="${esc(link)}">${esc(r.signalDate)}</a><small>${esc(r.code)} ${esc(r.name)} · ${side(r.cp)}</small>`,esc(labels(r.tags)),`${esc(r.baselineDate||'待驗證')}<small>${fmt(r.baselinePrice,3)}</small>`,esc(result.date||'待驗證'),`${signed(result.pct)}／${signed(result.stockPct)}`,esc(result.status)]);
      }))}${pagination}<p class="note">這裡是收盤價格變化，不是模擬交易報酬；未加入手續費、稅、滑價與買賣價差。沒有可成交報價時，不宣稱成本後獲利。無成交／缺值不以零填入，不順延挑選有利日期。</p></section>`;
  }
  async function page() {
    const index=await load('daily-reports/warrant-journal/index');
    if(!index.dates.length) return `<p class="notice warning">整合日報等待同日行情與名錄。${esc(index.error||'')}</p>`;
    const query=new URLSearchParams(location.hash.split('?')[1]);
    const day=query.get('date')||index.dates.at(-1), revision=query.get('revision');
    if(!index.dates.includes(day)) return `<section class="panel"><h2>這一天尚無整合日報</h2><p>整合日報自 ${esc(index.dates[0])} 開始封存。先前已補抓的量價日報仍可查詢。</p><a href="#warrant-volume-report">歷史個股權證量價日報 →</a> · <a href="#warrant-report">最新整合報告 →</a></section>`;
    const versions=index.revisions[day]||[];
    if(revision&&!versions.some(v=>v.id===revision)) return '<p class="notice warning">找不到這份修訂版本。<a href="#warrant-report">查看最新報告</a></p>';
    const prefix='daily-reports/warrant-journal/';
    const report=await load(prefix+(revision?'revisions/'+day+'-'+revision:day));
    currentDay=day; selectedRevision=report.revision;
    const outcomes=await load(prefix+'outcomes').catch(()=>null);
    const s=report.summary, ranked=heatRanking(report.stocks).slice(0,10);
    return `<div class="daily-report warrant-journal"><section class="panel"><div class="panel-head"><div class="filters">${select('journal-date','報告行情日',index.dates.slice().reverse(),day)}${select('journal-revision','保存版本',versions.map(v=>v.id),report.revision,id=>taipeiTime(versions.find(v=>v.id===id).recordedAt))}</div><button id="journal-print">列印目前畫面／PDF</button></div>
      <p>本版保存時間：${esc(taipeiTime(report.recordedAt))}。${report.status==='partial'?'分點尚未全部更新，先提供價量與已核對分點；後續補齊另存版本。':'本期行情與追蹤清單分點日期已核對。'}</p>
      ${index.status==='retained'?`<p class="notice warning">最近產生報告失敗，保留已保存版本：${esc(index.error)}</p>`:''}
      <div class="metrics">${metric('可比較權證',fmt(s.eligible),`${fmt(s.total)} 檔中的 ${rate(s.eligible,s.total)}`)}${metric('放量權證',fmt(s.volumeHits),'分母為有五日基準的可比較權證')}${metric('同日分點涵蓋',fmt(s.branchCovered),`${s.branchRequested} 檔追蹤清單；占名錄 ${rate(s.branchCovered,s.total)}`)}${metric('持續／反轉紀錄',fmt(s.branchSignals),'每筆為一檔權證與一個分點')}</div>
      <p>觀察訊號：${Object.entries(s.signals).map(([k,v])=>esc(signalLabels[k])+' '+fmt(v)+' 檔').join(' · ')||'本期沒有符合門檻的項目'}。不同訊號可能出現在同一檔權證，不可直接相加。</p>
      <p class="note">分點總清單 ${s.branchUniverse} 檔，本報告僅納入個股權證 ${s.branchRequested} 檔；ETF／指數權證另在原始分點頁查詢。同條件配對 ${fmt(report.spreadCoverage.paired)} 檔，可計算 Z ${fmt(report.spreadCoverage.zReady)} 檔；其餘無法判定 Z 異常。</p>
      <p><a href="#warrant-volume-report?date=${esc(day)}">完整量價日報與近月歷史 →</a> · <a href="#warrant-branches">原始分點排行 →</a> · <a href="#warrant-signal-backtest">分點線索回測 →</a> · <a href="data/derivatives/${prefix}revisions/${esc(day)}-${esc(report.revision)}.json" download>下載本版 JSON</a></p></section>
      <section class="panel"><h2>標的權證放量熱度</h2><p>至少 10 檔可比較權證的標的，按放量占比列前 10 名；同時呈現分母，認購、認售分開。</p>
      ${table(['標的','放量／可比較','放量比例','認購放量／可比','認售放量／可比'],ranked.map(r=>tr([`<button class="text-link" data-journal-stock="${esc(r.code)}">${esc(r.code)} ${esc(r.name)}</button>`,`${r.call+r.put}／${r.eligible}`,rate(r.call+r.put,r.eligible),`${r.call}／${r.callBase}`,`${r.put}／${r.putBase}`])))}</section>
      ${shortlist(report)}${branches(report)}
      <section class="panel"><h2>前期重點追蹤</h2><p>${report.previousDate?'比較上一份整合日報 '+esc(report.previousDate):'第一份整合日報；之後每天對照前期清單。'}</p>
      ${report.changes.length?`<details><summary>查看 ${report.changes.length} 檔的持續與變化</summary>${table(['權證','前期訊號','本期訊號','觀察結果'],report.changes.map(r=>tr([esc(r.code)+' '+esc(r.name),esc(labels(r.before)),esc(labels(r.after)||'未觀測到'),esc(r.status)])))}</details>`:''}</section>
      ${tracking(outcomes)}
      <details class="panel journal-method"><summary>規則、缺值與來源追蹤</summary><p>規則版本 ${esc(report.rules.version)}。${esc(report.rules.description)}</p><p>五日均量基準：${report.baselineDates.map(esc).join('、')}；當日不納入基準。</p>
      <p>量價排除原因：${Object.entries(report.volumeExcluded).map(([k,v])=>esc(k)+' '+fmt(v)+' 檔').join('；')}。</p>
      ${report.limits.map(v=>'<p>'+esc(v)+'</p>').join('')}
      ${table(['來源','資料日','抓取時間'],report.sources.map(r=>tr([`<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.id)}</a>`,esc(r.date),esc(taipeiTime(r.fetchedAt))])))}
      <p>公司名錄日期：${esc(JSON.stringify(report.directoryDates))}。逐檔分點來源與校驗摘要保留於本版 JSON；歷史排行可能修訂，保存版本不覆寫。</p></details></div>`;
  }
  function bind(rerender) {
    document.getElementById('journal-date')?.addEventListener('change',e=>{state.page=state.bpage=state.opage=0;location.hash='warrant-report?date='+e.target.value;});
    document.getElementById('journal-revision')?.addEventListener('change',e=>{location.hash=`warrant-report?date=${currentDay}&revision=${e.target.value}`;});
    document.getElementById('journal-print')?.addEventListener('click',()=>window.print());
    document.getElementById('journal-search')?.addEventListener('submit',e=>{e.preventDefault();state.query=e.target.elements.query.value;state.page=0;rerender();});
    document.getElementById('journal-branch-search')?.addEventListener('submit',e=>{e.preventDefault();state.branch=e.target.elements.branch.value;state.bpage=0;rerender();});
    document.getElementById('journal-reset')?.addEventListener('click',()=>{state.query='';state.tag=state.cp='all';state.page=state.bpage=0;rerender();});
    for(const [id,key] of [['journal-tag','tag'],['journal-cp','cp'],['journal-mode','mode'],['journal-horizon','horizon']]) document.getElementById(id)?.addEventListener('change',e=>{state[key]=e.target.value;state.page=state.bpage=state.opage=0;rerender();});
    document.querySelectorAll('[data-journal-page]').forEach(el=>el.addEventListener('click',()=>{state[el.dataset.journalPage]+=Number(el.value);rerender();}));
    document.querySelectorAll('[data-journal-stock]').forEach(el=>el.addEventListener('click',async()=>{state.query=el.dataset.journalStock;state.page=0;await rerender();document.getElementById('journal-search')?.scrollIntoView({block:'start'});}));
    document.getElementById('journal-export')?.addEventListener('click',()=>{
      const rows=exported.map(r=>({...r,signals:labels(r.tags),missing:r.missingTerms.join('、'),revision:selectedRevision}));
      const fields=[['date','資料日'],['code','權證代碼'],['name','名稱'],['underlying','標的'],['cp','權別'],['signals','觀察訊號'],['price','收盤'],['volume','成交張數'],['multiple','五日均量倍數'],['previousDate','漲跌比較起日'],['warrantPct','權證漲跌百分比'],['stockPct','標的漲跌百分比'],['strike','履約價'],['ratio','行使比例'],['style','履約方式'],['lastTrade','最後交易日'],['expiry','到期日'],['termsDate','條款資料日'],['missing','缺少條款欄位'],['revision','保存版本']];
      const url=URL.createObjectURL(new Blob([toCSV(rows,fields)],{type:'text/csv;charset=utf-8'}));
      const a=document.createElement('a');a.href=url;a.download=`warrant-journal-${currentDay}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
  }
  return {page,bind};
}
