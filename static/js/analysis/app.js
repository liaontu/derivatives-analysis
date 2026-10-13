import { finite, spreadPercent, putCallRatio, payoff, breakEven, normalizedHistory } from './math.js';

const $ = id => document.getElementById(id);
const publicSite = document.body.dataset.deployment === 'static';
const escape = value => String(value ?? '').replace(/[&<>"']/g, ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const fmt = (n,d=2) => finite(n) ? n.toLocaleString('zh-TW',{maximumFractionDigits:d}) : '—';
const sign = n => finite(n) ? `${n>0?'+':''}${fmt(n)}%` : '—';
const tone = n => n>0?'up':n<0?'down':'neutral';
const colors = ['#68dfcf','#ac9bff','#e8be79'];
const labels = {overview:'市場總覽',futures:'期貨分析',options:'選擇權分析',warrants:'權證篩選',calculator:'損益試算',sources:'資料來源'};
const descriptions = {overview:'期貨、選擇權與權證，在同一個視野。',futures:'對齊契約與計價幣別，觀察不同市場的價格。',options:'從履約價、到期日與流動性，拆解選擇權。',warrants:'從標的到條款，篩選台灣上市櫃權證。',calculator:'改變結算價，觀察每一筆交易的損益。',sources:'每個數字，都有來源與時間。'};
const state = {market:'ALL',view:'overview',range:'3m',tw:{},overview:null,us:null,rows:[],filtered:[],page:0,selected:null,route:0};
const rowMap = new Map();
function remember(rows){for(const r of rows)rowMap.set(`${r.market}:${r.key}`,r);return rows;}
const rowId = r=>escape(`${r.market}:${r.key}`);
function localTime(iso){return iso ? new Date(iso).toLocaleString('zh-TW',{timeZone:'Asia/Taipei',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})+' 台北' : '—';}
function quoteTime(r){return r.quoteTime ? localTime(r.quoteTime) : r.quoteTimeRaw ? `${r.quoteTimeRaw}（來源時間）` : r.priceDate || '無行情';}
async function json(path){
  const compressed=publicSite&&typeof DecompressionStream==='function';
  const r=await fetch(compressed?path+'.gz':path,{cache:'no-cache'});
  if(!r.ok)throw new Error(`行情檔讀取失敗（${r.status}）`);
  return compressed?new Response(r.body.pipeThrough(new DecompressionStream('gzip'))).json():r.json();
}
function notice(message,error=false){$('notice').textContent=message;$('notice').classList.toggle('error',error);}
function statusNotice(){
  if(publicSite){
    const dates=state.overview?.quoteDates;
    notice(`${dates?`期貨 ${dates.futures||'未取得'} · 選擇權 ${dates.options||'未取得'} · 權證 ${dates.warrants||'未取得'}`:`台灣資料日 ${state.overview?.asOf||'未載入'}`} · 非即時行情 · 美國請使用官方行情入口`,!state.overview);
    return;
  }
  const failed=state.us?.sources?.filter(s=>s.status!=='ok').length||0;
  notice(`台灣資料日 ${state.overview?.asOf||'未載入'} · 美國${state.us?'延遲行情，最近擷取 '+localTime(state.us.fetchedAt):'行情尚未取得'} · 漲紅跌綠；行情時間各自標示${failed?` · ${failed} 個美國來源未更新，請見資料來源`:''}`,!!failed);
}
function lineChart(lines,{percent=false,includeZero=false,label='行情圖'}={}){
  if(!lines.length||!lines.some(l=>l.points.length))return '<div class="empty">目前沒有足夠資料繪製走勢。</div>';
  const W=760,H=265,L=56,R=14,T=18,B=34;
  let values=lines.flatMap(l=>l.points.map(p=>p.value)).filter(finite);if(includeZero)values.push(0);
  let low=Math.min(...values),high=Math.max(...values);const span=high-low||Math.abs(high)*.02||1;low-=span*.12;high+=span*.12;
  const numericX=lines.every(l=>l.points.every(p=>finite(p.x)));
  const allX=numericX?lines.flatMap(l=>l.points.map(p=>p.x)):[];
  const minX=Math.min(...allX),maxX=Math.max(...allX);
  const x=(i,n,p)=>L+(numericX?(p.x-minX)/(maxX-minX||1):i/Math.max(n-1,1))*(W-L-R),y=v=>T+(high-v)/(high-low)*(H-T-B);
  let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${escape(label)}"><title>${escape(label)}</title>`;
  for(let i=0;i<5;i++){const v=low+(high-low)*i/4,yy=y(v);svg+=`<line x1="${L}" x2="${W-R}" y1="${yy}" y2="${yy}" stroke="#25313f" stroke-dasharray="3 5"/><text x="${L-10}" y="${yy+4}" text-anchor="end" fill="#8397ad" font-size="12">${fmt(v,percent?1:0)}${percent?'%':''}</text>`;}
  if(low<0&&high>0)svg+=`<line x1="${L}" x2="${W-R}" y1="${y(0)}" y2="${y(0)}" stroke="#5d7185" stroke-dasharray="4 4"/>`;
  lines.forEach((line,j)=>{
    const path=line.points.map((p,i)=>`${i?'L':'M'}${x(i,line.points.length,p).toFixed(2)},${y(p.value).toFixed(2)}`).join(' ');
    svg+=`<path d="${path}" fill="none" stroke="${colors[j%3]}" stroke-width="2.3" stroke-linejoin="round"/>`;
    const p=line.points.at(-1);if(p)svg+=`<circle cx="${x(line.points.length-1,line.points.length,p)}" cy="${y(p.value)}" r="3" fill="${colors[j%3]}"><title>${escape(line.name)}: ${fmt(p.value)}${percent?'%':''}</title></circle>`;
    if(j===0){const ticks=[0,Math.floor((line.points.length-1)/3),Math.floor((line.points.length-1)*2/3),line.points.length-1];for(const i of [...new Set(ticks)])svg+=`<text x="${x(i,line.points.length,line.points[i])}" y="${H-5}" fill="#8397ad" font-size="12" text-anchor="${i===0?'start':i===line.points.length-1?'end':'middle'}">${escape(line.points[i].day)}</text>`;}
  });return svg+'</svg>';
}
function spark(history){if(!history?.length)return '';const points=history.slice(-22).map(p=>p.close),min=Math.min(...points),max=Math.max(...points);return `<svg class="metric-spark" viewBox="0 0 75 28" aria-hidden="true"><polyline fill="none" stroke="#658d97" stroke-width="1.5" points="${points.map((p,i)=>`${i/(points.length-1||1)*75},${25-(p-min)/(max-min||1)*22}`).join(' ')}"/></svg>`;}
function renderOverview(){
  const tw=state.overview?.highlights||[],us=state.us?.futures||[];
  const rows=remember(state.market==='TW'?tw:state.market==='US'?us:publicSite?tw:[...tw.slice(0,1),...us]);
  $('metrics').innerHTML=rows.slice(0,4).map(r=>`<article class="metric-card"><div class="metric-top"><span>${escape(r.name)}</span><span class="market-abbr">${r.market}</span></div><div class="metric-value">${fmt(r.price,4)}</div><div class="metric-bottom"><span class="${tone(r.changePct)}" title="${escape(r.previousPriceDate ? '比較 ' + r.previousPriceDate + ' 至 ' + r.priceDate : '來源漲跌幅')}">${sign(r.changePct)}</span>${spark(r.history)}<span>${escape(r.contract)} · ${r.currency}</span></div></article>`).join('')||'<p class="empty">此市場尚無行情。</p>';
  $('watch-rows').innerHTML=rows.map(r=>`<tr><td><strong>${escape(r.name)}</strong><small>${escape(r.contractName||r.key)}</small></td><td><span class="market-abbr">${r.market==='TW'?'台灣':'美國'}</span></td><td class="num">${fmt(r.price,4)}<small>${r.currency} · ${escape(r.priceType)}</small></td><td class="num ${tone(r.changePct)}">${sign(r.changePct)}${r.previousPriceDate?`<small>自 ${escape(r.previousPriceDate)}</small>`:''}</td><td class="num">${fmt(r.volume,0)}</td><td><small>${escape(quoteTime(r))}${r.stale?' · 保留舊資料':''}</small></td><td><button class="row-button" data-detail="${rowId(r)}">分析</button></td></tr>`).join('');
  let positioning=state.overview?.options||{},title='台指選擇權結構';
  if(state.market==='US'){
    const spy=state.us?.options?.find(o=>o.symbol==='SPY');const expiry=spy?.rows.map(r=>r.expiry).sort()[0];const sums=putCallRatio(spy?.rows.filter(r=>r.expiry===expiry)||[]);
    positioning={expiry,callOi:sums.call,putOi:sums.put};title='SPY 選擇權結構';
  }
  document.querySelector('.market-structure h2').textContent=title;
  const {callOi=0,putOi=0,expiry}=positioning,total=callOi+putOi;
  $('pcr-expiry').textContent=`最近到期 ${expiry||'—'} · ${positioning.asOf||'同一期別'}`;
  $('pcr-value').textContent=callOi&&finite(putOi)?fmt(putOi/callOi):'—';
  $('ratio-bar').innerHTML=total?`<span style="width:${callOi/total*100}%;background:${colors[0]}"></span><span style="flex:1;background:${colors[1]}"></span>`:'';
  $('ratio-labels').innerHTML=`<span class="cyan">Call ${fmt(callOi,0)}</span><span class="purple">Put ${fmt(putOi,0)}</span>`;
  for(const kind of ['futures','options','warrants'])$(''+kind+'-count').textContent=fmt(state.overview?.counts[kind],0)+' 台灣商品';
  renderOverviewChart();
}
function renderOverviewChart(){
  if(state.market==='TW'||(publicSite&&state.market==='ALL')){
    $('chart-title').textContent='台指期貨月份結構';$('chart-legend').innerHTML='<span><i class="legend-dot cyan-bg"></i>各到期月份結算價</span>';
    const rows=(state.overview?.term||[]).filter(r=>finite(r.price));
    $('overview-chart').innerHTML=lineChart([{name:'台指期貨',points:rows.map(r=>({day:r.month,value:r.price}))}],{label:'台指期貨各月份結算價'});
    $('chart-caption').textContent=`資料日 ${state.overview?.quoteDates?.futures||state.overview?.asOf||'—'} · 橫軸為到期月份，非歷史走勢。`;
  }else{
    $('chart-title').textContent='美國股指期貨走勢比較';
    const selected=(state.us?.futures||[]).filter(r=>['ES=F','NQ=F','YM=F'].includes(r.key));
    const series=selected.map(r=>({...r,history:state.range==='1m'?r.history.slice(-22):r.history}));
    const lines=normalizedHistory(series);
    $('chart-legend').innerHTML=lines.map((l,i)=>`<span><i class="legend-dot" style="background:${colors[i]}"></i>${escape(l.key.replace('=F',''))} <b>${sign(l.points.at(-1)?.value)}</b></span>`).join('');
    $('overview-chart').innerHTML=lineChart(lines,{percent:true,includeZero:true,label:'美國三大股指期貨共同日期累積報酬率'});
    $('chart-caption').textContent=lines.length?`${lines[0].points[0].day} — ${lines[0].points.at(-1).day} · 共同起日歸零；未調整換月的日線，非投資組合報酬。`:publicSite?'公開版提供美國官方行情入口，尚未提供可公開轉發的美國歷史資料。':'美國歷史資料尚未載入，請更新行情或查看資料來源。';
  }
  document.querySelector('[aria-label="比較期間"]').hidden=publicSite||state.market==='TW';
}
async function loadProducts(kind){
  const ticket=++state.route;
  $('product-rows').innerHTML='<tr><td colspan="9" class="empty">正在讀取商品行情…</td></tr>';
  if(!state.tw[kind])state.tw[kind]=(await json(`data/analysis/tw-${kind}.json`)).rows;
  if(ticket!==state.route||state.view!==kind)return;
  const us=kind==='futures'?state.us?.futures||[]:kind==='options'?(state.us?.options||[]).flatMap(o=>o.rows.map(r=>({...r,stale:o.stale}))):[];
  state.rows=remember(state.market==='TW'?state.tw[kind]:state.market==='US'?us:[...state.tw[kind],...us]);
  $('cp-label').hidden=kind==='futures';$('option-distribution').hidden=kind!=='options'||(publicSite&&state.market==='US');
  $('products-heading').textContent={futures:'期貨契約',options:'選擇權契約',warrants:'上市櫃權證'}[kind];
  $('products-note').textContent=kind==='warrants'?'權證限台灣上市櫃；成交量為單位數，1 張＝1,000 單位。':kind==='options'?'台灣參考價優先採結算價；美國採買賣中價或最後成交價，限 90 天內到期。':'台灣各月份契約；美國為來源指定近月連續期貨，成交量單位為口。';
  if(publicSite&&kind!=='warrants')$('products-note').textContent=state.market==='US'?'請從上方官方入口查看美國行情；損益試算可自行輸入報價。':'台灣每日行情，參考價優先採結算價；美國行情由上方官方入口查看。';
  resetFilters();
}
function optionList(id,values,selected=''){
  $(id).replaceChildren(new Option('全部',''),...[...new Set(values.filter(Boolean))].sort().map(v=>new Option(v,v)));
  $(id).value=selected;
}
function resetFilters(){
  $('product-search').value='';$('cp-filter').value='';$('sort-filter').value='volume';state.page=0;
  const field=state.view==='warrants'?'underlying':'contract';
  optionList('underlying-filter',state.rows.map(r=>r[field]));
  if(state.view==='options')$('underlying-filter').value=state.market==='US'?'SPY':'TXO';
  updateExpiries(state.view==='options');renderProducts();
}
function updateExpiries(selectNearest=false){
  const field=state.view==='warrants'?'underlying':'contract',under=$('underlying-filter').value;
  const rows=state.rows.filter(r=>!under||r[field]===under);
  const values=rows.map(r=>r.expiry||r.month);
  optionList('expiry-filter',values);
  if(selectNearest){
    const active=rows.filter(r=>r.expiry && (r.market==='TW'?r.expiry>r.priceDate:r.expiry>=r.priceDate)).map(r=>r.expiry).sort();
    $('expiry-filter').value=active[0]||values.filter(Boolean).sort()[0]||'';
  }
}
function renderProducts(){
  const kind=state.view,field=kind==='warrants'?'underlying':'contract',search=$('product-search').value.toLowerCase().trim(),under=$('underlying-filter').value,expiry=$('expiry-filter').value,cp=$('cp-filter').value,sort=$('sort-filter').value;
  const scope=state.rows.filter(r=>(!under||r[field]===under)&&(!expiry||(r.expiry||r.month)===expiry)&&(!search||`${r.name} ${r.key} ${r.underlying}`.toLowerCase().includes(search)));
  const rows=scope.filter(r=>!cp||r.cp===cp).sort((a,b)=>sort==='expiry'?(a.expiry||'9999').localeCompare(b.expiry||'9999'):sort==='spread'?(spreadPercent(a)??Infinity)-(spreadPercent(b)??Infinity):(b[sort]??-1)-(a[sort]??-1));
  state.filtered=rows;const pages=Math.ceil(rows.length/25);state.page=Math.min(state.page,Math.max(0,pages-1));
  $('result-count').textContent=`${fmt(rows.length,0)} 筆結果`;
  $('product-head').innerHTML=`<tr><th>商品 / 契約</th><th>市場 / 到期</th><th class="num">參考價</th>${kind!=='futures'?'<th class="num">履約價</th>':'<th class="num">區間漲跌</th>'}<th class="num">成交量</th><th class="num">${kind==='warrants'?'行使比例':'未平倉量'}</th><th class="num">價差率</th><th>資料時間</th><th></th></tr>`;
  $('product-rows').innerHTML=rows.slice(state.page*25,state.page*25+25).map(r=>`<tr><td><strong>${escape(r.name)}</strong><small>${escape(r.key)}${r.cp?' · '+(r.cp==='C'?'Call':'Put'):''}</small></td><td>${r.market==='TW'?'台灣':'美國'}<small>${escape(r.expiry||r.month||r.contractName||'近月連續')}</small></td><td class="num">${fmt(r.price,4)}<small>${r.currency} · ${escape(r.priceType)}</small></td><td class="num ${kind==='futures'?tone(r.changePct):''}">${kind==='futures'?sign(r.changePct):fmt(r.strike)}${kind==='futures'&&r.previousPriceDate?`<small>自 ${escape(r.previousPriceDate)}</small>`:''}</td><td class="num">${fmt(r.volume,0)}</td><td class="num">${fmt(kind==='warrants'?r.ratio:r.oi,kind==='warrants'?5:0)}</td><td class="num">${finite(spreadPercent(r))?fmt(spreadPercent(r))+'%':'—'}</td><td><small>${escape(quoteTime(r))}${r.stale?' · 舊資料':''}</small></td><td><button class="row-button" data-detail="${rowId(r)}">分析</button></td></tr>`).join('')||`<tr><td colspan="9" class="empty">${kind==='warrants'&&state.market==='US'?'美國區未提供台灣式上市權證，請切換至台灣市場。':'此篩選沒有符合商品，請調整條件。'}</td></tr>`;
  $('page-count').textContent=rows.length?`第 ${state.page+1} / ${pages} 頁 · 每頁 25 筆`:'沒有符合的行情';$('previous-page').disabled=state.page===0;$('next-page').disabled=state.page>=pages-1;
  if(kind==='options')renderDistribution(scope,under,expiry);
}
function renderDistribution(rows,under,expiry){
  if(!under||!expiry||new Set(rows.map(r=>r.market)).size>1){$('distribution-chart').innerHTML='<div class="empty">請選擇單一商品及到期日，以比較相同條件的部位。</div>';$('distribution-caption').textContent='不混合不同標的與期別。';return;}
  const sums=putCallRatio(rows),groups=new Map();
  for(const r of rows){if(!finite(r.strike))continue;if(!groups.has(r.strike))groups.set(r.strike,{strike:r.strike,C:0,P:0});groups.get(r.strike)[r.cp]+=r.oi||0;}
  const all=[...groups.values()].sort((a,b)=>a.strike-b.strike);
  const spot=rows.find(r=>finite(r.underlyingClose))?.underlyingClose||(under==='TXO'?state.overview?.highlights[0]?.price:null);
  const center=spot??all[Math.floor(all.length/2)]?.strike;
  const shown=all.sort((a,b)=>Math.abs(a.strike-center)-Math.abs(b.strike-center)).slice(0,21).sort((a,b)=>a.strike-b.strike);
  $('distribution-caption').textContent=`${under} · ${expiry} · P/C ${fmt(sums.ratio)}（此商品、期別與搜尋條件，含買賣權） · 圖示${spot?'參考價附近':'中間'} ${shown.length} 個履約價`;
  if(!shown.length){$('distribution-chart').innerHTML='<div class="empty">此條件沒有未平倉資料。</div>';return;}
  const W=960,H=245,L=55,B=35,T=18,max=Math.max(1,...shown.flatMap(g=>[g.C,g.P])),step=(W-L-15)/shown.length,bar=Math.min(14,step*.3);
  let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${escape(under)} ${expiry} 買賣權未平倉分布"><title>未平倉口數，非成交量</title>`;
  for(let i=0;i<=4;i++){const y=H-B-i/4*(H-T-B);svg+=`<line x1="${L}" x2="${W}" y1="${y}" y2="${y}" stroke="#25313f"/><text x="${L-8}" y="${y+4}" text-anchor="end" font-size="12" fill="#8397ad">${fmt(max*i/4,0)}</text>`;}
  shown.forEach((g,i)=>{for(const [j,cp] of ['C','P'].entries()){const height=g[cp]/max*(H-T-B),x=L+i*step+step/2+(j-1)*bar;svg+=`<rect x="${x}" y="${H-B-height}" width="${bar-2}" height="${height}" rx="2" fill="${colors[j]}"><title>${g.strike} ${cp}: ${g[cp]} 口</title></rect>`;}if(i%Math.ceil(shown.length/9)===0)svg+=`<text x="${L+i*step+step/2}" y="${H-10}" text-anchor="middle" font-size="12" fill="#8397ad">${g.strike}</text>`;});$('distribution-chart').innerHTML=svg+'</svg>';
}
function showDetail(row){
  state.selected=row;$('detail-code').textContent=`${row.market} / ${row.key}`;$('detail-title').textContent=row.name;
  const fields=[['計價幣別',row.currency],['到期日',row.expiry||row.month||'來源近月契約'],['履約價',fmt(row.strike)],['買價 / 賣價',`${fmt(row.bid)} / ${fmt(row.ask)}`],['成交量',fmt(row.volume,0)],['未平倉量',fmt(row.oi,0)],['契約乘數',fmt(row.multiplier)],['行使比例',fmt(row.ratio,5)],['原始保證金',finite(row.initialAmount)?fmt(row.initialAmount)+' '+row.currency:'未提供'],['隱含波動率',finite(row.iv)?fmt(row.iv*100)+'%':'未提供']];
  $('detail-body').innerHTML=`<div class="detail-price">${fmt(row.price,4)} <small>${row.currency}</small></div><p class="muted">${escape(row.priceType)} · ${escape(quoteTime(row))}</p><dl class="detail-grid">${fields.map(([k,v])=>`<div><dt>${k}</dt><dd>${escape(v)}</dd></div>`).join('')}</dl><p class="detail-note">${row.market==='TW'?'日行情快照。最後揭示買賣價不代表目前可以成交的價格。':'來源延遲行情。選擇權中價是買賣價的平均值，並非可保證成交的價格。'}${row.lastTradeTimeRaw?' 最後成交：'+escape(row.lastTradeTimeRaw)+'（來源原始時間）。':''}</p>`;
  $('detail-calculate').disabled=!finite(row.price)||!finite(row.multiplier)||row.multiplier<=0||row.adjusted||finite(row.cap)||finite(row.floor)||(row.market==='TW'&&row.expiry&&row.expiry<=row.priceDate)||(row.kind!=='futures'&&(!finite(row.strike)||!['C','P'].includes(row.cp)))||(row.kind==='warrants'&&(!finite(row.ratio)||row.ratio<=0));
  if($('detail-calculate').disabled)$('detail-body').insertAdjacentHTML('beforeend','<p class="detail-note amber">此商品已到期、缺少報價／規格，或含調整／特殊條款，不能直接帶入標準試算。</p>');
  $('detail-dialog').showModal();
}
function useInCalculator(row){
  const form=$('calculator').elements;
  const values={kind:row.kind,currency:row.currency,entry:row.price,strike:row.strike??row.price,multiplier:row.multiplier,quantity:1,ratio:row.ratio??1,cp:row.cp||'C',side:'long',target:row.kind==='futures'?row.price:row.underlyingClose??row.strike};
  for(const [key,value]of Object.entries(values))form.namedItem(key).value=value??'';
  $('calculator-context').textContent=`帶入 ${row.name} ${row.key} · ${row.priceType} · ${quoteTime(row)}；可修改價格假設。`;
  updateCalculator();
}
function updateCalculator(){
  const form=$('calculator'),f=form.elements,kind=f.kind.value;
  f.cp.disabled=f.strike.disabled=kind==='futures';f.ratio.disabled=kind!=='warrants';f.side.disabled=kind!=='futures';if(kind!=='futures')f.side.value='long';
  const value=name=>f.namedItem(name).value===''?null:Number(f.namedItem(name).value);
  const input={kind,cp:f.cp.value,side:f.side.value,entry:value('entry'),strike:value('strike'),ratio:kind==='warrants'?value('ratio'):1,multiplier:value('multiplier'),quantity:value('quantity')},target=value('target');
  const result=payoff(input,target),be=breakEven(input);
  if(result===null||!form.checkValidity()){$('calculator-error').textContent='請填寫有效價格、正數乘數與整數口數。';$('payoff-result').textContent=$('break-even').textContent='—';$('payoff-chart').innerHTML='';return;}
  $('calculator-error').textContent='';$('payoff-result').textContent=`${result>0?'+':''}${fmt(result)} ${f.currency.value}`;$('payoff-result').className=tone(result);$('break-even').textContent=be===null?'非負標的價格下無法兩平':fmt(be);
  const center=kind==='futures'?input.entry:input.strike;
  const width=Math.max(center*.12,Math.abs(target-center)*1.2,Math.abs((be??center)-center)*1.2,1);
  const low=Math.max(0,center-width),high=center+width;
  const targets=[low,high,target,center,...Array.from({length:41},(_,i)=>low+(high-low)*i/40),...(be===null?[]:[be])].filter(v=>v>=low&&v<=high).sort((a,b)=>a-b);
  $('payoff-chart').innerHTML=lineChart([{name:'損益',points:[...new Set(targets)].map(x=>({x,day:fmt(x,0),value:payoff(input,x)}))}],{includeZero:true,label:'指定商品在不同結算價格下的損益'});
  $('payoff-risk').textContent=kind==='futures'?'橫軸：期貨結算價；縱軸：損益。保證金不是最大損失，實際交易仍有追繳與強制平倉風險。':`橫軸：標的到期結算價；縱軸：損益。買方權利金成本 ${fmt(input.entry*input.multiplier*input.quantity)} ${f.currency.value}，可能全數損失。${kind==='warrants'?'一般權證的簡化到期公式；實際結算依發行條款。':'到期前市價仍受波動率與剩餘時間影響。'}`;
}
function renderSources(){
  $('tw-source-time').textContent=`台灣行情基準日：${state.overview?.asOf||'未取得'}。台灣行情由既有每日抓取與建置流程更新。`;
  if(publicSite){
    const dates=state.overview?.quoteDates||{};
    $('tw-source-time').textContent=`期貨：${dates.futures||'未取得'}；選擇權：${dates.options||'未取得'}；權證：${dates.warrants||'未取得'}。各商品的實際行情日期亦會逐筆標示。`;
    $('us-fetch-time').textContent='台北時間平日 19:17、週二至週六 06:17 排程更新；平台可能延遲。';
    $('source-status').textContent='台灣採官方日資料；美國採官方行情入口，公開站不提供美國原始行情下載。按「載入最新資料」可讀取最近一次成功發布的版本。';
    return;
  }
  $('us-fetch-time').textContent=`最近擷取：${localTime(state.us?.fetchedAt)}`;
  $('source-status').innerHTML=(state.us?.sources||[]).map(s=>`<div class="source-row"><strong>${escape(s.id)}</strong><span class="${s.status==='ok'?'cyan':'amber'}">${s.status==='ok'?'讀取成功':s.status==='stale'?'更新失敗，保留舊行情':'暫無可用資料'}</span><small>${s.error?escape(s.error):escape(localTime(s.checkedAt))}</small></div>`).join('')||'<p class="empty">尚無美國資料，請執行更新。</p>';
}
async function route(){
  state.view=labels[location.hash.slice(1)]?location.hash.slice(1):'overview';state.route++;
  document.querySelectorAll('[data-nav]').forEach(a=>{a.classList.toggle('active',a.dataset.nav===state.view);if(a.dataset.nav===state.view)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  $('page-title').innerHTML=labels[state.view]+'<span class="title-dot">.</span>';$('breadcrumb-current').textContent=labels[state.view];$('page-description').textContent=descriptions[state.view];
  const target=['futures','options','warrants'].includes(state.view)?'products':state.view;
  document.querySelectorAll('[data-view]').forEach(el=>el.hidden=el.dataset.view!==target);
  document.querySelector('[aria-label="市場篩選"]').hidden=['calculator','sources'].includes(state.view);
  if(publicSite){
    $('public-us-links').hidden=state.market==='TW'||!['overview','futures','options'].includes(state.view);
    document.querySelector('.market-structure').hidden=state.market==='US';
    document.querySelector('.overview-grid').hidden=state.market==='US';
    $('metrics').hidden=state.market==='US';
  }
  try{if(target==='products')await loadProducts(state.view);else if(target==='overview')renderOverview();else if(target==='sources')renderSources();else updateCalculator();}catch(e){notice(e.message+'；請重新載入或查看資料來源。',true);$('product-rows').innerHTML='<tr><td colspan="9" class="empty">讀取失敗，請稍後重試。</td></tr>';}
}
document.addEventListener('click',e=>{
  const detail=e.target.closest('[data-detail]');if(detail){const r=rowMap.get(detail.dataset.detail);if(r)showDetail(r);}
  const market=e.target.closest('[data-market]');if(market){state.market=market.dataset.market;document.querySelectorAll('[data-market]').forEach(b=>b.setAttribute('aria-pressed',String(b===market)));route();}
  const range=e.target.closest('[data-range]');if(range){state.range=range.dataset.range;document.querySelectorAll('[data-range]').forEach(b=>b.setAttribute('aria-pressed',String(b===range)));renderOverviewChart();}
});
$('underlying-filter').addEventListener('change',()=>{updateExpiries(state.view==='options');state.page=0;renderProducts();});
for(const id of ['product-search','expiry-filter','cp-filter','sort-filter'])$(id).addEventListener(id==='product-search'?'input':'change',()=>{state.page=0;renderProducts();});
$('reset-filters').addEventListener('click',resetFilters);$('previous-page').addEventListener('click',()=>{state.page--;renderProducts();});$('next-page').addEventListener('click',()=>{state.page++;renderProducts();});
$('close-detail').addEventListener('click',()=>$('detail-dialog').close());$('detail-calculate').addEventListener('click',()=>{useInCalculator(state.selected);$('detail-dialog').close();location.hash='calculator';});
$('calculator').addEventListener('input',updateCalculator);$('calculator').addEventListener('submit',e=>e.preventDefault());
$('refresh-us').addEventListener('click',async()=>{
  const button=$('refresh-us');button.disabled=true;button.textContent='更新中…';
  if(publicSite){
    try{state.overview=await json('data/analysis/overview.json');state.tw={};rowMap.clear();statusNotice();await route();}
    catch(e){notice(e.message,true);}finally{button.disabled=false;button.textContent='載入最新資料';}
    return;
  }
  try{const response=await fetch('/api/analysis/refresh-us',{method:'POST'});if(!response.ok)throw new Error(response.status===404||response.status===405?'此為靜態預覽，請以分析伺服器開啟，或執行行情更新後重新建置。':'行情更新失敗，請稍後重試。');state.us=await response.json();remember(state.us.futures||[]);statusNotice();await route();}
  catch(e){notice(e.message,true);}finally{button.disabled=false;button.textContent='更新美國行情';}
});
window.addEventListener('hashchange',route);
const results=await Promise.allSettled([json('data/analysis/overview.json'),...(publicSite?[]:[json('data/analysis/us-market.json')])]);
if(results[0].status==='fulfilled')state.overview=results[0].value;
if(results[1]?.status==='fulfilled')state.us=results[1].value;
const first=state.overview?.highlights?.[0]||state.us?.futures?.[0];if(first)useInCalculator(first);
statusNotice();await route();
