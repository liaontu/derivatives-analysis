import {esc,taipeiTime} from './model.mjs';
const names={collect:'官方行情',directory:'發行名錄',option_history:'選擇權歷史',stock_options:'個股選擇權',reports:'量價／籌碼日報',journal:'整合日報',relationship:'近月關聯',signal_backtest:'分點線索回測',branches:'分點排行',coverage:'覆蓋檢查',series:'週月報',lab:'模擬交易',lab_reports:'模擬觀察報告',focus:'重點清單',spreads:'價差觀察'};
export function latestModules(receipts) {
  const result={};
  for(const receipt of receipts.filter(Boolean)) for(const [key,row] of Object.entries(receipt.modules||{})) {
    if(!result[key] || row.checkedAt>result[key].checkedAt) result[key]={...row,runId:receipt.runId};
  }
  return result;
}
export async function pipelineHealth({load,table,tr}) {
  const receipts=await Promise.all(['check-market','check-branches','check-series-reports'].map(k=>load(k).catch(()=>null)));
  const rows=latestModules(receipts);
  const issues=Object.values(rows).filter(r=>r.outcome!=='success'||['retained','unavailable','failure','failed','error'].includes(r.status));
  const publication=receipts[2]?.publication;
  if(publication && publication.status!=='verified') issues.push({status:publication.status});
  return `<section class="panel pipeline-health"><h2>分析模組實際更新結果</h2><p class="${issues.length?'notice warning':'muted'}">${Object.keys(rows).length?issues.length?`${issues.length} 個步驟未確認成功，已保留既有資料；請查看原因。`:'最近記錄的分析步驟已完成；資料日仍以各報告為準。':'新版逐項檢查紀錄尚未產生，不能據此宣稱全部分析已更新。'}</p>
  <details ${issues.length?'open':''}><summary>逐項查看資料日、檢查時間與失敗證據</summary>${table(['模組','資料日','檢查時間（台北）','結果／原因'],Object.entries(rows).map(([key,r])=>tr([esc(names[key]||key),esc(r.dataDate||'見報告'),esc(taipeiTime(r.checkedAt)),`${esc(r.outcome)}／${esc(r.status)}${r.reason?'<small>'+esc(r.reason)+'</small>':''}${r.evidence?'<small><a href="data/derivatives/'+esc(r.evidence)+'">失敗快照</a></small>':''}<small>上次成功：${esc(taipeiTime(r.lastSuccessAt))}</small><a href="https://github.com/liaontu/derivatives-analysis/actions/runs/${esc(r.runId)}" target="_blank" rel="noopener">執行紀錄 ↗</a>`])))}</details>
  ${publication?`<p>週月報線上內容驗證：${esc(publication.status)}；${esc(taipeiTime(publication.checkedAt))}。${esc(publication.error||'')}<small>目標提交 <code>${esc(publication.commit)}</code> · 已驗證 ${publication.verifiedFiles?.length||0} 個檔案</small></p>`:''}</section>`;
}
