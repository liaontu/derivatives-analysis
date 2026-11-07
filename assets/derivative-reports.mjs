import {esc,fmt,finite,taipeiTime} from './model.mjs';

const kinds={daily:'每日簡報',weekly:'每週完整分析',monthly:'每月策略檢討'};
const cp=v=>v==='C'?'買權':v==='P'?'賣權':'—';
const n=(v,d=0)=>finite(v)?fmt(v,d):'—';
const signed=(v,d=0)=>finite(v)?(v>0?'+':'')+fmt(v,d):'—';
const pct=v=>finite(v)?signed(v,2)+'%':'—';
const statuses={available:'可用',partial:'部分資料',retained:'沿用舊資料',unavailable:'尚無資料'};
const categories={dealer:'交易商',assetManager:'資產管理機構',leveraged:'槓桿基金',other:'其他申報人',nonreportable:'非申報人'};
export const validDerivativeReportId=id=>/^(daily|weekly|monthly)-\d{4}-\d{2}-\d{2}-r[1-9]\d*-[a-f0-9]{12}$/.test(id||'');
export function reportChoices(index,kind,requested,archived=false){
  const items=(index?.items||[]).filter(i=>i.kind===kind&&validDerivativeReportId(i.id)&&(archived||i.archiveState==='current'||i.id===requested))
    .sort((a,b)=>b.periodEnd.localeCompare(a.periodEnd)||b.revision-a.revision);
  const chosen=items.some(i=>i.id===requested)?requested:items.some(i=>i.id===index.latest?.[kind])?index.latest[kind]:items[0]?.id;
  return {items,chosen};
}
export function eventRows(rows,{tr}){
  return rows.map(r=>tr([esc(r.label)+' · '+cp(r.cp),n(r.evaluated)+'／'+n(r.signals),n(r.distinctSignalDates),pct(r.median),pct(r.positivePct),
    pct(r.mean),pct(r.meanCost1)+'／'+pct(r.meanCost3),
    `未到日 ${n(r.counts?.pending||0)}<small>缺進場 ${n(r.counts?.['missing-entry']||0)} · 缺退出／到期 ${n(r.counts?.['missing-exit']||0)}</small>`,esc(r.assessment)]));
}
export function renderDerivativeReport(r,{table,tr,metric}){
  const panels=r.panels||{}, panel=name=>panels[name]||{rows:[]};
  const period=r.kind==='daily'?r.periodEnd:r.periodStart+'～'+r.periodEnd;
  const notes=r.findings.map(f=>`<article class="derivative-finding"><h3>${esc(f.topic)}</h3><p>${esc(f.fact)}</p><p class="muted">${esc(f.reading)}</p></article>`).join('');
  const inst=['futures-institutional','options-institutional'].flatMap(name=>panel(name).rows.map(x=>tr([
    (name==='futures-institutional'?'大台':'臺指選擇權 '+cp(x.cp))+'<small>'+esc(x.investor)+'</small>',
    esc(panel(name).date||'—'),n(x.longOi),n(x.shortOi),signed(x.netOi),signed(x.changes?.netOi)+'<small>基準 '+esc(panel(name).baseDate||'缺資料')+'</small>'])));
  const futures=panel('futures');
  const large=['futures-large','options-large'].flatMap(name=>panel(name).rows.map(x=>tr([
    esc(x.name||name)+' '+cp(x.cp),esc(panel(name).date||'—'),n(x.top5Buy),n(x.top5Sell),n(x.longShare,2)+'%／'+n(x.shortShare,2)+'%',signed(x.changes?.longShare,2)+'／'+signed(x.changes?.shortShare,2)+'<small>基準 '+esc(panel(name).baseDate||'缺資料')+'</small>'])));
  const us=panel('us-options'),cot=panel('us-cot-financial'),cfe=panel('us-futures');
  const eventHeaders=['規則／權別','可評估／觸發','有效訊號日','報酬中位數','正報酬比例','平均報酬','平均減 1／3 點','排除／待觀察','判定'];
  const s=r.stockOptions,e=r.events;
  const coverage=r.sources.filter(x=>x.status!=='available'||x.missingDates?.length);
  const warning=coverage.length?`<p class="notice warning">${coverage.length} 項來源缺少本期資料、日期未齊或本次收集異常；下方逐項列出。數值不代表全期間完整。</p>`:'';
  return `<section class="panel derivative-summary"><div class="panel-head"><h2>${esc(r.title)}</h2><span class="tag">${esc(statuses[r.status]||r.status)}</span></div>
    <p class="coverage">報告期間 ${esc(period)} · 第 ${n(r.revision)} 版 · 保存時間 ${esc(taipeiTime(r.generatedAt))}（台北）</p>${warning}
    <div class="derivative-findings">${notes}</div>
    <div class="metrics mobile-secondary" data-mobile-label="基準日與回測樣本">${metric('期間基準日',esc(r.baseDate),'台灣按核實交易日曆；缺基準就不計變化')}${metric('選擇權歷史日數',n(e.sessions),'資料至 '+esc(e.through||'尚無資料'))}${metric('到期先前訊號可評估',n(e.followup.evaluated)+'／'+n(e.followup.signals),'缺價與未到日不算零報酬')}</div>
    <p class="report-actions"><a class="text-link" href="data/derivatives/derivative-reports/${esc(r.id)}.html" target="_blank" rel="noopener">開啟完整報告／列印</a> · <a href="data/derivatives/derivative-reports/${esc(r.id)}.html" download>下載 HTML</a> · <a href="data/derivatives/derivative-reports/${esc(r.id)}.json" download>下載 JSON</a></p></section>
    <section class="panel"><h2>台灣期貨與選擇權法人部位</h2>${table(['商品／法人','資料日','多方未平倉','空方未平倉','淨部位（口）','較基準變化'],inst)}
    <p class="note">部位不同可能包含現股避險、套利與不同履約價；不能把買權與賣權口數直接相加當作多空曝險。</p></section>
    <section class="panel"><h2>期間逐日觀察</h2>${table(['日期','外資大台淨部位','投信大台淨部位','選擇權成交量','成交 P/C','未平倉 P/C'],r.timeline.map(x=>tr([
      esc(x.date),signed(x.foreignNetOi),signed(x.trustNetOi),n(x.volume),finite(x.volumeRatio)?n(x.volumeRatio,2)+'%':'—',finite(x.oiRatio)?n(x.oiRatio,2)+'%':'—'])))}
      <p>本期開始前的基準日 ${esc(r.baseDate)}；缺日留白。跨到期日的 P/C 變化不能全部視為新開倉。</p></section>
    <section class="panel"><h2>期貨價量與大額集中度</h2><p>期貨行情資料日 ${esc(futures.date||'尚未收錄本期')}；基準日 ${esc(futures.baseDate||'缺資料')}。各商品取最近有成交月份，同契約比較，沒有連續換月報酬。</p>
      ${table(['契約／月份','最後成交','結算價','來源日漲跌','成交量','未平倉','價格／未平倉變化'],futures.rows.map(x=>tr([esc(x.contract)+'／'+esc(x.month),n(x.last,2),n(x.price,2),pct(x.changePct),n(x.volume),n(x.oi),signed(x.changes?.last,2)+'／'+signed(x.changes?.oi)])))}
      <details><summary>大額交易人（所有月份）</summary>${table(['商品','資料日','前五大多方','前五大空方','各占未平倉','多／空占比變化（百分點）'],large)}<p>台指使用交易所換算口徑，不與大台法人直接相減；多空排行可能不是同一批人。</p></details></section>
    <section class="panel"><h2>履約價部位集中</h2><p>資料日 ${esc(r.strikes.date||'無')}；${esc(r.strikes.scope)}。</p>${table(['到期別','權別','履約價','未平倉','較 '+r.strikes.baseDate+' 變化'],r.strikes.rows.map(x=>tr([esc(x.month),cp(x.cp),n(x.strike),n(x.oi),signed(x.change)])))}<p>只對同一到期別及履約價比較；部位集中不是已確認的支撐或壓力。</p></section>
    <section class="panel"><h2>美國市場觀察</h2><p>Cboe 日期 ${esc(us.date||'無')}；CFTC 部位日期 ${esc(cot.date||'無')}（週資料）。${cot.date&&cot.date<r.periodStart?'本期尚無新版週資料；沿用不代表當日部位沒有變化。':''}</p>
      ${table(['Cboe 類別','成交量','成交 P/C（倍）','未平倉 P/C（倍）'],us.rows.map(x=>tr([esc(x.contract),n(x.totalVolume),n(x.volumeRatio,3),n(x.oiRatio,3)])))}
      ${table(['CFTC 金融期貨','類別','淨部位','較基準變化'],cot.rows.flatMap(x=>(x.categories||[]).filter(c=>['assetManager','leveraged'].includes(c.category)).map(c=>tr([
        esc(x.shortName),esc(categories[c.category]),signed(c.net),signed(c.netChange)+'<small>基準 '+esc(cot.baseDate||'缺資料')+'</small>']))))}
      <details><summary>CFE 波動率期貨結算價</summary><p>資料日 ${esc(cfe.date||'無')}，基準 ${esc(cfe.baseDate||'缺資料')}。</p>${table(['契約','到期日','結算價','同契約變化'],cfe.rows.map(x=>tr([esc(x.symbol),esc(x.expiry),n(x.price,4),signed(x.changes?.price,4)])))}</details>
      <p>美國買賣權比為倍數，台灣表格為百分比。Cboe 涵蓋範圍與 CFTC 週部位不能混當成全市場當日資金流。</p></section>
    <section class="panel"><h2>固定訊號的後續結果</h2><p>臺指選擇權（TXO）；下表按本期訊號日分組，只評估在 ${esc(r.periodEnd)} 前走完觀察期的事件。${esc(e.notes)}</p>
      ${table(eventHeaders,eventRows(e.period,{tr}))}
      ${r.kind==='monthly'?`<h3>近 90 日策略檢討</h3><p>${esc(e.reviewStart)}～${esc(r.periodEnd)}；至少 30 筆可評估事件、20 個不同訊號日才列為可繼續檢驗。此門檻不等於證明策略有效。</p>${table(eventHeaders,eventRows(e.review,{tr}))}`:''}
      <p class="notice warning">這是權利金價格事件研究，包含事後補抓資料；沒有帳戶績效、前瞻獲利或自動改策略。成本敏感度是減 1／3 個百分點，尚未扣真實完整費稅與滑價。</p></section>
    <section class="panel"><h2>${r.kind==='daily'?'下一交易日':'下一期'}重點觀察清單</h2><p>訊號資料日 ${esc(e.through||'尚無')}，共 ${n(e.watchTotal)} 個規則事件，按放量倍數列前 20 筆；不依未來報酬排序。同契約可能同時符合多種規則。</p>
      ${table(['訊號日／到期別','權別／履約價','規則','前五日均量倍數','價格變化','未平倉變化'],e.watch.map(x=>tr([esc(x.date)+'<small>'+esc(x.month)+'</small>',cp(x.cp)+'／'+n(x.strike),esc(({volume:'放量',momentum:'價漲放量增倉',pullback:'價跌放量增倉'})[x.rule]||x.rule),n(x.volumeMultiple,2),pct(x.pricePct),signed(x.oiChange)])))}
      <p>下期核對外資淨部位是否延續、同一到期別籌碼是否移動，以及這批訊號的成交價是否足以追蹤；清單不是買賣推薦。</p></section>
    <section class="panel"><h2>個股選擇權價差覆蓋</h2><p>資料日 ${esc(s.date||'尚無')}，${n(s.stocks)} 檔標的、${n(s.pairs)} 組配對；${n(s.active)} 組至少一邊有成交，${n(s.priced)} 組可算收盤價差，${n(s.quoted)} 組有至少一個方向三邊盤末報價。</p><p>缺少同步報價、可成交數量與完整成本時，不能確認套利。<a href="#stock-options">查看個股選擇權與現股 →</a></p></section>
    <section class="panel derivative-evidence"><h2>資料完整度與來源</h2><p><a href="#market-history">檢查全市場歷史、日夜盤與期貨商全表缺日 →</a></p>${warning}${table(['來源','資料日／基準','本期已收錄／預期日數','狀態與缺漏'],r.sources.map(s=>tr([
      esc(s.label),esc(s.date||'無')+'<small>基準 '+esc(s.baseDate||'缺資料')+'</small>',n(s.days)+'／'+(s.expected===null?'依美國原頻率':n(s.expected)),esc(statuses[s.status]||s.status)+'<small>'+esc(s.missingDates.join('、')||s.note||'已核對')+'</small>'])))}
      <details><summary>來源連結與資料雜湊</summary>${r.sources.map(s=>`<p>${esc(s.label)} · ${/^https:\/\//.test(s.url||'')?'<a href="'+esc(s.url)+'" target="_blank" rel="noopener">官方來源</a>':''}<small><code>${esc(s.sha256||'無')}</code></small></p>`).join('')}<p>雜湊對應本報告使用的逐日欄位；歷史來源不宣稱當日已取得。選擇權研究版本 ${esc(e.version||'無')}。</p></details></section>
    <section class="panel source-list"><h2>方法、規則與修訂</h2><p>規則版本 ${esc(r.rules.version)}；${esc(r.revisionReason)}。</p><ul>${r.limitations.map(x=>'<li>'+esc(x)+'</li>').join('')}</ul></section>`;
}

export function createDerivativeReports(deps){
  const state={archived:false};let lastIndex;
  async function page(){
    const query=new URLSearchParams(location.hash.split('?')[1]||'');
    const kind=Object.hasOwn(kinds,query.get('kind'))?query.get('kind'):'daily';
    const index=await deps.load('derivative-reports').catch(()=>null);lastIndex=index;
    if(!index)return '<section class="panel"><h2>期貨與選擇權報告中心</h2><p class="notice warning">報告尚未生成，不能以空白資料判讀行情。</p></section>';
    const requested=query.get('report')||'',choices=reportChoices(index,kind,requested,state.archived);
    const check=index.checks?.[kind];
    const error=requested&&!choices.items.some(i=>i.id===requested)?'<p class="notice warning">指定報告不存在或格式不符，以下顯示可用版本。</p>':'';
    const controls=`<section class="panel"><div class="filters">${deps.select('dr-kind','報告週期',Object.keys(kinds),kind,k=>kinds[k])}${deps.select('dr-report','報告期間與版本',choices.items.map(i=>i.id),choices.chosen,id=>{const i=choices.items.find(x=>x.id===id);return i.periodStart+'～'+i.periodEnd+' · 第 '+i.revision+' 版'+(i.archiveState==='superseded'?'（封存）':'');})}<label><input type="checkbox" id="dr-archived" ${state.archived?'checked':''}>顯示修訂前版本</label></div><details><summary>更新排程與修訂規則</summary><p>${esc(index.schedule)}</p></details><p>最後檢查 ${esc(taipeiTime(check?.checkedAt))}（台北）；${check?.status==='retained'?'產生失敗，保留先前版本':'報告依實際資料日保存'}。</p>${check?.error?'<p class="notice warning">'+esc(check.error)+'</p>':''}${error}</section>`;
    if(!choices.chosen)return controls+'<p class="notice warning">本週期尚無可用報告。</p>';
    const report=await deps.load('derivative-reports/'+choices.chosen);
    return controls+renderDerivativeReport(report,deps);
  }
  function bind(rerender){
    document.getElementById('dr-kind')?.addEventListener('change',e=>{location.hash='derivative-report?kind='+encodeURIComponent(e.target.value);});
    document.getElementById('dr-report')?.addEventListener('change',e=>{
      const item=lastIndex?.items.find(i=>i.id===e.target.value);
      if(item&&validDerivativeReportId(item.id))location.hash='derivative-report?kind='+item.kind+'&report='+item.id;
    });
    document.getElementById('dr-archived')?.addEventListener('change',e=>{state.archived=e.target.checked;rerender();});
  }
  return {page,bind};
}
