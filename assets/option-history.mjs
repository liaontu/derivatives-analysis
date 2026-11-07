import {esc, finite, fmt, cpLabel, chartFrames, toCSV} from './model.mjs';

const signed = (v, suffix='') => finite(v) ? (v>0?'+':'')+fmt(v,2)+suffix : '—';
const difference = (a,b) => finite(a)&&finite(b)?a-b:null;
const pct = (a,b) => finite(a)&&finite(b)&&b>0?100*(a/b-1):null;
const identity = r => r[0]+'/'+r[1];
const names = {TXO:'臺指選擇權',TEO:'電子選擇權',TFO:'金融選擇權',CDO:'台積電選擇權'};
const ruleNames = {volume:'放量',momentum:'價量增倉',pullback:'價跌增倉'};
const statusNames = {evaluated:'可評估',pending:'待後續資料','missing-entry':'次日缺成交價','missing-exit':'退出日缺價／契約已結束'};
const segmentNames = {development:'前 70% 時間區段',evaluation:'後 30% 時間區段',purged:'跨分界，排除比較'};

export function dailyChanges(row, old, priorFive) {
  const mean = priorFive.length===5 && priorFive.every(r=>r && finite(r[3])) ? priorFive.reduce((s,r)=>s+r[3],0)/5 : null;
  return {priceChange:difference(row[2],old?.[2]),pricePct:pct(row[2],old?.[2]),
    volumeChange:difference(row[3],old?.[3]),volumePct:pct(row[3],old?.[3]),
    oiChange:difference(row[4],old?.[4]),volumeMultiple:mean>0&&finite(row[3])?row[3]/mean:null};
}

export function observations(group, sessions, selected) {
  return sessions.filter(d=>d>=Object.keys(group.days).sort()[0] && d<=Object.keys(group.days).sort().at(-1))
    .map(date=>({date,row:(group.days[date]||[]).find(r=>identity(r)===selected)}));
}

