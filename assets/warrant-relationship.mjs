import {esc,finite,fmt,toCSV,chartFrames,chartGutter} from './model.mjs';

const pct=v=>finite(v)?(v>0?'+':'')+fmt(v,2)+'%':'—';
const share=(n,d)=>d>0?fmt(100*n/d,1)+'%':'—';
const side=cp=>cp==='C'?'認購':'認售';
const compactLots=n=>n>=10000?fmt(n/10000,1)+'萬':fmt(n);
const ratioText=v=>finite(v)?String(v):'—';
export const relationText={issuer:'發行商總公司（疑似造市）',sameBroker:'同券商分點',other:'其他分點',unknown:'發行商未判定'};
export const marketNet=(day,cp)=>{const m=day.marketMaking?.[cp];return m&&m.rows>0?m.marketNet:null;};
const signedLots=v=>finite(v)?(v>0?'+':'')+fmt(v):'—';
const lotsOrNone=v=>finite(v)?signedLots(v)+' 張':'無排行';
export const hintText={
  'call-up':['上漲日認購連續買超','與順勢增加認購曝險相容；也可能來自不同客戶或既有組合調整。'],
  'call-down':['下跌日認購連續買超','可能逆勢布局或調整部位，不能確認抄底意圖。'],
  'put-down':['下跌日認售連續買超','可能看跌，也可能保護原有現股；缺現股持倉，無法區分。'],
  'put-up':['上漲日認售連續買超','可能增加下跌保護或預期反轉；不能直接當成看空證據。'],
  roll:['近到期賣超與長天期買超並存','與延長期限的換倉相容；仍需相同客戶及行使比例調整後的部位才能確認。'],
  both:['同分點認購、認售同日買超','可能是雙向波動曝險，也可能是不同客戶；不同條款不能直接稱為跨式策略。']
};
export function volumeValue(row,cp) {
  const s=row.sides[cp];return s.count>0&&s.missingVolume===s.count?null:s.volume;
}
export function monthFacts(stock) {
  const prices=stock.days.filter(d=>finite(d.spot)&&d.spot>0);
  const calls=stock.days.reduce((n,d)=>n+d.sides.C.volume,0),puts=stock.days.reduce((n,d)=>n+d.sides.P.volume,0);
  const hints=stock.days.flatMap(d=>d.hints.map(h=>({...h,date:d.date,spotPct:d.spotPct})));
  const making={};for(const cp of ['C','P']){const days=stock.days.filter(d=>finite(marketNet(d,cp)));making[cp]={net:days.reduce((n,d)=>n+marketNet(d,cp),0),days:days.length};}
  const rankedVolume=stock.days.reduce((n,d)=>n+(d.branchVolume||0),0);
  return {first:prices[0],last:prices.at(-1),calls,puts,hints,making,rankedVolume,branchDays:stock.days.filter(d=>d.branchWarrants>0).length};
}

