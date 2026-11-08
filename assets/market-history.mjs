import {esc,fmt,taipeiTime} from './model.mjs';

export const safeArchivePath=p=>typeof p==='string'&&/^market-history\/[a-z-]+\/\d{4}-\d{2}-\d{2}-[a-f0-9]{12}\.json\.gz$/.test(p);
export function filterHistory(rows,contract,session){
  return rows.filter(r=>(!contract||r.contract===contract)&&(!session||r.session===session));
}
const labels={date:'資料日',contract:'商品',name:'名稱',month:'到期別',sourceMonth:'來源到期別',session:'時段',cp:'權別',strike:'履約價',last:'成交收盤',price:'結算價',volume:'成交量',oi:'未平倉',bid:'買價',ask:'賣價',open:'開盤',high:'最高',low:'最低',changePct:'漲跌 %',investor:'法人',buy:'買進',sell:'賣出',net:'淨交易量',longOi:'多方未平倉',shortOi:'空方未平倉',netOi:'淨未平倉',traderType:'交易人類別',top5Buy:'前五大多方',top5Sell:'前五大空方',top10Buy:'前十大多方',top10Sell:'前十大空方',code:'期貨商代碼',expiry:'到期日',symbol:'契約代碼',putVolume:'賣權量',callVolume:'買權量',volumeRatio:'成交 P/C',putOi:'賣權未平倉',callOi:'買權未平倉',oiRatio:'未平倉 P/C'};
export function coverageTable(index,{table,tr}){
  return table(['資料','收錄／應有日期','缺日與範圍'],index.coverage.map(r=>tr([esc(r.label),`${r.dates.length}／${r.expected??'日曆未驗證'}`,esc(r.missingDates.join('、')||'本期無缺日')+`<small>${esc(r.note)}</small>`])));
}
export function fcmAudit(audit,{table,tr}){
  if(!audit)return '';
  return `<details class="fcm-source-audit"><summary>期貨商免費來源與補抓驗證（${esc(audit.checkedAt)}）</summary><p>${esc(audit.conclusion)}</p>${audit.correction?`<p>${esc(audit.correction)}</p>`:''}${audit.license?`<p>${esc(audit.license.provider)}；${esc(audit.license.attribution)} <a href="https://data.gov.tw/license" target="_blank" rel="noopener">授權條款</a></p>`:''}${table(['來源','查核結果'],audit.checks.map(x=>tr([/^https:\/\//.test(x.url)?`<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.name)}</a>`:esc(x.name),esc(x.result)])))}<p><a href="#derivative-report?kind=monthly">查看官方完整月表分析 →</a></p></details>`;
}
async function readArchive(entry){
  if(!safeArchivePath(entry.path))throw Error('歷史檔案路徑不符');
  const response=await fetch('data/derivatives/'+entry.path);
  if(!response.ok)throw Error('歷史檔下載失敗');
  const raw=await new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
  const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',raw))].map(x=>x.toString(16).padStart(2,'0')).join('');
  if(hash!==entry.sha256)throw Error('歷史檔校驗不符，請重新整理');
  return JSON.parse(new TextDecoder().decode(raw));
}
export function createMarketHistory(deps){
  const state={feed:'futures',day:'',contract:'',session:'',page:0};
  let cachedKey='',cached;
  async function page(){
    const index=await deps.load('market-history');
    const choices=index.coverage.map(x=>x.id), info=index.coverage.find(x=>x.id===state.feed)||index.coverage[0];state.feed=info.id;
    const dates=Object.keys(index.feeds[state.feed]||{}).sort().reverse();
    if(!dates.includes(state.day))state.day=dates[0]||'';
    const entry=index.feeds[state.feed]?.[state.day];
    let rows=[],failure='';
    if(entry){
      try{
        if(cachedKey!==entry.path){cached=await readArchive(entry);cachedKey=entry.path;}
        if(cached.date!==state.day||cached.feed!==state.feed)throw Error('歷史檔日期或來源不符');
        rows=cached.rows;
      }catch(e){failure=e.message;}
    }
    const contracts=[...new Set(rows.map(r=>r.contract))].sort(), sessions=[...new Set(rows.map(r=>r.session).filter(Boolean))].sort();
    if(!contracts.includes(state.contract))state.contract='';
    if(!sessions.includes(state.session))state.session='';
    const filtered=filterHistory(rows,state.contract,state.session),pages=Math.max(1,Math.ceil(filtered.length/100));state.page=Math.min(state.page,pages-1);
    const shown=filtered.slice(state.page*100,(state.page+1)*100), fields=[...new Set(shown.flatMap(r=>Object.keys(r)))];
    const value=v=>v===null||v===undefined?'—':typeof v==='number'?fmt(v):Array.isArray(v)?`<details><summary>部位分類明細</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(JSON.stringify(v,null,2))}</pre></details>`:esc(v);
    return `<section class="panel"><h2>逐日資料覆蓋檢查</h2><p>${esc(index.start)}～${esc(index.end)}；最後檢查 ${esc(taipeiTime(index.checkedAt))}（台北）。${index.status==='retained'?'本次補抓有失敗，保留已驗證資料。':''}</p>${coverageTable(index,deps)}<details><summary>範圍與限制</summary><ul>${index.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul><p>美國日期依 <a href="${esc(index.calendar.us)}">Cboe 公告日曆</a>；CFTC 依週二部位日期檢查，不套用台灣日曆。</p></details>${index.errors?.length?'<p class="notice warning">'+index.errors.map(x=>esc(x.error)).join('；')+'</p>':''}</section>
      <section class="panel">${fcmAudit(index.fcmSourceAudit,deps)}</section>
      <section class="panel"><h2>歷史全表查詢</h2><div class="filters">${deps.select('mh-feed','資料類別',choices,state.feed,id=>index.coverage.find(x=>x.id===id).label)}${deps.select('mh-day','資料日期',dates,state.day)}${deps.select('mh-contract','商品',['',...contracts],state.contract,x=>x||'全部商品')}${sessions.length?deps.select('mh-session','交易時段',['',...sessions],state.session,x=>x||'全部時段'):''}</div>
      <p>符合條件 ${fmt(filtered.length)} 筆，每頁 100 筆，第 ${state.page+1}／${pages} 頁。<button id="mh-prev" ${state.page===0?'disabled':''}>上一頁</button> <button id="mh-next" ${state.page+1>=pages?'disabled':''}>下一頁</button></p>
      ${failure?'<p class="notice warning">'+esc(failure)+'</p>':deps.table(fields.map(x=>labels[x]||x),shown.map(r=>deps.tr(fields.map(k=>value(r[k])))))}
      <p>「—」是缺值；無成交不畫成價格 0。所有到期別統一為 999912（歷史 CSV 原碼 999999），666666 為週契約合計。交易人類別 0 為全部排行、1 為其中的特定法人。P/C：台灣為 %、美國為倍。</p>
      ${entry?`<p><a href="data/derivatives/${esc(entry.path)}" download>下載本日完整資料（gzip JSON）</a> · ${/^https:\/\//.test(entry.evidence?.url||'')?'<a href="'+esc(entry.evidence.url)+'">官方來源</a>':''}</p><details><summary>檔案校驗與來源證據</summary><p style="overflow-wrap:anywhere"><code>${esc(entry.sha256)}</code></p><p>壓縮後 ${fmt(entry.bytes)} bytes；${fmt(entry.rows)} 筆、${fmt(entry.contracts)} 商品。</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(JSON.stringify(entry.evidence,null,2))}</pre></details>`:''}
      </section><p><a href="#derivative-report">查看重算後的期貨與選擇權日／週／月報 →</a> · <a href="#option-history">一般時段固定規則回測 →</a></p>`;
  }
  function bind(rerender){
    for(const field of ['feed','day','contract','session'])document.getElementById('mh-'+field)?.addEventListener('change',e=>{state[field]=e.target.value;state.page=0;rerender();});
    for(const [id,step] of [['prev',-1],['next',1]])document.getElementById('mh-'+id)?.addEventListener('click',()=>{state.page+=step;rerender();});
  }
  return {page,bind};
}
