// Native disclosures keep optional controls and methods below the primary data.
const remembered=new Map();
let listening=false;
export function compactMobile(root,route) {
  const mobile=matchMedia('(max-width: 680px)'); let serial=0;
  if(!listening){listening=true;mobile.addEventListener('change',e=>{
    document.querySelectorAll('details[data-mobile-fold]').forEach(d=>{d.open=!e.matches||remembered.get(d.dataset.mobileFold)===true;});
  });}
  const fold=(node,label)=>{
    if(node.closest('details') || node.closest('.table-wrap')) return;
    const key=route+':'+serial++, details=document.createElement('details'), summary=document.createElement('summary');
    details.className='mobile-disclosure';details.dataset.mobileFold=key;
    summary.textContent=label;details.append(summary);node.before(details);details.append(node);
    details.open=!mobile.matches || remembered.get(key)===true;
    details.addEventListener('toggle',()=>{if(mobile.matches)remembered.set(key,details.open);});
  };
  root.querySelectorAll('.mobile-secondary').forEach(node=>fold(node,node.dataset.mobileLabel||'其他設定與觀察'));
  root.querySelectorAll('.watch-charts').forEach(node=>fold(node,'價格與成交量趨勢圖'));
  root.querySelectorAll('.filters').forEach(node=>{
    const chosen=[...node.querySelectorAll('select')].slice(0,2).map(s=>s.selectedOptions[0]?.textContent).filter(Boolean).join(' · ');
    fold(node,'篩選與排序'+(chosen?' · '+chosen:''));
  });
  root.querySelectorAll('.source-list,.journal-method').forEach(node=>fold(node,'方法、來源與限制'));
  root.querySelectorAll('.report-evidence').forEach(node=>{
    const alert=node.querySelector('.notice.warning');
    if(alert)node.before(alert.cloneNode(true));
    fold(node,'資料範圍、補抓來源與有效樣本明細');
  });
  root.querySelectorAll('p').forEach(node=>{
    if(!node.classList.contains('notice') && node.textContent.length>60 && !/資料日|資料至|資料日期|啟用時間/.test(node.textContent))fold(node,'閱讀說明與計算條件');
  });
  root.querySelectorAll('ul,ol').forEach(node=>{
    if(node.textContent.length>150 && !node.closest('nav')) {
      const heading=node.previousElementSibling;
      fold(node,heading?.matches('h3,h4')?heading.textContent:'重點解讀與條件');
    }
  });
  const first=root.querySelector('.table-wrap,.journal-grid');
  if(first)root.querySelectorAll('.chart').forEach(node=>{
    if(node.compareDocumentPosition(first)&Node.DOCUMENT_POSITION_FOLLOWING){
      const panel=node.closest('.panel');
      fold(panel&&!panel.querySelector('.table-wrap,.metrics,.filters')?panel:node,'查看趨勢圖');
    }
  });
}
