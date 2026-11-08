import {esc,fmt,finite,taipeiTime} from './model.mjs';

const n=(v,d=0)=>finite(v)?fmt(v,d):'—';
const pct=v=>finite(v)?(v>0?'+':'')+fmt(v,2)+'%':'—';
const names={volume:'放量',momentum:'價漲放量增倉',pullback:'價跌放量增倉'};
const segments={development:'開發',evaluation:'評估',purged:'分割隔離'};
const reasons={'traded':'有成交','no-trade':'當日無成交','suspended':'官方停牌','not-yet-listed':'尚未上市櫃','source-row-missing':'來源未列行情','traded-close-missing':'有量但缺收盤'};
export function txoCost({entry,exit,days,commission,exerciseFee,slippagePct,fundingPct,settlementPrice=null}){
  if(![entry,exit,days,commission,exerciseFee,slippagePct,fundingPct].every(finite)||entry<=0||[exit,days,commission,exerciseFee,slippagePct,fundingPct].some(v=>v<0))return null;
  if(settlementPrice!==null&&(!finite(settlementPrice)||settlementPrice<=0))return null;
  const expiry=settlementPrice!==null,exercised=expiry&&exit>0;
  const tax=Math.round(entry*50*.001)+Math.round(exercised?settlementPrice*50*.00002:expiry?0:exit*50*.001);
  const fees=commission+(exercised?exerciseFee:expiry?0:commission);
  const slip=(entry+(expiry?0:exit))*50*slippagePct/100,funding=entry*50*fundingPct/100*days/365;
  const total=tax+fees+slip+funding,pnl=(exit-entry)*50-total;
  return {tax,fees,slip,funding,total,pnl,netPct:pnl/(entry*50)*100};
}

