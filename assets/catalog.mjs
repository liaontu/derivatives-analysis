import {warrantUnderlyings} from './model.mjs';

export async function loadWarrants(load, {underlying = '', code = '', all = false} = {}) {
  const index = await load('warrant-catalog').catch(() => null);
  if (!Array.isArray(index?.items)) {
    const data = await load('warrants');
    return {...data, choices: warrantUnderlyings(data.rows), underlying};
  }
  const choices = index.items;
  if (all) return {...await load('warrants'), choices, underlying: ''};
  if (code) underlying = (await load('warrant-code-index')).codes?.[code] || '';
  else if (!underlying) underlying = choices.find(r => r.code === '2330')?.code || choices[0]?.code || '';
  if (!/^[A-Za-z0-9_-]{1,24}$/.test(underlying) || !choices.some(r => r.code === underlying)) return {rows: [], choices, underlying};
  const data = await load(`warrants-by-underlying/${underlying}`);
  return {...data, choices, underlying};
}
