import {esc, finite, fmt} from './model.mjs';

const delta = (a,b) => finite(a) && finite(b) ? a-b : null;
const signed = (x, unit='') => finite(x) ? (x > 0 ? '+' : '')+fmt(x,2)+unit : '—';
const ratio = (part,total) => finite(part) && finite(total) && total > 0 ? 100*part/total : null;
const pct = v => finite(v) ? fmt(v,2)+'%' : '—';
const hits = s => s.call+s.put;
const rowRate = s => ratio(hits(s),s.eligible);
const types = {futures:'臺股期貨',options:'臺指選擇權'};
const optionSide = cp => cp === 'C' ? '買權' : cp === 'P' ? '賣權' : '';
const numberWithChange = (v,old) => fmt(v)+'<small>變動 '+signed(delta(v,old))+'</small>';
const coverageLabels = {available:'行情齊備',partial:'部分資料',missing:'資料不足',closed:'休市'};

export function movement(w,s) {
  if (!finite(w) || !finite(s)) return '缺同期間價格';
  if (w === 0 || s === 0) return '含持平';
  return Math.sign(w) === Math.sign(s) ? '同向' : '反向';
}

export function concentration(row, field) {
  return ratio(row?.[field], row?.oi);
}

export function strikeRanking(rows, month, side) {
  const groups = new Map();
  for (const [expiry,strike,call,put] of rows) {
    if (month !== 'all' && month !== expiry) continue;
    const value = side === 'C' ? call : put;
    const entry = groups.get(strike) || {strike, total:0, missing:false};
    if (finite(value)) entry.total += value; else entry.missing = true;
    groups.set(strike,entry);
  }
  const known = [...groups.values()].filter(x => !x.missing).sort((a,b) => b.total-a.total || a.strike-b.strike);
  return {known, missing:groups.size-known.length, total:known.reduce((n,x) => n+x.total,0)};
}

function dayHeaders(index,kind,day,prior,select,days=index.dates) {
  const file = 'data/derivatives/daily-reports/'+kind+'/'+day+'.json';
  return `<section class="panel"><div class="panel-head"><div>${select('report-date','行情日',days.slice().reverse(),day)}
    <small>前一收錄日 ${esc(prior || '尚無')}</small></div><div><button id="report-print">列印目前畫面／PDF</button> <a class="text-link" href="${esc(file)}" download>下載完整本期資料</a></div></div>
    ${index.status === 'retained' ? '<p class="notice warning">最近一輪資料尚未齊備，保留上次核對通過的報告。'+(index.attemptSources||[]).map(s=>esc(s.id)+' '+esc(s.date||'缺日期')+' '+esc(s.status)).join(' · ')+'</p>' : ''}</section>`;
}

function sources(report) {
  return `<details class="panel"><summary>本期資料來源與日期</summary>${report.sources.map(s =>
    `<p><a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.id)}</a> · ${esc(s.date)}</p>`).join('')}</details>`;
}

function pagination(pos, size, length, key) {
  const pages = Math.max(1,Math.ceil(length/size));
  return `<div class="pagination"><span>第 ${pos+1}／${pages} 頁 · 共 ${fmt(length)} 筆</span><div>
    <button data-pager="${key}" value="-1" ${pos===0 ? 'disabled':''}>上一頁</button>
    <button data-pager="${key}" value="1" ${pos+1>=pages ? 'disabled':''}>下一頁</button></div></div>`;
}

