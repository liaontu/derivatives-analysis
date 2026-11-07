import {warrantUnderlyings} from './model.mjs';

export async function loadWarrants(load, {underlying = '', code = '', all = false, preview = false, onProgress = () => {}} = {}) {
  const index = await load('warrant-catalog').catch(() => null);
  if (!Array.isArray(index?.items)) {
    if (preview) return {...await load('warrants-preview'), choices: [], underlying: '', preview: true};
    const data = await load('warrants');
    return {...data, choices: warrantUnderlyings(data.rows), underlying};
  }
  const choices = index.items;
  if (preview) return {...await load('warrants-preview'), choices, underlying: '', preview: true};
  if (all) {
    if(index.schemaVersion!==3)return {...await load('warrants'),choices,underlying:''};
    // Explicit full-market requests only; limit concurrent shard downloads.
    const parts=new Array(choices.length); let next=0,completed=0;
    onProgress(0,choices.length);
    await Promise.all(Array.from({length:24},async()=>{
      while(next<choices.length) {
        const n=next++, item=choices[n];
        if(!/^[A-Za-z0-9_-]{1,24}$/.test(item.code)) throw Error('Invalid warrant shard');
        const part=await load(`warrants-by-underlying/${item.code}`);
        if(!Array.isArray(part.rows)||part.rows.length!==item.count||part.rows.some(r=>r.underlying!==item.code)) throw Error('Incomplete warrant shard');
        parts[n]=part.rows;
        onProgress(++completed,choices.length);
      }
    }));
    const rows=parts.flat();
    if(rows.length!==index.count||new Set(rows.map(r=>`${r.market}:${r.code}`)).size!==rows.length)throw Error('Warrant catalogue count or identity mismatch');
    return {rows,choices,underlying:''};
  }
  if (code) underlying = (await load('warrant-code-index')).codes?.[code] || '';
  else if (!underlying) underlying = choices.find(r => r.code === '2330')?.code || choices[0]?.code || '';
  if (!/^[A-Za-z0-9_-]{1,24}$/.test(underlying) || !choices.some(r => r.code === underlying)) return {rows: [], choices, underlying};
  const data = await load(`warrants-by-underlying/${underlying}`);
  return {...data, choices, underlying};
}
