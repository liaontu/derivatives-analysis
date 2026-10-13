export const finite = value => typeof value === 'number' && Number.isFinite(value);
export function spreadPercent(row) {
  if (!finite(row.bid) || !finite(row.ask) || row.bid < 0 || row.ask <= 0 || row.ask < row.bid) return null;
  return (row.ask - row.bid) / ((row.ask + row.bid) / 2) * 100;
}
export function putCallRatio(rows) {
  const sums = { C: 0, P: 0 };
  for (const row of rows) if (finite(row.oi) && row.oi >= 0 && row.cp in sums) sums[row.cp] += row.oi;
  return { call: sums.C, put: sums.P, ratio: sums.C ? sums.P / sums.C : null };
}
export function payoff({kind, cp, side, entry, strike, ratio = 1, multiplier, quantity}, target) {
  if (![entry,multiplier,quantity,target].every(finite) || entry < 0 || multiplier <= 0 || quantity <= 0 || !Number.isInteger(quantity) || target < 0) return null;
  if (kind === 'futures') return (target - entry) * (side === 'short' ? -1 : 1) * multiplier * quantity;
  if (!finite(strike) || strike < 0 || !finite(ratio) || ratio <= 0 || !['C','P'].includes(cp)) return null;
  const intrinsic = Math.max(0, cp === 'C' ? target - strike : strike - target);
  return (intrinsic * (kind === 'warrants' ? ratio : 1) - entry) * multiplier * quantity;
}
export function breakEven({kind, cp, entry, strike, ratio=1}) {
  if (!finite(entry) || entry < 0) return null;
  if (kind === 'futures') return entry;
  if (!finite(strike) || !finite(ratio) || ratio <= 0) return null;
  const result = strike + (cp === 'C' ? 1 : -1) * entry / (kind === 'warrants' ? ratio : 1);
  return result >= 0 ? result : null;
}
export function normalizedHistory(series) {
  // Compare only common calendar dates; rebase every line at the same first date.
  if (!series.length || series.some(s=>!s.history?.length)) return [];
  const maps = series.map(s=>new Map(s.history.filter(p=>finite(p.close)&&p.close>0).map(p=>[new Date(p.time*1000).toISOString().slice(0,10),p.close])));
  const dates = [...maps[0].keys()].filter(day=>maps.every(m=>m.has(day))).sort();
  if (!dates.length) return [];
  return series.map((s,i)=>({name:s.name,key:s.key,points:dates.map(day=>({day,value:(maps[i].get(day)/maps[i].get(dates[0])-1)*100}))}));
}