export function createReports({load,table,tr,select,metric}) {
  const state = {sort:'count', min:'1', query:'', stock:'', page:0, details:0, month:'all'};
  function coveragePanel(coverage,index) {
    if (!coverage) return '';
    const entries=Object.entries(coverage.days);
    const count=status=>entries.filter(([,v])=>v.status===status).length;
    return `<section class="panel"><h2>每日資料涵蓋</h2><p>${esc(coverage.start)} 至 ${esc(coverage.end)} · 行情齊備 ${count('available')} 日、部分資料 ${count('partial')} 日、資料不足 ${count('missing')} 日、休市 ${count('closed')} 日。</p>
      <details id="report-coverage"><summary>逐日查看檔數與不足原因</summary>
      ${table(['日期','狀態','收錄／可比較','量增檔數','資料說明'],entries.slice().reverse().map(([day,v])=>tr([
        index.dates.includes(day)?`<a href="#warrant-volume-report?date=${esc(day)}">${esc(day)}</a>`:esc(day),
        coverageLabels[v.status]||esc(v.status),fmt(v.total)+'／'+fmt(v.eligible),fmt(v.surges),
        esc([...(v.notes||[]),...(v.missingSources||[]).map(s=>s.market+' '+s.reason)].join('；'))])))}
      <p class="note">行情齊備表示已取得兩市場的同日價量及前五交易日基準。個別權證仍可能因無成交、歷史不足或均量為零而無法比較。歷史個股分類採各期註明的官方名錄，補抓資料可能含官方後續修訂。</p></details></section>`;
  }
  function volume(report) {
    const stocks = report.stocks;
    const eligible = stocks.reduce((n,s)=>n+s.eligible,0), total = stocks.reduce((n,s)=>n+s.total,0);
    const call = stocks.reduce((n,s)=>n+s.call,0), put = stocks.reduce((n,s)=>n+s.put,0);
    const ranked = stocks.filter(s=>s.eligible>=Number(state.min) && (s.code.includes(state.query) || s.name.includes(state.query)))
      .sort((a,b)=>(state.sort==='rate' ? (rowRate(b) ?? -1)-(rowRate(a) ?? -1) : hits(b)-hits(a)) || hits(b)-hits(a) || a.code.localeCompare(b.code));
    const selected = ranked.find(s=>s.code===state.stock) || ranked[0];
    const size=25;
    state.page=Math.min(state.page,Math.max(0,Math.ceil(ranked.length/size)-1));
    const top = stocks.filter(s=>hits(s)>0).sort((a,b)=>hits(b)-hits(a) || a.code.localeCompare(b.code))[0];
    const leaders = top ? `<p>入榜檔數最多的是 <strong>${esc(top.name)}</strong>，${fmt(top.eligible)} 檔可比較，其中 ${fmt(top.call)} 檔認購、${fmt(top.put)} 檔認售放量，占 ${pct(rowRate(top))}。</p>` : '<p>本期沒有權證通過量增門檻。</p>';
    let detail = '';
    if (selected) {
      const s = selected;
      const counts = {'同向':0,'反向':0,'含持平':0,'缺同期間價格':0};
      for (const r of s.hits) counts[movement(r.warrantPct,r.stockPct)]++;
      state.details=Math.min(state.details,Math.max(0,Math.ceil(s.hits.length/size)-1));
      detail = `<section class="panel"><h2 id="report-stock-heading">${esc(s.code)} ${esc(s.name)} · 入榜明細</h2>
        <p>認購 ${fmt(s.call)}／${fmt(s.callBase)} 檔；認售 ${fmt(s.put)}／${fmt(s.putBase)} 檔（分母為各權別可比較檔數）。入榜權證與現股同向 ${counts['同向']} 檔，反向 ${counts['反向']} 檔，含持平 ${counts['含持平']} 檔，缺價 ${counts['缺同期間價格']} 檔。</p>
        ${table(['權證／代碼','權別','成交量（張）','前五期均量倍數','比較起日','權證漲跌','現股漲跌','價格方向'],s.hits.slice(state.details*size,(state.details+1)*size).map(r=>tr([esc(r.name)+'<small>'+esc(r.code)+'</small>',r.cp==='C'?'認購':'認售',fmt(r.volume),fmt(r.multiple,2)+' 倍',esc(r.previous || '—'),signed(r.warrantPct,'%'),signed(r.stockPct,'%'),movement(r.warrantPct,r.stockPct)])))}
        ${pagination(state.details,size,s.hits.length,'details')}<p><a href="#warrant-daily?underlying=${esc(s.code)}">查看此標的最新逐檔觀察表 →</a></p></section>`;
    }
    const scope = report.coverage;
    const coverageNote = scope ? `<p class="notice ${scope.status==='partial'?'warning':''}">本期${coverageLabels[scope.status]} · ${(scope.markets||[]).map(m=>m==='TWSE'?'上市':'上櫃').join('、')}。${esc((scope.notes||[]).join(' '))}</p>`:'';
    const baseline = report.rules.version==='volume-only-v2' ? '交易日' : '已收錄行情日';
    return `${coverageNote}<section class="panel"><div class="metrics">${metric('收錄個股',fmt(stocks.length),fmt(total)+' 檔個股權證')}
      ${metric('可比較權證',fmt(eligible),fmt(total-eligible)+' 檔缺資料或不符資格')}
      ${metric('量增權證',fmt(call+put),'認購 '+fmt(call)+' · 認售 '+fmt(put))}
      ${metric('有權證入榜的個股',fmt(stocks.filter(s=>hits(s)>0).length),'全體可比較權證入榜率 '+pct(ratio(call+put,eligible)))}</div>${leaders}
      <p>量增採當日至少 ${report.rules.minVolume} 張，且達前 ${report.rules.volumeWindow} 個${baseline}均量的 ${report.rules.volumeMultiple} 倍。入榜率＝入榜檔數 ÷ 同標的可比較檔數。均量為零或缺值時不列入分母。</p>
      ${report.baselineDates ? '<p class="note">本期均量基準 '+report.baselineDates.map(esc).join('、')+'。量價資格核對代碼、名稱、權別、標的與逐日價量。</p>':''}</section>
      <section class="panel"><h2>個股放量排行</h2><form id="report-search" class="filters"><label>代碼或名稱<input name="query" value="${esc(state.query)}"></label><button>查詢</button>
      ${select('report-sort','排序',['count','rate'],state.sort,k=>k==='count'?'入榜檔數':'入榜率')}
      ${select('report-min','最少可比較檔數',['0','1','5','10','25','50'],state.min)}</form>
      ${table(['個股／看明細','入榜率','入榜／可比較','認購入榜','認售入榜','收錄總數'],ranked.slice(state.page*size,(state.page+1)*size).map(s=>tr([
        `<button class="text-link" data-report-stock="${esc(s.code)}">${esc(s.code)} ${esc(s.name)}</button>`,pct(rowRate(s)),fmt(hits(s))+'／'+fmt(s.eligible),fmt(s.call),fmt(s.put),fmt(s.total)])))}${pagination(state.page,size,ranked.length,'page')}</section>${detail}
      <details class="panel"><summary>缺漏、無成交與漲跌口徑</summary><p>價格比較本日與前一次有效成交日，現股沿用相同起訖日。兩者按實際價格方向分類，認售權證與現股反向屬正常可能。無成交日留白。金額採未還原收盤價。</p>${table(['排除原因','檔數'],Object.entries(report.excluded).map(([k,v])=>tr([esc(k),fmt(v)])))}</details>`;
  }

  function positioning(report,prev) {
    const p=report.pcr, old=prev?.pcr;
    const dv=delta(p.volumeRatio,old?.volumeRatio), doi=delta(p.oiRatio,old?.oiRatio);
    const sync=finite(dv)&&finite(doi) ? dv===0 || doi===0 ? '至少一項持平' : Math.sign(dv)===Math.sign(doi) ? (dv>0?'同步上升':'同步下降') : '一升一降' : '缺比較期';
    const inst=report.institutions.map(r=>{
      const oldRow=prev?.institutions.find(x=>x.product===r.product && x.investor===r.investor && x.cp===r.cp);
      return tr([types[r.product]+' '+optionSide(r.cp),esc(r.investor),fmt(r.longOi),fmt(r.shortOi),numberWithChange(r.netOi,oldRow?.netOi)]);
    });
    const large=report.large.map(r=>{
      const oldRow=prev?.large.find(x=>x.product===r.product && x.cp===r.cp);
      return tr([esc(r.name)+' '+optionSide(r.cp),fmt(r.oi),...['top5Buy','top5Sell','top10Buy','top10Sell'].map(k=>{
        const value=concentration(r,k); return pct(value)+'<small>'+signed(delta(value,concentration(oldRow,k)),' 個百分點')+'</small>';
      })]);
    });
    const months=[...new Set(report.strikes.map(r=>r[0]))].sort();
    if(!months.includes(state.month)) state.month='all';
    const rankPanels=['C','P'].map(side=>{
      const {known,missing,total}=strikeRanking(report.strikes,state.month,side);
      const oldRanks=prev ? strikeRanking(prev.strikes,state.month,side).known : [];
      const byStrike=new Map(oldRanks.map(x=>[x.strike,x.total]));
      return `<div><h3>${optionSide(side)}未平倉前十履約價</h3>
      ${table(['履約價','未平倉（口）','占已知總量'],known.filter(x=>x.total>0).slice(0,10).map(x=>tr([fmt(x.strike),numberWithChange(x.total,byStrike.get(x.strike)),pct(ratio(x.total,total))])))}
      <small>涵蓋 ${fmt(known.length)} 個完整履約價，${fmt(missing)} 個有缺值。</small></div>`;
    });
    const foreign=report.institutions.find(r=>r.product==='futures' && r.investor.includes('外資'));
    const previousForeign=prev?.institutions.find(r=>r.product==='futures' && r.investor===foreign?.investor);
    const futLarge=report.large.find(r=>r.product==='futures'), oldLarge=prev?.large.find(r=>r.product==='futures');
    const leaders=['C','P'].map(side=>{
      const first=strikeRanking(report.strikes,state.month,side).known[0];
      return optionSide(side)+(first?.total>0 ? '最高為 '+fmt(first.strike)+' 點（'+fmt(first.total)+' 口）' : '尚無可排名資料');
    });
    const channels=['futures','options'].map(k=>`<div><h3>${types[k]}交易通路前五</h3>${table(['期貨商','成交量（口）'],report.brokers[k].map(r=>tr([esc(r.name),fmt(r.volume)])))}</div>`);
    return `<section class="panel"><div class="metrics">${metric('臺指成交量 P/C',pct(p.volumeRatio),'變動 '+signed(dv,' 個百分點'))}
      ${metric('臺指未平倉 P/C',pct(p.oiRatio),'變動 '+signed(doi,' 個百分點'))}
      ${metric('兩種 P/C 變化',sync,'比較前一收錄行情日')}</div>
      <p>成交量：賣權 ${fmt(p.putVolume)} 口／買權 ${fmt(p.callVolume)} 口。未平倉：賣權 ${fmt(p.putOi)} 口／買權 ${fmt(p.callOi)} 口。</p>
      <p>臺股期貨外資淨未平倉 ${signed(foreign?.netOi)} 口，較前期 ${signed(delta(foreign?.netOi,previousForeign?.netOi))} 口。前十大交易人買方集中度 ${pct(concentration(futLarge,'top10Buy'))}（${signed(delta(concentration(futLarge,'top10Buy'),concentration(oldLarge,'top10Buy')),' 個百分點')}），賣方 ${pct(concentration(futLarge,'top10Sell'))}（${signed(delta(concentration(futLarge,'top10Sell'),concentration(oldLarge,'top10Sell')),' 個百分點')}）。</p></section>
      <section class="panel"><h2>三大法人淨未平倉</h2>${table(['商品／權別','法人','多方（口）','空方（口）','淨額／變動（口）'],inst)}<p class="note">選擇權分買、賣權列示。各列是該類法人買方與賣方未平倉之差。</p></section>
      <section class="panel"><h2>大額交易人集中度</h2>${table(['商品／權別','市場未平倉','前五買方','前五賣方','前十買方','前十賣方'],large)}
      <p class="note">各欄＝該側大額交易人部位 ÷ 同商品全月份市場未平倉。變動單位為百分點。前五名包含在前十名內，僅採全體大額交易人口徑。</p></section>
      <section class="panel"><h2>臺指選擇權履約價分布</h2>${select('report-month','到期別',['all',...months],state.month,x=>x==='all'?'全部到期別':x)}
      <p>${leaders.join('，')}。</p><div class="split">${rankPanels.join('')}</div><p class="note">只採一般時段並按相同履約價加總；排名描述持倉分布，單靠未平倉集中度無法確認支撐或壓力。口數相同按履約價由低至高排列。</p></section>
      <details class="panel"><summary>期貨商成交熱度</summary><div class="split">${channels.join('')}</div><p>成交量衡量交易通路的活躍程度，方向須另查法人與部位資料。</p></details>`;
  }

  async function page(route) {
    const warrant = route==='warrant-volume-report', kind=warrant?'warrant-volume':'positioning';
    const index=await load('daily-reports/'+kind+'/index');
    if (!index.dates.length) return '<p class="notice warning">等待第一份同日來源齊備的報告。</p>';
    const coverage=warrant ? await load('daily-reports/warrant-volume/coverage').catch(()=>null) : null;
    const days=[...new Set([...index.dates,...Object.keys(coverage?.days||{})])].sort();
    const coverageHtml=coveragePanel(coverage,index);
    const desired=new URLSearchParams(location.hash.split('?')[1]).get('date');
    if(desired && !index.dates.includes(desired)) {
      const status=coverage?.days[desired];
      return '<div class="daily-report"><section class="panel">'+select('report-date','日期',days.slice().reverse(),desired)+
        '<p class="notice warning">'+esc(desired)+' · '+(status?coverageLabels[status.status]:'此日沒有已保存的報告')+'。</p><p>'+esc((status?.notes||[]).join(' '))+
        '</p><a href="#'+route+'">查看最近一期</a></section>'+coverageHtml+'</div>';
    }
    const day=desired || index.dates.at(-1), prior=index.dates[index.dates.indexOf(day)-1];
    const prefix='daily-reports/'+kind+'/';
    const [now,previous]=await Promise.all([load(prefix+day),prior && !warrant ? load(prefix+prior) : null]);
    return '<div class="daily-report">'+dayHeaders(index,kind,day,prior,select,days)+coverageHtml+(warrant?volume(now):positioning(now,previous))+sources(now)+'</div>';
  }

  function bind(route,rerender) {
    document.getElementById('report-date')?.addEventListener('change',e=>{
      state.page=state.details=0; state.stock=''; location.hash=route+'?date='+e.target.value;
    });
    document.getElementById('report-print')?.addEventListener('click',()=>window.print());
    const form=document.getElementById('report-search');
    form?.addEventListener('submit',e=>{e.preventDefault();state.query=form.elements.query.value.trim();state.page=state.details=0;state.stock='';rerender();});
    for(const [id,key] of [['report-sort','sort'],['report-min','min'],['report-month','month']]) {
      document.getElementById(id)?.addEventListener('change',e=>{state[key]=e.target.value;state.page=state.details=0;state.stock='';rerender();});
    }
    document.querySelectorAll('[data-report-stock]').forEach(el=>el.addEventListener('click',async()=>{
      state.stock=el.dataset.reportStock;state.details=0;await rerender();document.getElementById('report-stock-heading')?.scrollIntoView({block:'start'});
    }));
    document.querySelectorAll('[data-pager]').forEach(el=>el.addEventListener('click',()=>{state[el.dataset.pager]+=Number(el.value);rerender();}));
  }
  return {page,bind};
}
