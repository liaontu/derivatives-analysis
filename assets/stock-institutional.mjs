import {esc,fmt,finite,toCSV} from './model.mjs';
const signed=v=>finite(v)?`${v>0?'+':''}${fmt(v,2)}`:'—';
export function createStockInstitutional({load,table,tr,select,metric}) {
  const state={date:'',market:'TWSE',query:'',page:0};let shown=[];
  return {
    async page(){
      const index=await load('stock-institutional');
      if(!index.latest)return `<p class="notice warning">法人資料尚未核對完成。${esc(index.error||'')}</p>`;
      const day=index.dates.includes(state.date)?state.date:index.latest;
      const pos=index.dates.indexOf(day),prev=pos>0?await load('stock-institutional/'+index.dates[pos-1]):null;
      const data=await load('stock-institutional/'+day),before=new Map((prev?.rows||[]).map(r=>[r.code,r]));
      const query=state.query.trim();
      shown=data.rows.filter(r=>r.market===state.market&&(!query||(r.code+' '+r.name).includes(query))).sort((a,b)=>Math.abs(b.total)-Math.abs(a.total));
      const paired=shown.filter(r=>finite(r.stockPct)&&finite(r.volumeEligible));
      const selected=shown.slice(state.page*30,(state.page+1)*30);
      const rows=selected.map(r=>tr([`<a href="#warrants?underlying=${encodeURIComponent(r.code)}">${esc(r.code)} ${esc(r.name)}</a>`,signed(r.stockPct)+'%',
        signed(r.foreign),signed(before.has(r.code)?r.foreign-before.get(r.code).foreign:null),signed(r.trust),
        `${signed(r.dealer)}<small>自行 ${signed(r.dealerOwn)}／避險 ${signed(r.dealerHedge)}</small>`,signed(r.total),
        finite(r.volumeEligible)?`${fmt(r.callVolumeSignals)}／${fmt(r.putVolumeSignals)}<small>可比較 ${fmt(r.volumeEligible)} 檔</small>`:'權證量價缺資料']));
      return `<section class="panel"><h2>現股法人與權證同日觀察</h2><p>資料日 <strong>${esc(day)}</strong> · 已累積 ${index.dates.length} 個資料日 · 法人單位：股</p>
       ${index.status!=='ok'?`<p class="notice warning">最近更新未成功，保留既有資料。${esc(index.error||'')}</p>`:''}
       <p class="notice">法人類別與權證放量並列，不能認定是同一投資人。自營商避險不等於看空或看多；完整券商分點仍未取得。</p>
       <div class="filters">${select('si-day','資料日',index.dates.slice().reverse(),day)}${select('si-market','市場',['TWSE','TPEX'],state.market,v=>v==='TWSE'?'上市':'上櫃')}
       <label>標的代號／名稱<input id="si-query" value="${esc(state.query)}"></label><button id="si-search">查詢</button></div>
       <div class="metrics">${metric('收錄標的',fmt(shown.length),'本市場篩選結果，按法人淨額絕對值排序')}${metric('同日股證可比較',fmt(paired.length),'現股漲跌與權證量價均可用')}${metric('全體名錄涵蓋',`${data.coverage.matchedStocks}／${data.coverage.directoryStocks}`,'缺列不當作零買賣超')}</div>
       ${table(['標的','現股漲跌%','外資淨額','外資淨額較前日變化','投信淨額','自營商淨額／分項','法人合計','放量認購／認售'],rows)}
       <div class="pagination"><button id="si-prev" ${state.page?'':'disabled'}>上一頁</button><span>${state.page+1}／${Math.max(1,Math.ceil(shown.length/30))}</span><button id="si-next" ${(state.page+1)*30>=shown.length?'disabled':''}>下一頁</button><button id="si-csv">下載 CSV</button></div>
       <details><summary>來源、涵蓋與缺漏</summary><p>未收錄標的 ${data.coverage.missingStocks.length} 檔；前期 ${esc(prev?.date||'尚無')}。前期比較使用同一代碼，缺資料留空。</p><ul>${data.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>${data.sources.map(s=>`<p><a href="${esc(s.url)}">${esc(s.market)} 官方日報</a> · ${s.rows} 檔 · SHA-256 <code>${esc(s.sha256)}</code></p>`).join('')}</details></section>`;
    },
    bind(render){
      for(const [id,key] of [['si-day','date'],['si-market','market']])document.getElementById(id)?.addEventListener('change',e=>{state[key]=e.target.value;state.page=0;render();});
      document.getElementById('si-search')?.addEventListener('click',()=>{state.query=document.getElementById('si-query').value;state.page=0;render();});
      for(const [id,step] of [['si-prev',-1],['si-next',1]])document.getElementById(id)?.addEventListener('click',()=>{state.page+=step;render();});
      document.getElementById('si-csv')?.addEventListener('click',()=>{const fields=[['date','日期'],['code','標的'],['market','市場'],['foreign','外資淨股數'],['trust','投信淨股數'],['dealer','自營商淨股數'],['dealerOwn','自行買賣淨股數'],['dealerHedge','避險淨股數'],['total','法人合計股數'],['stockPct','現股漲跌%'],['callVolumeSignals','認購放量檔數'],['putVolumeSignals','認售放量檔數']];const url=URL.createObjectURL(new Blob([toCSV(shown,fields)],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='stock-institutional.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    }
  };
}
