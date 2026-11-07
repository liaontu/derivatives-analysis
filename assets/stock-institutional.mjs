import {esc,fmt,finite,toCSV} from './model.mjs';
const signed=v=>finite(v)?`${v>0?'+':''}${fmt(v,2)}`:'—';
const pct=v=>finite(v)?signed(v)+'%':'—';
export const flowStreak=v=>!finite(v)?'—':v===0?'持平／中斷':`${v>0?'連買':'連賣'}${Math.abs(v)===5?'≥':''}${Math.abs(v)}日`;
export function researchRows(data,market,horizon,phase){return (data?.rows||[]).filter(r=>r.market===market&&r.horizon===Number(horizon)&&r.phase===phase);}
export function createStockInstitutional({load,table,tr,select,metric}) {
  const state={date:'',market:'TWSE',query:'',page:0,horizon:'5',phase:'all'};let shown=[];
  async function researchPage(){
    const data=await load('stock-institutional-research').catch(()=>null);
    if(!data?.rules)return '<p class="notice warning">法人歷史研究尚未完成；沒有以零或示範結果代替。</p>';
    const rows=researchRows(data,state.market,state.horizon,state.phase);
    const labels=Object.fromEntries(data.strategies.map(r=>[r.id,r.name]));
    const details=data.recentEvents.filter(e=>e.market===state.market).slice(0,20).map(e=>{const o=e.outcomes[state.horizon];return tr([esc(e.date),esc(e.code+' '+e.name),esc(labels[e.rule]),esc(e.warrant?.code||'未選到對應放量權證'),`${esc(o.entryDate)} → ${esc(o.exitDate)}`,pct(o.stockPct),pct(o.warrantPct),esc(e.inputRevised?'來源修訂，排除統計':o.status)]);});
    return `<section class="panel"><h2>法人連買賣與權證放量：歷史事件回測</h2><p>訊號期間 ${esc(data.start)}～${esc(data.asOf)} · ${data.analysisDays} 個分析資料日 · 分段界線 ${esc(data.splitDate)}</p><p><a href="#stock-institutional">返回法人每日觀察 →</a> · <a href="#series-report">日報、週報、月報 →</a></p>
      <p class="notice">事後補抓的價格事件研究，非真實成交或扣費後獲利。首個後續交易日收盤作基準；缺價不順延。前後段都屬歷史資料。</p>${data.status!=='ok'?`<p class="notice warning">研究資料有缺漏或本次未更新：${esc(data.error||data.missingDates.join('、')||'請查看來源紀錄')}</p>`:''}
      ${data.missingVolumeDates?.length?`<p class="notice warning">這些日期缺權證量價日報，無法建立放量確認訊號：${esc(data.missingVolumeDates.join('、'))}。單純法人連買仍可觀察現股。</p>`:''}
      <div class="filters">${select('si-market','市場',['TWSE','TPEX'],state.market,v=>v==='TWSE'?'上市':'上櫃')}${select('si-horizon','基準後觀察期',['1','3','5'],state.horizon,v=>v+' 個交易日')}${select('si-phase','歷史區段',['all','early','later'],state.phase,v=>({all:'全期間',early:'前段（歷史）',later:'後段（歷史）'}[v]))}</div>
      <div class="metrics">${metric('已保存事件',fmt(data.events),'全市場、四個固定規則；同股同規則七交易日去重')}${metric('分析資料日',fmt(data.analysisDays),`另含基準日，共 ${data.days} 個資料日`)}${metric('來源變動排除',fmt(data.revisedEvents),'原事件保留，修訂者不混入主統計')}</div>
      ${table(['固定規則','候選／訊號日','股／證／配對有效','現股價格變動中位數','權證價格變動中位數','現股符合方向比例','等待／缺價／跨界','樣本狀態'],rows.map(r=>tr([esc(r.label),`${r.events}／${r.signalDates}`,`${r.stockSamples}／${r.warrantSamples}／${r.pairedSamples}`,pct(r.stockMedianPct),pct(r.warrantMedianPct),pct(r.stockDirectionShare),`${r.statuses.pending||0}／${r.statuses['missing-prices']||0}／${r.statuses['boundary-excluded']||0}`,esc(r.sampleStatus)])))}
      <details><summary>逐項缺漏：各規則原始狀態計數</summary>${table(['規則','狀態／筆數'],rows.map(r=>tr([esc(r.label),Object.entries(r.statuses).map(([k,v])=>esc(k)+': '+fmt(v)).join('；')])))}<p>stock-only／warrant-only 代表只有一側價格可用；paired 為兩側皆有價格。候選含等待或排除，分段統計另列跨界數。</p></details>
      <h3>最近事件核對（本市場最多 20 筆，未套用分段篩選）</h3>${table(['訊號日','標的','規則','權證','基準 → 觀察日','現股變化','權證變化','狀態'],details)}
      <details><summary>方法、資料偏差與下載</summary><p>${esc(data.rules.baseline)} ${esc(data.rules.warrantSelection)}</p><ul>${data.limitations.map(t=>'<li>'+esc(t)+'</li>').join('')}</ul><p><a href="data/derivatives/stock-institutional-backfill.json" download>補抓來源與缺漏 JSON</a> · <a href="data/derivatives/stock-institutional-research/outcomes.json" download>全部事件與價格觀察 JSON</a> · <a href="data/derivatives/stock-institutional-research/cohort.json" download>首次封存訊號 JSON</a></p><p>輸入校驗碼 <code>${esc(data.inputSha256)}</code></p></details></section>`;
  }
  return {
    async page(){
      if(new URLSearchParams(location.hash.split('?')[1]).get('view')==='research')return researchPage();
      const index=await load('stock-institutional');
      if(!index.latest)return `<p class="notice warning">法人資料尚未核對完成。${esc(index.error||'')}</p>`;
      const day=index.dates.includes(state.date)?state.date:index.latest;
      const pos=index.dates.indexOf(day),prev=pos>0?await load('stock-institutional/'+index.dates[pos-1]):null;
      const data=await load('stock-institutional/'+day),before=new Map((prev?.rows||[]).map(r=>[r.market+':'+r.code,r]));
      const trends=await load('stock-institutional-trends/'+day).catch(()=>null);
      const query=state.query.trim();
      shown=data.rows.filter(r=>r.market===state.market&&(!query||(r.code+' '+r.name).includes(query))).map(r=>({...r,...trends?.rows?.[r.market+':'+r.code]})).sort((a,b)=>Math.abs(b.total)-Math.abs(a.total));
      const paired=shown.filter(r=>finite(r.stockPct)&&finite(r.volumeEligible)&&r.volumeEligible>0);
      const selected=shown.slice(state.page*30,(state.page+1)*30);
      const rows=selected.map(r=>tr([`<a href="#warrants?underlying=${encodeURIComponent(r.code)}">${esc(r.code)} ${esc(r.name)}</a>`,pct(r.stockPct),
        signed(r.foreign),signed(before.has(r.market+':'+r.code)&&prev.date===r.basisDates?.[1]?r.foreign-before.get(r.market+':'+r.code).foreign:null),`${signed(r.foreign5)}<small>${flowStreak(r.foreignStreak5)}</small>`,signed(r.trust),`${signed(r.trust5)}<small>${flowStreak(r.trustStreak5)}</small>`,
        `${signed(r.dealer)}<small>自行 ${signed(r.dealerOwn)}／避險 ${signed(r.dealerHedge)}</small>`,signed(r.total),
        finite(r.volumeEligible)?`${fmt(r.callVolumeSignals)}／${fmt(r.putVolumeSignals)}<small>可比較 ${fmt(r.volumeEligible)} 檔</small>`:'權證量價缺資料']));
      return `<section class="panel"><h2>現股法人與權證同日觀察</h2><p>資料日 <strong>${esc(day)}</strong> · 已累積 ${index.dates.length} 個資料日 · 法人單位：股</p>
       <p><a class="text-link" href="#stock-institutional?view=research">法人連買賣與權證放量歷史回測 →</a> · <a href="#series-report">分析報告 →</a></p>
       ${index.status!=='ok'?`<p class="notice warning">最近更新未成功，保留既有資料。${esc(index.error||'')}</p>`:''}
       <p class="notice">法人類別與權證放量並列，不能認定是同一投資人。自營商避險不等於看空或看多；完整券商分點仍未取得。</p>
       <div class="filters">${select('si-day','資料日',index.dates.slice().reverse(),day)}${select('si-market','市場',['TWSE','TPEX'],state.market,v=>v==='TWSE'?'上市':'上櫃')}
       <label>標的代號／名稱<input id="si-query" value="${esc(state.query)}"></label><button id="si-search">查詢</button></div>
       <div class="metrics">${metric('收錄標的',fmt(shown.length),'本市場篩選結果，按法人淨額絕對值排序')}${metric('同日股證可比較',fmt(paired.length),'現股漲跌與權證量價均可用')}${metric('全體名錄涵蓋',`${data.coverage.matchedStocks}／${data.coverage.directoryStocks}`,'缺列不當作零買賣超')}</div>
       ${table(['標的','現股漲跌%','外資淨額','外資淨額較前日變化','外資五日累計／連續','投信淨額','投信五日累計／連續','自營商淨額／分項','法人合計','放量認購／認售'],rows)}
       <div class="pagination"><button id="si-prev" ${state.page?'':'disabled'}>上一頁</button><span>${state.page+1}／${Math.max(1,Math.ceil(shown.length/30))}</span><button id="si-next" ${(state.page+1)*30>=shown.length?'disabled':''}>下一頁</button><button id="si-csv">下載 CSV</button></div>
       <details><summary>來源、涵蓋與缺漏</summary><p>未收錄標的 ${data.coverage.missingStocks.length} 檔：${esc(data.coverage.missingStocks.join('、')||'無')}；前期 ${esc(prev?.date||'尚無')}。前日比較須對齊交易日；五日累計缺一日即留空，連續買賣最多回看五日。</p><p>首次保存 ${esc(data.firstRecordedAt||'舊版未保存')}；${esc(data.acquisitionMode||'舊版')}，名錄日期 ${esc(data.membership?.date||'舊版未保存')}。<a href="data/derivatives/stock-institutional-backfill.json">補抓逐日涵蓋</a></p><ul>${data.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>${data.sources.map(s=>`<p><a href="${esc(s.url)}">${esc(s.market)} 官方日報</a> · ${s.rows} 檔 · SHA-256 <code>${esc(s.sha256)}</code></p>`).join('')}</details></section>`;
    },
    bind(render){
      for(const [id,key] of [['si-day','date'],['si-market','market'],['si-horizon','horizon'],['si-phase','phase']])document.getElementById(id)?.addEventListener('change',e=>{state[key]=e.target.value;state.page=0;render();});
      document.getElementById('si-search')?.addEventListener('click',()=>{state.query=document.getElementById('si-query').value;state.page=0;render();});
      for(const [id,step] of [['si-prev',-1],['si-next',1]])document.getElementById(id)?.addEventListener('click',()=>{state.page+=step;render();});
      document.getElementById('si-csv')?.addEventListener('click',()=>{const fields=[['date','日期'],['code','標的'],['market','市場'],['foreign','外資淨股數'],['foreign5','外資五日累計股數'],['foreignStreak5','外資五日窗口連續買賣日數'],['trust','投信淨股數'],['trust5','投信五日累計股數'],['trustStreak5','投信五日窗口連續買賣日數'],['dealer','自營商淨股數'],['dealerOwn','自行買賣淨股數'],['dealerHedge','避險淨股數'],['total','法人合計股數'],['stockPct','現股漲跌%'],['callVolumeSignals','認購放量檔數'],['putVolumeSignals','認售放量檔數']];const url=URL.createObjectURL(new Blob([toCSV(shown,fields)],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='stock-institutional.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    }
  };
}
