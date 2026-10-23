import {spawnSync} from 'node:child_process';
import {mkdir,readFile,writeFile,rename,rm} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {pathToFileURL} from 'node:url';
import {seal,keyId} from '../../static/js/analysis/branch-crypto.js';

async function atomic(path,value) {
  await mkdir(dirname(path),{recursive:true});
  await writeFile(path+'.tmp',JSON.stringify(value));
  await rename(path+'.tmp',path);
}
async function read(path,fallback) {
  try{return JSON.parse(await readFile(path,'utf8'));}
  catch(e){if(e.code==='ENOENT')return fallback;throw e;}
}

export async function update(results,{root,publicKey,now=new Date(),retention=60}) {
  const path=resolve(root,'data/branches/index.json');
  const previous=await read(path,{version:1,stocks:[]});
  const id=await keyId(publicKey);
  if(previous.keyId && previous.keyId!==id)throw new Error('Explicit migration required to rotate keys');
  const stocks=[];
  for(const result of results){
    const symbol=result.symbol;
    if(!/^\d{4}$/.test(symbol))throw new Error('Invalid stock code');
    const old=previous.stocks.find(s=>s.symbol===symbol)||{symbol,dates:[]};
    let error=result.error;
    const p=result.payload;
    if(!error){
      const age=(now-new Date(p?.data_date+'T00:00:00+08:00'))/86400000;
      if(p?.symbol!==symbol || !/^\d{4}-\d{2}-\d{2}$/.test(p?.data_date||'') || !Number.isFinite(age) || age<0 || age>10 || (old.dates[0] && p.data_date<old.dates[0]) || !p.rows?.length || p.complete_branch_coverage!==false || p.missing_branch_means_zero!==false){
        error='invalid_or_old_source_date';
      }
    }
    if(error){stocks.push({...old,status:'error',error,checkedAt:now.toISOString()});continue;}
    const payload={...p,processed_at:now.toISOString(),input_mode:'scheduled_http',usage:'personal_encrypted'};
    const envelope=await seal(payload,publicKey);
    await atomic(resolve(root,`data/branches/${symbol}/${p.data_date}.json`),envelope);
    const all=[...new Set([p.data_date,...old.dates])].sort().reverse();
    const dates=all.slice(0,retention);
    stocks.push({symbol,dates,status:'ok',checkedAt:now.toISOString(),lastSuccessAt:now.toISOString()});
    for(const day of all.slice(retention)){
      if(/^\d{4}-\d{2}-\d{2}$/.test(day))await rm(resolve(root,`data/branches/${symbol}/${day}.json`),{force:true});
    }
  }
  const index={version:1,keyId:id,checkedAt:now.toISOString(),schedule:'每天 19:43（台北時間），平台可能延遲',retention,stocks};
  await atomic(path,index);
  return index;
}

async function main(){
  const root=process.cwd();
  const config=await read(resolve(root,'config/branch-watchlist.json'));
  const publicKey=await read(resolve(root,'config/branch-public-key.json'));
  if(!Array.isArray(config.symbols)||config.retention!==60)throw new Error('Invalid watchlist configuration');
  const child=spawnSync(process.env.PYTHON||'python3',['automation/branches/collect.py','config/branch-watchlist.json'],{encoding:'utf8',timeout:270000,maxBuffer:2_000_000});
  let results;
  if(child.status!==0){results=config.symbols.map(symbol=>({symbol,error:'collector_failed'}));}
  else{
    try{results=JSON.parse(child.stdout);}
    catch{results=config.symbols.map(symbol=>({symbol,error:'collector_invalid_response'}));}
  }
  if(JSON.stringify(results.map(r=>r.symbol))!==JSON.stringify(config.symbols))throw new Error('Unexpected collector symbols');
  const index=await update(results,{root,publicKey,retention:config.retention});
  console.log(JSON.stringify({checkedAt:index.checkedAt,stocks:index.stocks.map(s=>({symbol:s.symbol,status:s.status,date:s.dates[0]||null}))}));
  if(index.stocks.some(s=>s.status!=='ok'))process.exitCode=1;
}
if(process.argv[1] && import.meta.url===pathToFileURL(resolve(process.argv[1])).href)main().catch(()=>{console.error('Branch update failed; previous ciphertext retained.');process.exitCode=1;});
