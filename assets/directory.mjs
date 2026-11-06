import {esc,fmt,finite,toCSV} from './model.mjs';
import {branchSourceUrl} from './branches.mjs';
import {matchedSpot, costPanel, bindCostPanel} from './warrant-costs.mjs';
const idOK=x=>/^[0-9]{5}[0-9A-Z]$/.test(x||'');
const fieldNames={listedDate:'上市／上櫃日期',exerciseStart:'履約開始日',lastTrade:'最後交易日',expiry:'履約截止／到期日',style:'履約方式',strike:'目前履約價（元）',ratio:'目前行使比例',originalStrike:'原始履約價（元）',reportedIssueK:'發行單位數量（仟單位，上市彙總）',initialIssueK:'初次發行（仟單位）',addedK:'累計增額（仟單位）',cancelledK:'累計註銷（仟單位）',kind:'發行類別',quoteMethod:'流動量提供者報價方式',settlementCode:'結算方式（官方代碼）',reset:'重設型（官方標記）',cap:'上限價格',floor:'下限價格'};
const no='—',value=x=>x==null||x===''?no:esc(x),n=x=>finite(x)?fmt(x,3):no;
const lookup='https://www.twse.com.tw/zh/products/securities/warrant/mops.html';
export function rankingState(q,item,idx){
  if(item && (item.underlying!==q.underlying || item.market!==q.market || item.name!==q.name))item=null;
  if(item?.status==='ok'&&idx?.status!=='retained'&&idx?.status!=='unavailable'&&item.date===q.date)return '同日排行';
  if(item)return '已追蹤・日期或狀態待核對';
  if(!idx||idx.status==='retained'||idx.status==='unavailable')return '排行索引待確認';
  if(q.volume===0)return '零成交・未收錄排行';
  return '未收錄排行';
}
const href=(u,c='')=>'#warrant-directory?underlying='+encodeURIComponent(u)+(c?'&code='+encodeURIComponent(c):'');
export function createDirectory({load,table,tr,empty}){
  let p=0,last='',filter='',rowsOut=[],underlying='';
  async function page(){
    const [ix,br,coverage]=await Promise.all([load('stock-warrant-directory').catch(()=>null),load('warrant-branches').catch(()=>null),load('stock-warrant-coverage').catch(()=>null)]);
    if(!ix?.items?.length)return empty('個股權證名錄尚未成功發布，請稍後再載入。');
    const args=new URLSearchParams(location.hash.split('?')[1]||'');
    const requested=args.get('code')||'',u=args.get('underlying')||'';
    const linkSearch='<label>標的股票代號、公司名稱或權證代號<input id="wi-input" type="search" placeholder="例如 2330 或 030001"></label><button id="wi-go">查詢</button><span role="status" id="wi-error"></span>';
    const members=new Set(ix.items.map(g=>g.code)), partial=ix.status!=='ok'?'本次名錄更新失敗，沿用上次成功的條款與行情。':'';
    const tracked=(br?.items||[]).filter(r=>members.has(r.underlying)&&['C','P'].includes(r.cp));
    const intro='<section class="panel"><h2>個股權證發行資料與分點覆蓋</h2><p>核對上市、上櫃公司名單後，收錄 '+n(ix.items.length)+' 個標的、'+n(ix.count)+' 檔認購／認售；不含 ETF、指數及無法對應公司名單的標的。發行補充表已核對 '+n(ix.issueMatches)+' 檔，並非每檔欄位完整。</p><p>行情日 '+esc(ix.quoteDates.join('、'))+'；上市發行彙總 '+esc(ix.sourceDates.listedIssues)+'、上櫃條款 '+esc(ix.sourceDates['tpex-terms'])+'。'+esc(partial)+'</p><p>富邦排行索引中可對應此類標的：'+(br?n(tracked.length):no)+' 檔。其餘可以逐檔查看收錄狀態及原始查詢入口；官方零成交與未取得分點均不當成分點淨額零。</p><div class="filters">'+linkSearch+'</div><p class="muted">來源：<a class="text-link" href="https://mopsfin.twse.com.tw/opendata/t187ap37_L.csv">上市發行表</a> · <a class="text-link" href="https://mopsfin.twse.com.tw/opendata/t187ap37_O.csv">上櫃發行日期表</a> · <a class="text-link" href="https://www.tpex.org.tw/zh-tw/derivative/warrant/info/issue.html">上櫃發行基本資料</a> · <a class="text-link" href="'+lookup+'">公開銷售說明書／發行公告查詢</a></p></section>';
    const coveragePanel='<section class="panel"><h2>實際分點覆蓋</h2><p>按市場與行情日統計。分點資料只有排行；未收錄不表示沒有交易。</p>'+
      (coverage?.current?.length ? ((coverage.status!=='ok'||coverage.rankIndexAt!==br?.updatedAt)?'<p class="status-bad">此統計沿用舊版或尚待與最新分點索引同步。</p>':'')+table(['行情日／市場','個股權證','同日排行','排行舊日／失敗','有成交未收錄','零成交未收錄','量未知未收錄'],coverage.current.map(v=>tr([value(v.date)+' · '+(v.market==='TWSE'?'上市':'上櫃'),n(v.total),n(v.ranked||0),n(v.otherDateOrFailed||0),n(v.tradedWithoutRank||0),n(v.zeroWithoutRank||0),n(v.unknownVolume||0)])))+'<a class="text-link" href="data/derivatives/stock-warrant-coverage.json" download>下載覆蓋歷史（最多 30 個行情日）</a>':empty('尚無可核實的分點覆蓋統計。'))+'</section>';
    let key=u;
    if(requested){
      if(!idOK(requested))return intro+empty('權證代碼格式不符。');
      key=(await load('warrant-code-index')).codes?.[requested];
      if(u&&u!==key)return intro+empty('權證與標的代碼不一致。');
    }else if(!key)key=members.has('2330')?'2330':ix.items[0].code;
    const group=ix.items.find(g=>g.code===key);
    if(!group)return intro+empty('這個代碼不在本次個股權證名錄內。');
    const payload=await load('stock-warrant-directory/'+key);
    if(!payload||payload.hash!==group.hash)return intro+empty('名錄與明細版本不同，請重新載入。');
    const indexed=new Map(tracked.map(r=>[r.code,r]));
    const rows=payload.rows.map(r=>{const i=indexed.get(r.code),item=i?.market===r.market&&i?.underlying===r.underlying&&i?.name===r.name?i:null;return {...r,rank:rankingState(r,item,br),rankDay:item?.date||null}});
    const cur=requested?rows.find(r=>r.code===requested):null;
    if(requested&&!cur)return intro+empty('權證不在這個標的的本次名錄內。');
    if(last!==key+'|'+requested){p=0;filter='';last=key+'|'+requested}
    rowsOut=rows.filter(r=>!filter||r.rank===filter);underlying=key;p=Math.min(p,Math.max(0,Math.ceil(rowsOut.length/30)-1));
    const counts=Object.fromEntries([...new Set(rows.map(r=>r.rank))].map(status=>[status,rows.filter(r=>r.rank===status).length]));
    let detail='';
    if(cur){
      const m=cur.market==='TWSE'?'上市':'上櫃';
      const quotes=await load('warrants-by-underlying/'+key).catch(()=>null);
      const calculator=costPanel(cur, matchedSpot(cur, quotes));
      detail='<section class="panel source-list"><h2>'+esc(cur.code)+' · '+esc(cur.name)+'</h2><p>'+m+' · '+(cur.cp==='C'?'認購':'認售')+' · '+esc(group.name)+'（'+esc(key)+'）</p><p>條款資料日 '+value(cur.termsDate)+'；發行補充表資料日 '+value(cur.issueDateOfReport)+'。</p>'+table(['發行欄位','官方值'],Object.entries(fieldNames).map(([k,v])=>tr([v,value(cur[k])])) )+'<p>— 表示此批次未提供或無法核對。掛牌日期不等於發行日；發行數量不等於市場流通餘額。發行人全名、原始發行價格、發行日、流通在外數量及逐檔說明書未由此批次核實，請至<a class="text-link" href="'+lookup+'">發行公告與說明書</a>核對。</p><p>'+esc(cur.rank)+' · 行情日 '+value(cur.date)+' · 排行資料日 '+value(cur.rankDay)+' · 成交 '+n(cur.volume)+' 張。</p><a class="text-link" href="#warrant-branches?code='+esc(cur.code)+'">站內分點狀態</a> · <a class="text-link" href="'+branchSourceUrl(cur.code)+'" target="_blank" rel="noopener">富邦逐檔原始查詢 ↗</a></section>';
      detail=calculator+detail;
    }
    return intro+detail+coveragePanel+'<section class="panel"><h2>'+esc(key)+' · '+esc(group.name)+'（'+n(rows.length)+' 檔）</h2><p>選擇權證代碼即可查發行條款、換算現股與試算費稅。買超／賣超各最多 15 名；未上榜不代表沒有交易。</p><div class="filters"><label>分點狀態<select id="wi-filter"><option value="">全部</option>'+Object.entries(counts).map(([k,v])=>'<option '+(filter===k?'selected':'')+' value="'+esc(k)+'">'+esc(k)+'（'+n(v)+'）</option>').join('')+'</select></label><button id="wi-export">下載本標的篩選結果 CSV</button></div>'+table(['權證／發行詳情','權別','掛牌日','到期日','履約價','成交量（張）','分點狀態','排行日'],rowsOut.slice(p*30,(p+1)*30).map(r=>tr(['<a class="text-link" href="'+href(key,r.code)+'">'+esc(r.code)+' · '+esc(r.name)+'</a>',r.cp==='C'?'認購':'認售',value(r.listedDate),value(r.expiry),n(r.strike),n(r.volume),esc(r.rank),value(r.rankDay)])))+'<div class="pagination"><span>第 '+(p+1)+' / '+Math.max(1,Math.ceil(rowsOut.length/30))+' 頁</span><button id="wi-prev" '+(!p?'disabled':'')+'>上一頁</button><button id="wi-next" '+((p+1)*30>=rowsOut.length?'disabled':'')+'>下一頁</button></div></section>';
  }
  function bind(render){
    bindCostPanel(table,tr);
    const el=id=>document.getElementById(id);
    const find=async()=>{
      const q=el('wi-input').value.trim(),ix=await load('stock-warrant-directory');
      const hits=ix.items.filter(g=>g.code===q||g.name===q);
      if(hits.length===1){location.hash=href(hits[0].code);return}
      if(idOK(q)){const key=(await load('warrant-code-index')).codes?.[q];if(ix.items.some(g=>g.code===key)){location.hash=href(key,q);return}}
      el('wi-error').textContent='不在本次個股權證名錄，請輸入完整代碼或公司名稱。';
    };
    el('wi-go')?.addEventListener('click',find);el('wi-input')?.addEventListener('keydown',e=>{if(e.key==='Enter')find()});
    el('wi-filter')?.addEventListener('change',e=>{filter=e.target.value;p=0;render()});
    el('wi-prev')?.addEventListener('click',()=>{p--;render()});el('wi-next')?.addEventListener('click',()=>{p++;render()});
    el('wi-export')?.addEventListener('click',()=>{const cols=[['code','權證'],['name','名稱'],['date','行情日'],['volume','成交量（張）'],['termsDate','條款日'],['issueDateOfReport','發行表資料日'],...Object.entries(fieldNames),['rank','分點狀態'],['rankDay','排行日']];const url=URL.createObjectURL(new Blob([toCSV(rowsOut,cols)],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='warrant-issuance-'+underlying+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)});
  }
  return {page,bind};
}