function chart(days,kind) {
  const volume=kind==='volume';
  const values=days.map(d=>volume?(d.sides.C.missingVolume||d.sides.P.missingVolume?null:d.sides.C.volume+d.sides.P.volume):d.spot);
  const valid=values.filter(finite);
  if(!valid.length)return '<p class="empty">本期缺少可繪圖資料。</p>';
  const low=volume?0:Math.min(...valid),high=Math.max(...valid),range=high-low||Math.max(1,high*.02);
  const label=volume?'認購與認售每日成交量（張）':'標的每日收盤價（元，未還原）';
  return chartFrames(({cls,width,font})=>{
    const height=230,ticks=[low,low+range/2,low+range];
    const left=chartGutter(ticks.map(v=>fmt(v,volume?0:2)),font,45),right=width-12,top=22,bottom=182;
    const step=(right-left)/Math.max(1,days.length),x=i=>left+step*(i+.5),y=v=>bottom-(v-low)/range*(bottom-top);
    let drawing='',path='',previous=false;
    for(let i=0;i<days.length;i++){
      if(!finite(values[i])){previous=false;continue;}
      if(volume){const c=days[i].sides.C.volume,p=days[i].sides.P.volume,w=Math.max(2,step*.7);drawing+=`<g><title>${esc(days[i].date)}：認購 ${fmt(c)} 張、認售 ${fmt(p)} 張</title><rect x="${x(i)-w/2}" y="${y(c)}" width="${w}" height="${bottom-y(c)}" fill="#118a85"/><rect x="${x(i)-w/2}" y="${y(c+p)}" width="${w}" height="${y(c)-y(c+p)}" fill="#c87927"/></g>`;}
      else{if(i>0&&days[i-1].date!==days[i].previousDate)previous=false;path+=(previous?'L':'M')+x(i)+','+y(values[i]);previous=true;drawing+=`<circle cx="${x(i)}" cy="${y(values[i])}" r="2.5" fill="#137d99"><title>${esc(days[i].date)}：${fmt(values[i],2)} 元</title></circle>`;}
    }
    if(!volume)drawing=`<path d="${path}" fill="none" stroke="#137d99" stroke-width="2"/>`+drawing;
    return `<svg class="${cls}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${label}"><title>${label}</title>${ticks.map(v=>`<line x1="${left}" x2="${right}" y1="${y(v)}" y2="${y(v)}" stroke="#d9e3e8"/><text x="${left-7}" y="${y(v)+4}" text-anchor="end">${fmt(v,volume?0:2)}</text>`).join('')}${drawing}${[0,Math.floor((days.length-1)/2),days.length-1].filter((v,i,a)=>a.indexOf(v)===i).map(i=>`<text x="${x(i)}" y="211" text-anchor="${i===0?'start':i===days.length-1?'end':'middle'}">${esc(days[i].date.slice(5))}</text>`).join('')}</svg>`;
  });
}

