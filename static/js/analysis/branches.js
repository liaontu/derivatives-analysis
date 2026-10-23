import {unlockKey,unseal} from './branch-crypto.js';

const $=id=>document.getElementById(id);
const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=v=>Number.isFinite(Number(v))?Number(v).toLocaleString('zh-TW',{maximumFractionDigits:2}):'—';
const base=document.body.dataset.branchBase||'https://raw.githubusercontent.com/liaontu/derivatives-analysis/main/data/branches/';
let index=null,unlock=null,snapshot=null,ticket=0,started=false;
const message=text=>{$('branch-status').textContent=text;};
const time=iso=>iso?new Date(iso).toLocaleString('zh-TW',{timeZone:'Asia/Taipei',hour12:false}):'尚未執行';

async function keyStore(action,value){
  const db=await new Promise((ok,bad)=>{
    const r=indexedDB.open('derivatives-personal-branches',1);
    r.onupgradeneeded=()=>r.result.createObjectStore('keys');
    r.onsuccess=()=>ok(r.result);r.onerror=()=>bad(r.error);
  });
  try{return await new Promise((ok,bad)=>{
    const tx=db.transaction('keys',action==='get'?'readonly':'readwrite');
    const store=tx.objectStore('keys');
    const r=action==='get'?store.get('unlock'):action==='put'?store.put(value,'unlock'):store.delete('unlock');
    tx.oncomplete=()=>ok(r.result);tx.onerror=()=>bad(tx.error);tx.onabort=()=>bad(tx.error);
  });}finally{db.close();}
}
async function json(path){
  const r=await fetch(base+path+'?v='+Date.now(),{cache:'no-store'});
  if(!r.ok)throw new Error('更新檔尚未取得，請稍後重試');
  return r.json();
}
function clear(){snapshot=null;$('branch-content').hidden=true;$('branch-rows').replaceChildren();}
function controls(){
  const stock=index?.stocks.find(s=>s.symbol===$('branch-stock').value);
  $('branch-date').replaceChildren(...(stock?.dates||[]).map(d=>new Option(d,d)));
  $('branch-empty').hidden=!!stock?.dates?.length;
  return stock;
}
function render(){
  if(!snapshot)return;
  const side=$('branch-side').value;
  const search=$('branch-search').value.trim().toLowerCase();
  const rows=snapshot.rows.filter(r=>r.side===side&&`${r.broker_code} ${r.broker_name}`.toLowerCase().includes(search));
  $('branch-rows').innerHTML=rows.map(r=>`<tr><td>${r.rank}</td><td><strong>${escape(r.broker_name)}</strong><small>${escape(r.broker_code)}</small></td><td class="num ${r.net_lots>0?'up':'down'}">${r.net_lots>0?'+':''}${fmt(r.net_lots)}</td><td class="num">${fmt(r.buy_lots)}</td><td class="num">${fmt(r.sell_lots)}</td><td class="num">${fmt(r.avg_buy_price)}</td><td class="num">${fmt(r.avg_sell_price)}</td></tr>`).join('')||'<tr><td colspan="7" class="empty">目前榜單沒有符合的分點；未上榜不代表零交易。</td></tr>';
  $('branch-table-title').textContent=`${snapshot.symbol} ${snapshot.stock_name} · ${side==='buy'?'買超':'賣超'}排行`;
  $('branch-data-date').textContent='資料日 '+snapshot.data_date;
  $('branch-source').href=`https://www.nstock.tw/${snapshot.symbol}?status=9`;
  $('branch-scope').textContent=`各保留買超、賣超前 15 名，非全部分點。${snapshot.includes_block_trades?'來源註明含鉅額交易。':''}未上榜不代表零交易，不以此推算完整多日累計。${snapshot.warnings?.length?' 部分張數有來源四捨五入差異，保留原值。':''}`;
  $('branch-content').hidden=false;
}
async function loadSnapshot(){
  const current=++ticket;clear();
  const symbol=$('branch-stock').value,day=$('branch-date').value;
  const stock=index?.stocks.find(s=>s.symbol===symbol);
  const status=`最近檢查：${time(stock?.checkedAt)}（台北）${stock?.status==='error'?' · 來源更新失敗，保留上次成功資料':''}`;
  if(!unlock){message(status+' · 請匯入個人解鎖檔');return;}
  if(!day){message(status+' · 此股票尚無成功資料');return;}
  try{
    message('正在解密個人分點資料…');
    const data=await unseal(await json(`${symbol}/${day}.json`),unlock);
    if(current!==ticket)return;
    if(data.symbol!==symbol||data.data_date!==day||!Array.isArray(data.rows)||data.complete_branch_coverage!==false)throw new Error('資料內容驗證失敗');
    snapshot=data;render();
    const stale=Date.now()-Date.parse(stock.dates[0]+'T00:00:00+08:00')>4*86400000;
    message(status+(stale?' · 最新資料已超過 4 天，請確認來源是否休市或延遲':''));
  }catch(e){if(current===ticket){clear();message(e.message==='這份解鎖檔不適用目前的資料'?e.message:'讀取或解密失敗，請確認解鎖檔並重新載入。');}}
}
export async function refreshBranches(){
  if(!started){
    started=true;
    try{unlock=await keyStore('get')||null;}catch{/* Browser storage is optional. */}
    $('branch-remember').checked=!!unlock;
    $('branch-lock').hidden=!unlock;
    $('branch-key').addEventListener('change',async e=>{
      const file=e.target.files[0];if(!file)return;
      try{
        if(file.size>16384)throw new Error('解鎖檔過大');
        const candidate=await unlockKey(JSON.parse(await file.text()));
        if(index && candidate.id!==index.keyId)throw new Error('這份解鎖檔不適用目前的資料');
        unlock=candidate;$('branch-lock').hidden=false;
        let storageError=false;
        try{await keyStore($('branch-remember').checked?'put':'delete',unlock);}catch{storageError=true;}
        await loadSnapshot();
        if(storageError)message($('branch-status').textContent+' · 此瀏覽器無法儲存解鎖狀態，下次請重新匯入');
      }catch(e){message(e.message||'無法讀取解鎖檔');}
      finally{e.target.value='';}
    });
    $('branch-lock').addEventListener('click',async()=>{
      ++ticket;unlock=null;clear();$('branch-lock').hidden=true;$('branch-remember').checked=false;message('正在移除此裝置的解鎖狀態…');
      try{await keyStore('delete');message('已鎖定並移除此裝置記住的解鎖狀態');}catch{message('已鎖定本頁；瀏覽器儲存空間無法存取，請清除本站資料以移除記住的解鎖狀態');}
    });
    $('branch-stock').addEventListener('change',()=>{controls();loadSnapshot();});
    $('branch-date').addEventListener('change',loadSnapshot);
    $('branch-side').addEventListener('change',render);
    $('branch-search').addEventListener('input',render);
    $('branch-reload').addEventListener('click',refreshBranches);
  }
  try{
    message('正在檢查每日更新狀態…');
    const next=await json('index.json');
    if(next.version!==1||!Array.isArray(next.stocks)||next.stocks.some(s=>!/^\d{4}$/.test(s.symbol)||!Array.isArray(s.dates)||s.dates.some(d=>!/^\d{4}-\d{2}-\d{2}$/.test(d))))throw new Error('更新索引格式不正確');
    index=next;
    const selected=$('branch-stock').value;
    $('branch-stock').replaceChildren(...index.stocks.map(s=>new Option(s.symbol+({'2330':' 台積電','2317':' 鴻海'}[s.symbol]||''),s.symbol)));
    if(index.stocks.some(s=>s.symbol===selected))$('branch-stock').value=selected;
    controls();await loadSnapshot();
  }catch{message('暫時無法取得更新狀態，請稍後按「重新讀取分點」重試。');}
}