function chart(points,label) {
  const valid=points.filter(p=>finite(p.value));
  if(!valid.length) return '<p class="muted">這個契約沒有可畫圖的資料。</p>';
  const low=Math.min(...valid.map(p=>p.value)),high=Math.max(...valid.map(p=>p.value));
  const span=high-low||Math.max(1,Math.abs(high)*.02);
  return chartFrames(({cls,width})=>{
    const left=75,right=width-15,top=20,bottom=175,height=220;
    const x=i=>left+i/Math.max(1,points.length-1)*(right-left),y=v=>bottom-(v-low)/span*(bottom-top);
    let path='',connected=false;
    points.forEach((p,i)=>{if(!finite(p.value)){connected=false;return;}path+=(connected?'L':'M')+x(i).toFixed(2)+','+y(p.value).toFixed(2);connected=true;});
    return `<svg class="${cls}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label)}"><title>${esc(label)}；缺值斷線</title>
      ${[low,low+span/2,low+span].map(v=>`<line x1="${left}" x2="${right}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="${left-7}" y="${y(v)+4}" text-anchor="end">${fmt(v,2)}</text>`).join('')}
      <path d="${path}" class="series" stroke-width="2" fill="none"/>
      ${points.map((p,i)=>finite(p.value)?`<circle cx="${x(i)}" cy="${y(p.value)}" r="2" class="dot"><title>${esc(p.date)}：${fmt(p.value,2)}</title></circle>`:'').join('')}
      <text x="${left}" y="205">${esc(points[0].date)}</text><text x="${right}" y="205" text-anchor="end">${esc(points.at(-1).date)}</text></svg>`;
  });
}

export function createOptionHistory({load,table,tr,select,metric}) {
  const state={contract:'TXO',month:'',day:'',cp:'',sort:'volume',page:0,series:'',rule:'volume',segment:'evaluation',eventPage:0};
  let csvRows=[];
  async function page() {
    const index=await load('option-history');
    const contracts=Object.keys(index.contracts).sort();
    if(!Object.hasOwn(index.contracts,state.contract)) state.contract=contracts[0];
    const choices=index.contracts[state.contract].slice().sort((a,b)=>b.dates.at(-1).localeCompare(a.dates.at(-1))||a.month.localeCompare(b.month));
    if(!choices.some(e=>e.month===state.month)) state.month=choices[0].month;
    const meta=choices.find(e=>e.month===state.month);
    if(!meta.dates.includes(state.day)) state.day=meta.dates.at(-1);
    const [group,backtest]=await Promise.all([load('option-history/'+state.contract+'/'+state.month),load('option-backtest/'+state.contract)]);
    const i=index.sessions.indexOf(state.day), prior=index.sessions[i-1];
    const dayMaps=new Map(Object.entries(group.days).map(([d,rs])=>[d,new Map(rs.map(r=>[identity(r),r]))]));
    const rows=(group.days[state.day]||[]).filter(r=>!state.cp||r[1]===state.cp).map(r=>({row:r,...dailyChanges(r,dayMaps.get(prior)?.get(identity(r)),index.sessions.slice(Math.max(0,i-5),i).map(d=>dayMaps.get(d)?.get(identity(r))))}));
    const value=r=>state.sort==='volume'?r.row[3]:state.sort==='oi'?r.row[4]:state.sort==='strike'?-r.row[0]:r[state.sort];
    rows.sort((a,b)=>(value(b)??-Infinity)-(value(a)??-Infinity)||a.row[0]-b.row[0]||a.row[1].localeCompare(b.row[1]));
    if(!rows.some(r=>identity(r.row)===state.series)) state.series=rows.length?identity(rows[0].row):'';
    state.page=Math.min(state.page,Math.max(0,Math.ceil(rows.length/40)-1));
    const series=observations(group,index.sessions,state.series);
    csvRows=series.map(({date,row})=>{
      const pos=index.sessions.indexOf(date),previous=dayMaps.get(index.sessions[pos-1])?.get(state.series);
      const change=row?dailyChanges(row,previous,index.sessions.slice(Math.max(0,pos-5),pos).map(d=>dayMaps.get(d)?.get(state.series))):{};
      return {date,contract:state.contract,month:state.month,strike:state.series.split('/')[0],cp:state.series.split('/')[1],last:row?.[2]??null,volume:row?.[3]??null,oi:row?.[4]??null,...change};
    });
    const events=backtest.events.filter(e=>e.rule===state.rule&&e.segment===state.segment&&(!state.cp||e.cp===state.cp));
    state.eventPage=Math.min(state.eventPage,Math.max(0,Math.ceil(events.length/30)-1));
    const buckets=backtest.summary.filter(e=>e.segment===state.segment&&(!state.cp||e.cp===state.cp));
    const pager=(p,len,size,key)=>`<div class="pagination"><span>${fmt(len)} 筆 · 第 ${p+1}／${Math.max(1,Math.ceil(len/size))} 頁</span><div><button data-oh-pager="${key}" value="-1" ${p===0?'disabled':''}>上一頁</button><button data-oh-pager="${key}" value="1" ${(p+1)*size>=len?'disabled':''}>下一頁</button></div></div>`;
    return `<section class="panel"><div class="metrics">
      ${metric('歷史資料日數',fmt(index.dates.length),index.start+' 至 '+index.latest)}
      ${metric('商品代碼',fmt(contracts.length),'包含期間內已到期契約；一般時段')}
      ${metric('價量與未平倉筆數',fmt(index.rows),'各日／商品／到期別／履約價／買賣權')}
      ${metric('缺少交易日',fmt(index.missingDates.length),'每日免費排程補新資料並重算回測')}</div>
      ${index.status!=='ok'?'<p class="notice warning">歷史仍有缺漏或最近下載失敗，保留成功資料。'+esc(index.missingDates.join('、'))+' '+esc(index.failures.map(f=>f.start+'～'+f.end+' '+f.reason).join('；'))+'</p>':''}
      <p>本頁補抓台灣期交所選擇權日行情，不包含美股完整選擇權鏈。無成交日的成交價留白；結算價分開列示。歷史資料會隨官方修訂重算，未宣稱為當時可見版本。</p>
      <p><a class="text-link" href="#position-report">法人與大額交易人籌碼日報 →</a> · <a class="text-link" href="#stock-options">個股選擇權與現股價差 →</a></p></section>
      <section class="panel"><h2>同一契約的每日變化</h2><div class="filters">
      ${select('oh-contract','商品',contracts,state.contract,c=>c+' · '+(index.names?.[c]||names[c]||'選擇權'))}
      ${select('oh-month','到期月份／週別',choices.map(e=>e.month),state.month,m=>m+'（末筆 '+choices.find(e=>e.month===m).dates.at(-1)+'）')}
      ${select('oh-day','資料日',meta.dates.slice().reverse(),state.day)}
      ${select('oh-cp','買賣權',['','C','P'],state.cp,c=>c?cpLabel(c):'全部')}
      ${select('oh-sort','排序',['volume','oi','strike','pricePct','volumeMultiple','oiChange'],state.sort,s=>({volume:'成交量高至低',oi:'未平倉高至低',strike:'履約價低至高',pricePct:'漲幅高至低',volumeMultiple:'量增倍數高至低',oiChange:'增倉高至低'}[s]))}</div>
      <p>比較基準：${esc(prior||'尚無前一交易日')}。只比較相同契約，沒有前日有效價格即不算漲跌；前五交易日資料不齊或均量為零即不算量增倍數。</p>
      ${table(['履約價／看趨勢','買賣權','成交價','價格變動／漲跌率','成交量（口）','量變／變動率','前五日均量倍數','未平倉（口）','未平倉增減','結算價'],rows.slice(state.page*40,(state.page+1)*40).map(x=>{const r=x.row;return tr([
        `<button class="text-link" data-oh-series="${esc(identity(r))}">${fmt(r[0],2)}</button>`,cpLabel(r[1]),fmt(r[2],2),signed(x.priceChange)+'<small>'+signed(x.pricePct,'%')+'</small>',fmt(r[3]),signed(x.volumeChange)+'<small>'+signed(x.volumePct,'%')+'</small>',finite(x.volumeMultiple)?fmt(x.volumeMultiple,2)+' 倍':'—',fmt(r[4]),signed(x.oiChange),fmt(r[5],2)]);} ))}
      ${pager(state.page,rows.length,40,'page')}</section>
      <section class="panel"><div class="panel-head"><h2>${esc(state.contract+' '+state.month+' '+state.series)} · 時間序列</h2><button id="oh-csv" ${state.series?'':'disabled'}>匯出此契約 CSV</button></div>
      <p>點上表履約價切換。日期沿用交易日軸；缺資料處斷線，未跨到期月份接續。${state.series?'':'目前沒有符合條件的契約。'}</p>
      <h3>有效成交價</h3>${chart(series.map(p=>({date:p.date,value:p.row?.[2]})),'選擇權成交價')}
      <div class="split"><div><h3>成交量（口）</h3>${chart(series.map(p=>({date:p.date,value:p.row?.[3]})),'成交量')}</div><div><h3>未平倉（口）</h3>${chart(series.map(p=>({date:p.date,value:p.row?.[4]})),'未平倉')}</div></div></section>
      <section class="panel"><span class="eyebrow">HISTORICAL EVENT STUDY</span><h2>固定規則回測 · ${esc(state.contract)}</h2>
      <p>回測包含此商品所有已收錄到期別，不受上方單一期別與資料日限制；買權、賣權分開統計。訊號日收盤後判定，次一交易日收盤作觀察基準，再觀察五個交易日。這是買進權利金方向的價格事件研究，沒有實際下單。</p>
      <div class="filters">${select('oh-segment','時間區段',Object.keys(segmentNames),state.segment,s=>segmentNames[s])}${select('oh-rule','事件明細',Object.keys(ruleNames),state.rule,r=>ruleNames[r])}</div>
      <p>時間分界：${esc(index.splitDate)}。規則固定，未按回測結果調參；跨分界事件另列。這是事後歷史切分，非即時樣本外績效（Live OOS＝0）。同契約同規則六個交易日內不重複觸發，不同契約的相關與重疊事件仍存在。</p>
      <p class="notice warning">平均與正報酬比例只計入可評估事件。週契約可能在五日觀察期內到期，缺價與到期排除會造成選擇偏差；請連同觸發總數、中位數和有效訊號日期數閱讀。</p>
      ${table(['規則／權別','觸發／可評估','待資料／進場缺價／退出缺價','平均／中位價格報酬','正報酬比例','成本 1%／3% 情境','有效訊號日期數'],buckets.map(b=>tr([
        ruleNames[b.rule]+'<small>'+cpLabel(b.cp)+'</small>',fmt(b.signals)+'／'+fmt(b.evaluated),fmt(b.counts.pending||0)+'／'+fmt(b.counts['missing-entry']||0)+'／'+fmt(b.counts['missing-exit']||0),signed(b.mean,'%')+'<small>中位 '+signed(b.median,'%')+'</small>',finite(b.positivePct)?fmt(b.positivePct,2)+'%':'—',signed(b.meanCost1,'%')+'／'+signed(b.meanCost3,'%'),fmt(b.distinctSignalDates)+(b.evaluated<30?'<small>有效樣本不足 30 筆</small>':'')]))) }
      <details><summary>規則、成本與回測限制</summary><ul>${Object.values(index.rules).map(r=>'<li>'+esc(r)+'</li>').join('')}</ul>
      <p>報酬＝退出日成交價 ÷ 基準日成交價 − 1。成本情境從報酬扣除初始權利金的 1% 或 3%，不代表實際稅費、價差或滑價；不同商品每口乘數不混算成金額。收盤資料不能保證成交，缺同步報價、盤中路徑與成交容量。</p>
      <p>缺次日／退出日成交價或契約已結束，不延後找價、不補零、不用結算價代替成交，也未模擬到期結算。可評估結果因此有流動性與存續選擇偏差，須連同排除數閱讀。非投資組合，不提供資金曲線、年化報酬或已實現獲利；未加入歷史法人、分點、IV 或 Greeks 條件。</p></details></section>
      <section class="panel"><div class="panel-head"><h2>逐筆事件與排除原因</h2><a class="text-link" href="data/derivatives/option-backtest/${esc(state.contract)}.json" download>下載此商品完整回測</a></div>
      ${table(['訊號日／到期別','履約價／權別','漲跌／放量／增倉','基準日／價格','退出日／價格','狀態','價格報酬'],events.slice(state.eventPage*30,(state.eventPage+1)*30).map(e=>tr([
        esc(e.date)+'<small>'+esc(e.month)+'</small>',fmt(e.strike,2)+'<small>'+cpLabel(e.cp)+'</small>',signed(e.pricePct,'%')+'<small>'+fmt(e.volumeMultiple,2)+' 倍／'+signed(e.oiChange)+' 口</small>',esc(e.entryDate||'待資料')+'<small>'+fmt(e.entry,2)+'</small>',esc(e.exitDate||'待資料')+'<small>'+fmt(e.exit,2)+'</small>',statusNames[e.status],signed(e.returnPct,'%')]))) }${pager(state.eventPage,events.length,30,'eventPage')}</section>
      <details class="panel"><summary>資料來源、下載核對與涵蓋</summary><p><a href="${esc(index.source)}" target="_blank" rel="noopener noreferrer">期交所免費每日行情歷史下載</a> · <a href="data/derivatives/option-history.json">完整資料涵蓋與 SHA-256 紀錄</a> · <a href="https://github.com/liaontu/derivatives-analysis/actions/workflows/derivatives-daily.yml">每日更新執行紀錄</a></p>
      <p>每日沿用公開 GitHub Actions 與 GitHub Pages，無付費 API 或模型呼叫。逐期保留歷史，排程以每批最多 14 個日曆日、每日最多兩批補漏；失敗保留已成功資料。一般時段與盤後時段不重複加總；目前僅發布一般時段。</p>
      ${table(['下載區間','交易日數／筆數','擷取時間','原始內容校驗碼'],index.receipts.slice().reverse().map(r=>tr([esc(r.start+'～'+r.end),fmt(r.dates.length)+'／'+fmt(r.rows),esc(r.fetchedAt),'<small>'+esc(r.sha256.slice(0,20))+'…</small>'])))}</details>`;
  }
  function bind(rerender) {
    for(const key of ['contract','month','day','cp','sort','rule','segment']) document.getElementById('oh-'+key)?.addEventListener('change',e=>{
      state[key]=e.target.value;state.page=state.eventPage=0;
      if(key==='contract'){state.month='';state.series='';} if(key==='month')state.series='';rerender();
    });
    document.querySelectorAll('[data-oh-series]').forEach(b=>b.addEventListener('click',()=>{state.series=b.dataset.ohSeries;rerender();}));
    document.querySelectorAll('[data-oh-pager]').forEach(b=>b.addEventListener('click',()=>{state[b.dataset.ohPager]+=Number(b.value);rerender();}));
    document.getElementById('oh-csv')?.addEventListener('click',()=>{
      const fields=[['date','日期'],['contract','商品'],['month','到期別'],['strike','履約價'],['cp','買賣權'],['last','成交價'],['volume','成交量'],['oi','未平倉'],['priceChange','價格變動'],['pricePct','漲跌率%'],['volumeChange','成交量變動'],['volumePct','成交量變動率%'],['oiChange','未平倉變動'],['volumeMultiple','前五日均量倍數']];
      const blob=new Blob([toCSV(csvRows,fields)],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');
      a.href=url;a.download=state.contract+'-'+state.month+'-'+state.series.replace('/','-')+'.csv';a.click();URL.revokeObjectURL(url);
    });
  }
  return {page,bind};
}