export function createMarketCompletion({load,table,tr,select,metric}){
  let catalog=[],state={code:'TWSE-2330',future:'',error:''},qualityItem=null;
  async function page(){
    const index=await load('market-completion');catalog=index.stocks;
    const query=new URLSearchParams(location.hash.split('?')[1]||'');
    const requested=query.get('stock')||state.code;
    const item=catalog.find(r=>r.market+'-'+r.code===requested);
    if(!item)return `<section class="panel"><h2>查無這個上市櫃股票代碼</h2><p>${esc(requested)} 不在目前官方名錄；不改查其他股票。</p><a href="#market-completion?stock=TWSE-2330">返回台積電觀察</a></section>`;
    state.code=requested;
    const [stock,refs]=await Promise.all([load(item.path.replace(/\.json$/,'')),load(index.references.path.replace(/\.json$/,''))]);
    const quality=await load('warrant-liquidity').catch(()=>null);
    qualityItem=quality?.items?.find(r=>r.code===item.code)||null;
    const qualityPanel=`<section class="panel"><h2>權證流動性與隱含波動率｜官方免費資料</h2>${quality?.summary?`<p>資料日 ${esc(quality.date)}；來源實際回傳 ${n(quality.summary.rows)} 檔，其中 ${n(quality.matched)} 檔通過同日代號、名稱、履約價及行使比例核對。已完成 ${n(Object.keys(quality.sources||{}).length)}／${n(quality.issuerCount)} 家發行人查詢。個股權證配對 ${n(quality.individualStocks?.matched)}／${n(quality.individualStocks?.catalogue)} 檔。${quality.status!=='ok'?'部分收集尚未成功；缺項保留空值。':''}</p><div class="metrics">${metric('有買賣價差比',n(quality.summary.withSpread),'來源回傳範圍')}${metric('有雙邊委託量',n(quality.summary.twoSidedVolume),'取樣量；非保證成交')}${metric('有雙邊隱含波動率',n(quality.summary.twoSidedIv),'官方計算值')}${metric('本股已配對權證',n(qualityItem?.rows||0),esc(item.code+' '+item.name))}</div>`:'<p>官方報價品質資料尚未收集成功。</p>'}<p>未配對的個股權證：特殊牛熊證 ${n(quality?.individualStocks?.specialStructureMissing||0)} 檔、標的停牌 ${n(quality?.individualStocks?.underlyingSuspendedMissing||0)} 檔；缺報價不填零。</p><p class="notice">近一個月 IV 是官方彙總，不能代替過去每一天的報價。每日原始值從實際保存日開始累積；不是同步三邊報價或完整訂單簿。</p>${qualityItem?'<button id="mc-quality-load" type="button">載入本股權證報價品質</button><div id="mc-quality-result" aria-live="polite"></div>':''}<details><summary>資料限制與來源</summary><ul>${(quality?.limitations||[]).map(t=>'<li>'+esc(t)+'</li>').join('')}</ul><a href="https://warrants.twse.com.tw/">證交所權證資訊揭露平台</a> · <a href="data/derivatives/warrant-liquidity.json" download>下載涵蓋與來源雜湊</a></details></section>`;
    const rows=stock.rows.map(r=>Object.fromEntries(stock.fields.map((f,i)=>[f,r[i]])));
    const futures=stock.futures.map(r=>Object.fromEntries(stock.futureFields.map((f,i)=>[f,r[i]])));
    const choices=[...new Set(futures.map(r=>r.contract+'/'+r.month))].sort();
    if(!choices.includes(state.future))state.future=choices.find(v=>v.endsWith(index.end.slice(0,7).replace('-','')))||choices[0]||'';
    const final=rows.at(-1)||{},cov=index.coverage.filter(r=>r.date===index.end);
    const settlements=refs.settlements.filter(r=>r.code===item.code||(r.contract==='TXO'&&r.date>=index.start));
    const adjustments=refs.contractAdjustments.filter(r=>r.code===item.code);
    const terms=refs.adjustedContracts.filter(r=>r.code===item.code);
    const research=index.research.summary.filter(r=>r.segment!=='purged');
    const all=index.research.summary,signals=all.reduce((s,r)=>s+r.signals,0),evaluated=all.reduce((s,r)=>s+r.evaluated,0),settled=all.reduce((s,r)=>s+r.settled,0);
    const field=(key,label,value,min=0)=>`<label>${label}<input name="${key}" type="number" step="any" min="${min}" value="${value}" required></label>`;
    return `<section class="panel"><h2>免費來源補齊與逐檔核對</h2><p>${esc(index.start)}～${esc(index.end)}；最後檢查 ${esc(taipeiTime(index.checkedAt))}（台北）。${esc(index.scope)}</p>
      ${index.status!=='ok'?'<p class="notice warning">部分來源收集或核對失敗，舊資料仍可查；請看下方缺漏。</p>':''}
      <div class="metrics">${metric('普通股名錄',n(index.summary.stocks),'上市＋上櫃；不含 ETF、興櫃')}${metric('個股期貨標的',n(index.summary.futureUnderlyings),n(index.summary.futureContracts)+' 個標準商品，大小型分列')}${metric('最後結算紀錄',n(index.summary.settlements),'指數／個股選擇權自 5 月，個股期貨自 10 月')}${metric('公司行動參考',n(index.summary.exRights),'含 '+n(index.summary.capitalActions||0)+' 件減資／面額變更')}</div>
      ${table(['最新日市場','名錄','行情列','有成交價','法人列'],cov.map(r=>tr([esc(r.market),n(r.universe),n(r.quoteRows),n(r.priced),n(r.institutional)])))}
      <p>報表完整取得不等於每檔有交易或有法人列。缺列留白；未把未列入報表的個股當作零買賣超。</p>
      ${table(['最新日市場','缺成交價原因','代碼'],cov.flatMap(r=>Object.entries(r.priceReasons||{}).map(([key,codes])=>tr([esc(r.market),esc(reasons[key]||key)+' '+n(codes.length)+' 檔',esc(codes.join('、'))]))))}
      <details><summary>每日涵蓋與缺漏代碼</summary>${table(['日期','市場','行情／名錄','法人／名錄','缺行情代碼','缺法人代碼'],index.coverage.slice().reverse().map(r=>tr([esc(r.date),esc(r.market),n(r.quoteRows)+'／'+n(r.universe),n(r.institutional)+'／'+n(r.universe),esc(r.missingQuoteCodes.join('、')||'無'),esc(r.missingInstitutionalCodes.join('、')||'無')])))}${index.errors?.map(e=>'<p>'+esc(e.date||'')+' '+esc(e.source)+': '+esc(e.error)+'</p>').join('')||''}</details></section>
      <section class="panel"><h2>${esc(item.code+' '+item.name)}｜現股、法人與期貨</h2>
      <form id="mc-stock-form" class="filters"><label>股票（市場－代碼）<input id="mc-stock" list="mc-stocks" value="${esc(state.code)}" required><datalist id="mc-stocks">${catalog.map(r=>`<option value="${esc(r.market+'-'+r.code)}">${esc(r.name)}</option>`).join('')}</datalist></label><button type="submit">查詢</button></form><p aria-live="polite" id="mc-error">${esc(state.error)}</p>
      <div class="metrics">${metric('現股收盤',n(final.close,2),esc(final.date))}${metric('日漲跌',pct(final.returnPct),'與前一交易日成交價比較')}${metric('除權息參考調整',pct(final.referenceAdjustedPct),'價差連續化；非含息總報酬')}</div>
      ${table(['日期','收盤','行情狀態','漲跌','參考調整漲跌','成交股數','外資淨買股','投信淨買股','自營淨買股','參考調整'],rows.slice().reverse().map(r=>tr([esc(r.date),n(r.close,2),esc(reasons[r.priceStatus]||'未分類'),pct(r.returnPct),pct(r.referenceAdjustedPct),n(r.volume),n(r.foreign),n(r.trust),n(r.dealer),esc(r.referenceAction||'—')])))}
      <p>法人單位為股；買賣超不等於持倉或資金流入金額。參考調整使用官方除權息前價與參考價；前價不符或其他公司行動未核對時留白。</p>
      <h3>個股期貨同月份價量</h3><p>${esc(index.futureScope)}</p>${choices.length?select('mc-future','商品／到期月份',choices,state.future):'<p>此股票沒有收錄可對照的標準個股期貨商品。</p>'}
      ${table(['日期','期貨成交','漲跌','量（口）','未平倉（口）','現期收盤差','收盤差／現股'],futures.filter(r=>r.contract+'/'+r.month===state.future).reverse().map(r=>tr([esc(r.date),n(r.close,2),pct(r.returnPct),n(r.volume),n(r.oi),n(r.closingBasis,2),pct(r.basisPct)])))}
      <p><a href="data/derivatives/${esc(item.path)}" download>下載本股完整逐日資料</a> · <a href="#stock-options">個股選擇權價差</a> · <a href="#derivative-report?kind=period">三市場完整報告</a></p></section>
      ${qualityPanel}<section class="panel"><h2>最後結算、公司行動與契約調整</h2><p>下表列本股與 10 月起 TXO 的已公告最後結算。調整型契約顯示原始條款日期，不能把最新條款套回過去。減資恢復日漲跌相對官方參考價，與現金股利、退還股款及換股後的總報酬不同。</p>
      ${table(['到期日','類型／代碼','到期別','最後結算價','約定標的物價值'],settlements.slice().reverse().map(r=>tr([esc(r.date),esc(r.kind+' '+r.contract),esc(r.month),n(r.price,4),n(r.underlyingValue,2)])))}
      <details><summary>本股公司行動與調整型契約</summary>${table(['生效日','商品前綴','事件','現金／股','配股'],adjustments.map(r=>tr([esc(r.date)+(r.date>index.end?'（已公告未生效）':''),esc(r.prefix),esc(r.action),n(r.cash,4),n(r.shares,4)])))}
      ${table(['條款資料日','代碼','股數','現金','認購權價值','月份'],terms.map(r=>tr([esc(r.date),esc(r.contract),n(r.shares),n(r.cash,2),n(r.rights,2),esc(r.months)])))}
      ${table(['生效日','前收盤','參考價','事件'],stock.actions.map(r=>tr([esc(r.date),n(r.before,2),n(r.reference,2),esc(r.action)])))}${table(['停止買賣日','恢復／資料日','換股率','退還股款／股','原因'],(stock.tradingStates||[]).map(r=>tr([esc(r.stopDate||'官方未列'),esc(r.date||r.asOf),n(r.newShareRatio,8),n(r.cashReturned,6),esc(r.action||r.reason)])))}</details></section>
      <section class="panel"><h2>到期結算研究 v2｜TXO</h2><p>既有固定訊號 ${n(signals)} 筆，${n(evaluated)} 筆可評估，其中 ${n(settled)} 筆使用到期內含價值。缺進場價仍排除。開發／評估分割 ${esc(index.research.splitDate)}；分割隔離區不併入下表。</p>
      <p class="notice">逐筆官方行情核對：${Object.entries(index.research.gapAudit?.counts||{}).map(([key,count])=>esc(reasons[key]||key)+' '+n(count)+' 筆').join('；')||'尚無缺價稽核'}。無成交不代表漏抓；買賣報價與每日結算參考價不替代實際進出場成交價。</p>
      ${table(['規則','C/P','區段','可評估／訊號','到期結算','示例成本後中位數','較高成本後中位數'],research.map(r=>tr([esc(names[r.rule]||r.rule),esc(r.cp),esc(segments[r.segment]||r.segment),n(r.evaluated)+'／'+n(r.signals),n(r.settled),pct(r.scenarios.base.median),pct(r.scenarios.stress.median)])))}
      <p>v1 結果保留；v2 改變退出規則，不能混作同一策略改善。各規則樣本重疊，不是組合帳戶績效；評估區段仍是事後重建，非前瞻實盤。</p><p>示例：每口手續費／價內履約費各 20 元、滑價 0.5%、年資金成本 2%；較高成本：各 50 元、滑價 1%、年資金成本 5%。稅率按官方規則，費用是假設；報酬分母為進場權利金，可低於 −100%。</p>
      <details><summary>完整規則與排除數</summary><ul>${(index.research.rules||[]).map(r=>'<li>'+esc(r)+'</li>').join('')}</ul>${table(['規則','C/P','區段','狀態計數'],all.map(r=>tr([esc(names[r.rule]),esc(r.cp),esc(segments[r.segment]),esc(JSON.stringify(r.statusCounts))])))}</details>
      <p><a href="data/derivatives/${esc(index.research.path)}" download>下載完整 v2 事件與費稅明細</a> · <a href="#option-history">查看原 v1 研究</a></p></section>
      <section class="panel"><h2>一口標準 TXO 買進的費稅情境</h2><p>每點 50 元；以下預填為計算示例，可自行修改。到期時，退出權利金填內含價值；價外到期填 0。其他商品或調整型契約不套用此倍率。</p>
      <form id="mc-cost"><div class="filters">${field('entry','進場權利金（點）',100,.01)}${field('exit','退出權利金／內含價值（點）',120)}${field('days','持有日曆天數',5)}${field('commission','每邊券商費（元）',20)}${field('exerciseFee','價內履約費（元）',20)}${field('slippagePct','成交滑價（權利金 %）',.5)}${field('fundingPct','年資金成本（%）',2)}
      <label>退出方式<select name="basis"><option value="trade">成交退出</option><option value="expiry">到期結算</option></select></label>${field('settlementPrice','到期時最後結算指數',40000,.01)}</div><p class="notice" id="mc-cost-result" aria-live="polite"></p></form>
      <p>成交權利金稅 0.1%；價內到期按最後結算指數×50×0.002% 課稅並四捨五入至元，價外到期不收履約稅費。每邊券商費視為含交易所內部費用的總報價，不再重複加下表費用。沒有真實券商費率與成交深度，結果不代表可套利。</p>
      <details><summary>官方交易所費表（券商收費另議）</summary>${table(['商品','經手費','結算費','交割費','期貨交易稅率'],refs.officialFees.map(r=>tr(['商品','交易經手費','結算手續費','交割手續費','期貨交易稅比率'].map(k=>esc(r[k])))))}</details></section>
      <section class="panel"><h2>仍有限制的資料</h2>${table(['項目','狀態','影響'],index.limitations.map(r=>tr([esc(r.item),esc(r.status),esc(r.effect)])))}
      <p><a href="data/derivatives/market-completion.json" download>下載逐日涵蓋與來源雜湊</a> · <a href="data/derivatives/market-supplement.json" download>收集來源與失敗證據</a> · <a href="https://www.taifex.com.tw/cht/4/feeSchedules">期交所費稅規則</a> · <a href="https://www.taifex.com.tw/cht/9/optionsQA">選擇權結算說明</a></p></section>`;
  }
  function bind(rerender){
    document.getElementById('mc-quality-load')?.addEventListener('click',async e=>{
      const button=e.target,item=qualityItem;button.disabled=true;button.textContent='載入中…';
      try{const data=await load(item.path.replace(/\.json$/,''));if(!button.isConnected)return;
        const box=document.getElementById('mc-quality-result');box.innerHTML='<label>篩選權證代碼或名稱 <input id="mc-quality-filter" placeholder="例如 030156"></label><div id="mc-quality-table"></div><p><a href="data/derivatives/'+esc(item.path)+'" download>下載本股全部報價品質 JSON</a></p>';
        const draw=()=>{const value=document.getElementById('mc-quality-filter').value.trim();const rows=data.rows.filter(r=>!value||r.code.includes(value)||r.name.includes(value)).sort((a,b)=>(b.volumeUnits||0)-(a.volumeUnits||0));document.getElementById('mc-quality-table').innerHTML='<p>'+n(rows.length)+' 檔，依當日成交量顯示前 60 檔；資料日 '+esc(data.date)+'</p>'+table(['權證','價差比','委買量','委賣量','委買 IV','委賣 IV','近月買 IV','近月賣 IV'],rows.slice(0,60).map(r=>tr([esc(r.code+' '+r.name),pct(r.spreadPct),n(r.bidVolume),n(r.askVolume),pct(r.bidIv),pct(r.askIv),pct(r.periodBidIv),pct(r.periodAskIv)])));};document.getElementById('mc-quality-filter').addEventListener('input',draw);draw();button.remove();
      }catch(error){if(button.isConnected){button.disabled=false;button.textContent='載入失敗，重試';}}
    });
    document.getElementById('mc-stock-form')?.addEventListener('submit',e=>{e.preventDefault();const value=document.getElementById('mc-stock').value.trim().toUpperCase();const found=catalog.find(r=>r.market+'-'+r.code===value||r.code===value||r.name===value);if(!found){document.getElementById('mc-error').textContent='查無此股票，請使用名錄中的市場與代碼。';return;}state.future='';location.hash='market-completion?stock='+encodeURIComponent(found.market+'-'+found.code);});
    document.getElementById('mc-future')?.addEventListener('change',e=>{state.future=e.target.value;rerender();});
    const form=document.getElementById('mc-cost');
    const update=()=>{const values=Object.fromEntries(new FormData(form));const args=Object.fromEntries(Object.entries(values).filter(([k])=>k!=='basis').map(([k,v])=>[k,v.trim()===''?null:Number(v)]));if(values.basis!=='expiry')args.settlementPrice=null;const r=txoCost(args);document.getElementById('mc-cost-result').textContent=r?`稅 ${n(r.tax,0)} 元＋手續費 ${n(r.fees,0)} 元＋滑價 ${n(r.slip,2)} 元＋資金成本 ${n(r.funding,2)} 元＝總成本 ${n(r.total,2)} 元；淨損益 ${n(r.pnl,2)} 元（${pct(r.netPct)}）。`:'請填入有效、非負的費用與價格；進場權利金必須大於 0。';};
    if(form){form.addEventListener('submit',e=>e.preventDefault());form.addEventListener('input',update);form.addEventListener('change',update);update();}
  }
  return {page,bind};
}