export function createWarrantRelationship({load,table,tr,select,metric}) {
  const state={branch:'',page:0,minNet:'100'};let stockData=null,exportData=[];
  async function page(){
    const index=await load('warrant-relationship/index');
    if(!index.items.length)return '<p class="notice warning">尚無核對通過的近月資料。</p>';
    const params=new URLSearchParams(location.hash.split('?')[1]),requested=params.get('underlying')||'2330';
    const member=index.items.find(s=>s.code===requested);
    if(!member)return '<p class="notice warning">找不到此個股的近月權證資料。<a href="#warrant-relationship">返回查詢</a></p>';
    const stock=await load('warrant-relationship/stocks/'+member.code);stockData=stock;
    const facts=monthFacts(stock),days=stock.days,desired=params.get('date'),current=desired?days.find(d=>d.date===desired):days.at(-1);
    const sources=await load('warrant-relationship/sources');
    const unavailable=index.dates.filter(d=>d.status==='missing'),open=index.dates.filter(d=>d.status==='available');
    const substantial=facts.hints.filter(h=>Math.abs(h.aNet)>=Number(state.minNet)&&(!h.b||Math.abs(h.bNet)>=Number(state.minNet)));
    const summaryHints=Object.entries(hintText).map(([key,v])=>[v[0],substantial.filter(h=>h.kind===key).length,v[1]]);
    const hints=substantial.filter(h=>!state.branch||[h.branchCode,h.branchName,h.a,h.b].some(v=>String(v||'').includes(state.branch))).reverse();
    state.page=Math.min(state.page,Math.max(0,Math.ceil(hints.length/10)-1));
    const stockReturn=facts.first&&facts.last?(facts.last.spot/facts.first.spot-1)*100:null;
    exportData=days.map(d=>({date:d.date,spot:d.spot,spotPct:d.spotPct,callVolume:volumeValue(d,'C'),putVolume:volumeValue(d,'P'),callMatchedPct:d.sides.C.matchedVolumePct,putMatchedPct:d.sides.P.matchedVolumePct,branchWarrants:d.branchWarrants,warrants:d.count,callTermsVolume:d.sides.C.knownTermVolume,putTermsVolume:d.sides.P.knownTermVolume,callMarketNet:marketNet(d,'C'),putMarketNet:marketNet(d,'P')}));
    return `<div class="daily-report warrant-relationship"><section class="panel"><div class="panel-head"><div><span class="eyebrow">ONE-MONTH RELATIONSHIPS</span><h2>${esc(stock.code)} ${esc(stock.name)} · 現股與權證</h2><p>${esc(index.start)} 至 ${esc(index.end)} · ${open.length} 個行情日，${index.dates.filter(d=>d.status==='closed').length} 個休市日；${unavailable.length} 日缺市場資料。</p></div><button id="relationship-print">列印／PDF</button></div>
      <form id="relationship-search" class="filters"><label>個股代碼／名稱<input name="stock" list="relationship-stocks" value="${esc(stock.code)}" placeholder="例如 2330、台積電"></label><datalist id="relationship-stocks">${index.items.map(s=>`<option value="${esc(s.code)}">${esc(s.name)}</option>`).join('')}</datalist><button>查看個股</button><span id="relationship-error" role="status"></span><button id="relationship-export" type="button">匯出每日表 CSV</button></form>
      ${index.status==='retained'?`<p class="notice warning">最近更新未完成：${esc(index.error)}。保留上次核對資料。</p>`:''}
      <div class="metrics">${metric('區間股價變化',pct(stockReturn),`${facts.first?.date||'缺資料'} → ${facts.last?.date||'缺資料'}；非總報酬`)}${metric('認購／認售已知成交',compactLots(facts.calls)+'／'+compactLots(facts.puts),'張；不是淨買進或同等曝險')}${metric('分點有觀測值',facts.branchDays+'／'+days.length+' 日','只涵蓋已保存的部分權證與入榜分點')}${metric('同日條款涵蓋',finite(member.termVolumePct)?fmt(member.termVolumePct,1)+'%':'—','以成交張數計；各欄缺漏另列')}</div>
      <p class="notice">可直接確認的是價格、成交量及入榜分點買賣；現有資料不能識別單一大戶，也沒有其現股持倉。下列策略均是候選解釋。</p><p class="note">本標的有 ${days.length}／${open.length} 個行情日紀錄。${open.filter(d=>!days.some(s=>s.date===d.date)).map(d=>d.date).join('、')||'區間內每日皆有紀錄'}；未有紀錄可能是當日無收錄權證或資料未涵蓋，不能填為零交易。</p>
      <p>本期權證成交張數中，認購占 ${share(facts.calls,facts.calls+facts.puts)}、認售占 ${share(facts.puts,facts.calls+facts.puts)}。這反映交易活躍度，不代表買方或賣方的方向。</p><p>發行商總公司分點依名稱比對推定為造市部位（未取得造市專戶身分）：其淨賣超反號可粗估「有排行權證」的市場淨買進。本期認購 ${signedLots(facts.making.C.net)} 張（${facts.making.C.days} 日）、認售 ${signedLots(facts.making.P.net)} 張（${facts.making.P.days} 日）；正值表示市場淨買進、負值表示市場淨賣回。本標的有排行的權證只占本期成交量 ${share(facts.rankedVolume,facts.calls+facts.puts)}，未收錄的部分可能反向，所以這不是全市場淨額，也不是部位。</p>
      <p><a href="#warrant-signal-backtest">連續買超／換倉／雙邊線索回測 →</a> · <a href="#warrant-report">每日重點與訊號後續追蹤 →</a> · <a href="data/derivatives/warrant-relationship/stocks/${esc(stock.code)}.json" download>下載個股完整分析 JSON</a></p></section>
      <div class="split"><section class="panel"><h2>標的每日收盤價</h2>${chart(days,'price')}<p class="note">未做除權息還原；缺值處斷線。</p></section><section class="panel"><h2>權證每日成交張數</h2><p><span class="relationship-call">■ 認購</span>　<span class="relationship-put">■ 認售</span></p>${chart(days,'volume')}<p class="note">每日掛牌權證組成可能改變；缺成交量的日期留空。</p></section></div>
      <section class="panel"><h2>逐日價量與資料涵蓋</h2><p>相同權證量變只比較兩日皆有資料、代碼與名稱／市場／權別吻合的權證；新上市與退出樣本不混入。</p>
      ${table(['日期','現股收盤／日變化','認購量／相同樣本量變','認售量／相同樣本量變','同日條款量占比 C／P','有排行權證市場淨買進粗估 C／P','分點取得／權證總檔數'],days.map(d=>tr([
        `<a href="#warrant-relationship?underlying=${esc(stock.code)}&date=${esc(d.date)}">${esc(d.date)}</a>`,`${fmt(d.spot,2)}<small>${pct(d.spotPct)}</small>`,
        `${fmt(volumeValue(d,'C'))}<small>${pct(d.sides.C.matchedVolumePct)} · 比較 ${d.sides.C.matchedCount} 檔${d.sides.C.missingVolume?'；缺量 '+d.sides.C.missingVolume+' 檔':''}</small>`,
        `${fmt(volumeValue(d,'P'))}<small>${pct(d.sides.P.matchedVolumePct)} · 比較 ${d.sides.P.matchedCount} 檔${d.sides.P.missingVolume?'；缺量 '+d.sides.P.missingVolume+' 檔':''}</small>`,
        `${share(d.sides.C.knownTermVolume,d.sides.C.volume)}／${share(d.sides.P.knownTermVolume,d.sides.P.volume)}`,`${signedLots(marketNet(d,'C'))}／${signedLots(marketNet(d,'P'))}`,`${d.branchWarrants}／${d.count}${d.branchWarrants===0?'<small>未取得排行，非零交易</small>':''}`])))}
      <p class="note">「條款量占比」只表示取得同日條款，履約價、到期日與履約方式的可用量另列於下方。「有排行權證市場淨買進粗估」＝發行商總公司分點淨賣超反號，只含已取得排行的權證；未收錄的排行可能反向，不是全市場淨額；沒有排行的日期留空。單位為張。</p></section>
      <section class="panel"><h2>履約條件與當日分點證據</h2>${select('relationship-date','查閱日期',days.map(d=>d.date).reverse(),current?.date||desired||'')}
      ${current?detail(current,stock,table,tr):'<p class="notice warning">指定日期沒有此標的資料，不用其他日期替代。</p>'}</section>
      <section class="panel"><h2>資料支持哪些解讀？</h2><div class="filters">${select('relationship-min','淨買賣張數門檻',['0','100','500'],state.minNet,v=>v==='0'?'全部紀錄（含小量）':v+' 張以上')}</div><p class="note">雙邊／換倉線索要求兩筆各達門檻。這是觀察篩選條件，不是大戶認定；張數未換算成相同曝險。發行商總公司分點（疑似造市，依名稱比對推定）不列入線索；同券商其他分點另標示「同券商分點」。</p>${table(['已觀測現象','達門檻紀錄數','可能解釋與不能確認的部分'],summaryHints.map(r=>tr([esc(r[0]),fmt(r[1]),esc(r[2])])))}
      <p class="note">紀錄數為日期＋分點＋權證條件，不是大戶人數；0 筆只表示在有限排行內未觀測到。沒有買賣報價、完整現股持倉與客戶識別，無法確認套利、避險或同一人的換倉。</p>
      <form id="relationship-branch" class="filters"><label>篩選證據<input name="branch" value="${esc(state.branch)}" placeholder="分點名稱／代碼／權證"></label><button>篩選</button></form>
      ${table(['日期／現股變化','分點','已觀測線索','權證淨買賣超（張）'],hints.slice(state.page*10,(state.page+1)*10).map(h=>tr([`${esc(h.date)}<small>${pct(h.spotPct)}</small>`,`${esc(h.branchName)}<small>${esc(h.branchCode)}</small>${h.sameBroker?'<small>同券商分點</small>':''}`,esc(hintText[h.kind][0]),`${esc(h.a)}：${fmt(h.aNet)}${h.b?`<small>${esc(h.b)}：${fmt(h.bNet)}</small>`:`<small>連續 ${h.streakCapped?'至少 ':''}${h.streak} 日買超入榜</small>`}${h.kind==='roll'?`<small>剩餘 ${h.aDays} → ${h.bDays} 日；不代表同一客戶</small><small>賣超權證：履約 ${fmt(h.aStrike,2)}、比例 ${ratioText(h.aRatio)}、到期 ${esc(h.aExpiry||'缺資料')}</small><small>買超權證：履約 ${fmt(h.bStrike,2)}、比例 ${ratioText(h.bRatio)}、到期 ${esc(h.bExpiry||'缺資料')}</small>`:''}${h.kind==='both'?`<small>到期日與履約價${h.sameTerms?'相同':'不同或缺資料'}；部位比例未確認</small>`:''}`])))}
      <div class="pagination"><span>${hints.length} 筆 · 第 ${state.page+1}／${Math.max(1,Math.ceil(hints.length/10))} 頁</span><div><button data-relationship-page="-1" ${state.page===0?'disabled':''}>上一頁</button><button data-relationship-page="1" ${(state.page+1)*10>=hints.length?'disabled':''}>下一頁</button></div></div></section>
      <section class="panel"><h2>股價與權證量變的同期關聯</h2>${table(['權別','可比較日數','相關係數 r','解讀限制'],[['C',member.callCorrelation],['P',member.putCorrelation]].map(([cp,r])=>tr([side(cp),fmt(r.n),fmt(r.r,3),esc(r.status)])))}<p>以現股日漲跌對照相同權證樣本成交量的對數日變動，避免直接用價格水準計算。短期樣本不代表領先訊號，未檢驗顯著性或交易獲利。</p></section>
      <details class="panel"><summary>其他個股與資料來源</summary>${table(['個股','區間價格變化','權證成交張數','有分點資料日數','同日條款量占比'],index.items.slice(0,30).map(s=>tr([`<a href="#warrant-relationship?underlying=${esc(s.code)}">${esc(s.code)} ${esc(s.name)}</a>`,pct(s.spotPct),fmt(s.volume),`${s.branchDays}／${s.days}`,finite(s.termVolumePct)?fmt(s.termVolumePct,1)+'%':'—'])))}<p>上表按成交張數列前30個標的，可用上方查詢欄選取所有 ${index.items.length} 個股。不同權證張數不等於相同名目金額或 Delta 曝險。</p>
      ${index.limits.map(x=>'<p>'+esc(x)+'</p>').join('')}<p>歷史公司名錄可能採較晚日期回查，已退出名錄的標的涵蓋未驗證。歷史報表可能含後續官方修訂。</p>
      ${current?`<p>所選日期來源：${(sources.days[current.date]?.sources||[]).map(s=>`<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.id||'官方歷史行情')} ${esc(s.date)}</a>`).join(' · ')}</p>`:''}
      <p><a href="data/derivatives/warrant-relationship/sources.json">全部日期來源與名錄依據</a> · <a href="https://investoredu.twse.com.tw/pages/TWSE_InvestmentQA.aspx?ID=10&Page=2" target="_blank" rel="noopener noreferrer">證交所權證價格、避險與流動性說明</a></p></details></div>`;
  }
  function bind(rerender){
    document.getElementById('relationship-print')?.addEventListener('click',()=>window.print());
    document.getElementById('relationship-search')?.addEventListener('submit',async e=>{e.preventDefault();const q=e.target.elements.stock.value.trim();const index=await load('warrant-relationship/index');const match=index.items.filter(s=>s.code===q||s.name===q);if(match.length!==1){document.getElementById('relationship-error').textContent='請輸入完整個股代碼或名稱。';return;}state.page=0;state.branch='';location.hash='warrant-relationship?underlying='+match[0].code;});
    document.getElementById('relationship-date')?.addEventListener('change',e=>{location.hash=`warrant-relationship?underlying=${stockData.code}&date=${e.target.value}`;});
    document.getElementById('relationship-min')?.addEventListener('change',e=>{state.minNet=e.target.value;state.page=0;rerender();});
    document.getElementById('relationship-branch')?.addEventListener('submit',e=>{e.preventDefault();state.branch=e.target.elements.branch.value.trim();state.page=0;rerender();});
    document.querySelectorAll('[data-relationship-page]').forEach(el=>el.addEventListener('click',()=>{state.page+=Number(el.dataset.relationshipPage);rerender();}));
    document.getElementById('relationship-export')?.addEventListener('click',()=>{const fields=[['date','日期'],['spot','現股收盤未還原'],['spotPct','現股日漲跌百分比'],['callVolume','認購成交張數'],['putVolume','認售成交張數'],['callMatchedPct','相同認購樣本量變百分比'],['putMatchedPct','相同認售樣本量變百分比'],['callTermsVolume','有同日條款認購成交張數'],['putTermsVolume','有同日條款認售成交張數'],['branchWarrants','取得分點權證檔數'],['warrants','權證總檔數'],['callMarketNet','認購發行商總公司淨賣超反號（有排行權證市場淨買進粗估）'],['putMarketNet','認售發行商總公司淨賣超反號（有排行權證市場淨買進粗估）']];const url=URL.createObjectURL(new Blob([toCSV(exportData,fields)],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=`warrant-relationship-${stockData.code}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  }
  return {page,bind};
}

function detail(day,stock,table,tr){
  return `<h3>${esc(day.date)} · ${esc(stock.name)}</h3><p>以下分布以有對應欄位的成交張數為分母；缺資料不算成長天期或價外。</p>
    ${table(['權別','掛牌／有成交檔數','到期日可用量／總量','30日內／31–90日／91日以上','履約價可用量／總量','價內／價外成交比重','歐式／美式成交張數'],['C','P'].map(cp=>{const s=day.sides[cp];return tr([side(cp),`${fmt(s.count)}／${fmt(s.active)}`,`${fmt(s.knownExpiryVolume)}／${fmt(volumeValue(day,cp))}`,Object.values(s.expiry).map(n=>share(n,s.knownExpiryVolume)).join('／'),`${fmt(s.knownStrikeVolume)}／${fmt(volumeValue(day,cp))}`,share(s.moneyness.in,s.knownStrikeVolume)+'／'+share(s.moneyness.out,s.knownStrikeVolume),`${fmt(s.style['歐式'])}／${fmt(s.style['美式'])}<small>其他／未識別 ${fmt(s.style['其他／缺資料'])}</small>`]);}))}
    <h3>當日成交量前5檔權證</h3>${table(['權證／權別','成交量／收盤','履約價／行使比例','到期／最後交易／剩餘日數','履約方式／條款日','價內外程度'],day.topWarrants.map(r=>tr([`${esc(r.code)} ${esc(r.name)}<small>${side(r.cp)}</small>`,`${fmt(r.volume)} 張／${fmt(r.price,3)}`,`${fmt(r.strike,3)}／${ratioText(r.ratio)}`,`${esc(r.expiry||'缺歷史條款')}<small>最後交易：${esc(r.lastTrade||'缺資料')}</small><small>${r.remainingDays===null?'無法計算':r.remainingDays+' 日'}</small>`,`${esc(r.style||'缺歷史條款')}<small>${esc(r.termsDate||'未取得')}</small>`,pct(r.moneynessPct)])))}
    <p class="note">價內外程度＝（現股－履約價）÷履約價；認售反號，正值價內、負值價外。最後交易日與到期日不同；完整欄位保留於 JSON。此處不是權證報酬率。</p>
    <h3>已觀測分點買超／賣超各前三筆</h3>${table(['分點','身分','權證／權別','買進／賣出（張）','淨買賣超（張）'],day.leaders.map(r=>tr([`${esc(r.branchName)}<small>${esc(r.branchCode)}</small>`,esc(relationText[r.relation]||'—'),`${esc(r.code)} ${esc(r.name)}<small>${side(r.cp)}</small>`,`${fmt(r.buy)}／${fmt(r.sell)}`,fmt(r.net)])))}<p class="note">發行商總公司（疑似造市）：權證名稱中的發行商與分點所屬券商總公司相同，依名稱比對推定其賣超是在供應市場買盤、買超是市場賣回，未取得造市專戶身分。本日有排行權證的市場淨買進粗估：認購 ${lotsOrNone(marketNet(day,'C'))}、認售 ${lotsOrNone(marketNet(day,'P'))}${day.relationRows?`（排行筆數：造市 ${fmt(day.relationRows.issuer||0)}、同券商 ${fmt(day.relationRows.sameBroker||0)}、其他 ${fmt(day.relationRows.other||0)}）`:'（此日資料為舊版，未區分發行商）'}。</p><h3>排除發行商總公司後的分點買超／賣超各前三筆</h3>${day.customerLeaders?table(['分點','身分','權證／權別','買進／賣出（張）','淨買賣超（張）'],day.customerLeaders.map(r=>tr([`${esc(r.branchName)}<small>${esc(r.branchCode)}</small>`,esc(relationText[r.relation]||'—'),`${esc(r.code)} ${esc(r.name)}<small>${side(r.cp)}</small>`,`${fmt(r.buy)}／${fmt(r.sell)}`,fmt(r.net)]))):'<p class="note">此日資料為舊版，尚未區分發行商；下次重算後補上。</p>'}<p class="note">${day.branchWarrants?`取得 ${day.branchWarrants} 檔權證、${day.branchRows} 筆排行；這裡各列前三筆，並非全市場分點。`:'此日未取得可核對分點資料，不能據此判定沒有人買進。'}${day.branchIssues?'另有 '+day.branchIssues+' 檔分點與行情日期或成交量未吻合，已排除。':''}</p>`;
}
