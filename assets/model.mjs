export const finite = x => typeof x === 'number' && Number.isFinite(x);
export const fmt = (x, digits = 0) => finite(x) ? x.toLocaleString('zh-TW', { maximumFractionDigits: digits }) : '—';
export const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const cpLabel = x => ({ C: '買權 Call', P: '賣權 Put' }[x] || '—');
export const warrantCp = x => ({ C: '認購', P: '認售' }[x] || '其他');
export const warrantStyle = value => ({ E: '歐式', EUROPEAN: '歐式', '歐式': '歐式', A: '美式', AMERICAN: '美式', '美式': '美式' }[String(value || '').trim().toUpperCase()] || '未提供／未識別');
export const warrantSortLabels = { volume: '成交量高至低', value: '成交金額高至低', expiry: '到期日近至遠', 'expiry-desc': '到期日遠至近', strike: '履約價低至高', 'strike-desc': '履約價高至低', price: '價格低至高' };
export function warrantUnderlyings(rows) {
  const choices = new Map();
  for (const r of rows) {
    if (!r.underlying) continue;
    const code = String(r.underlying).trim();
    if (!choices.has(code)) choices.set(code, { code, name: r.underlyingName || code });
  }
  return [...choices.values()].sort((a, b) => a.code.localeCompare(b.code)).map(r => ({ ...r, label: `${r.code} · ${r.name}` }));
}
export function resolveWarrantUnderlying(choices, query) {
  const q = String(query || '').trim().toLowerCase();
  if (!q) return null;
  const matches = choices.filter(r => [r.code, r.name, r.label].some(v => v.toLowerCase() === q));
  return matches.length === 1 ? matches[0].code : null;
}
export function filterUSMarkets(rows, query = '') {
  const q = query.trim().toLowerCase();
  return rows.filter(r => !q || `${r.contract} ${r.name}`.toLowerCase().includes(q)).sort((a, b) => String(a.name).localeCompare(String(b.name)));
}
export function cotTrend(history, contract, category) {
  return history.map(d => ({ date: d.date, value: d.rows.find(r => r.contract === contract)?.categories.find(r => r.category === category)?.net }));
}
export function isStaleSource(source, today = taipeiToday()) {
  return !!source.date && daysUntil(source.date, today) < -(source.staleAfterDays ?? (source.frequency === 'weekly' ? 14 : 4));
}
export function taipeiToday(now = new Date()) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
}
export function daysUntil(day, today = taipeiToday()) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day || '')) return null;
  return Math.round((Date.parse(day + 'T00:00:00Z') - Date.parse(today + 'T00:00:00Z')) / 86400000);
}
export function filterWarrants(rows, filters, today = taipeiToday()) {
  const query = (filters.search || '').trim().toLowerCase();
  return rows.filter(r => {
    const days = daysUntil(r.expiry, today), last = daysUntil(r.lastTrade, today);
    if (filters.active !== false && ((days !== null && days < 0) || (last !== null && last < 0))) return false;
    if (filters.underlying && String(r.underlying) !== filters.underlying) return false;
    if (query && ![r.code, r.name, r.underlying, r.underlyingName].join(' ').toLowerCase().includes(query)) return false;
    if (filters.market && r.market !== filters.market || filters.cp && r.cp !== filters.cp) return false;
    if (filters.style && warrantStyle(r.style) !== (filters.style === 'unknown' ? '未提供／未識別' : filters.style)) return false;
    if (Number(filters.minVolume) > 0 && (!finite(r.volume) || r.volume < Number(filters.minVolume))) return false;
    if (Number(filters.minDays) > 0 && (days === null || days < Number(filters.minDays))) return false;
    if (Number(filters.maxPrice) > 0 && (!finite(r.price) || r.price > Number(filters.maxPrice))) return false;
    return true;
  }).sort((a, b) => {
    const requested = Object.hasOwn(warrantSortLabels, filters.sort) ? filters.sort : 'volume';
    const key = requested.replace(/-desc$/, ''), asc = ['expiry', 'strike', 'price'].includes(requested);
    const av = key === 'expiry' ? daysUntil(a.expiry, today) : a[key];
    const bv = key === 'expiry' ? daysUntil(b.expiry, today) : b[key];
    const tie = String(a.code).localeCompare(String(b.code));
    if (!finite(av)) return !finite(bv) ? tie : 1;
    if (!finite(bv)) return -1;
    return (av - bv) * (asc ? 1 : -1) || tie;
  });
}
export function oiDistribution(rows, contract, month, session = '一般') {
  const groups = new Map();
  for (const r of rows) {
    if (r.contract !== contract || r.month !== month || r.session !== session || !finite(r.strike) || !['C', 'P'].includes(r.cp)) continue;
    const group = groups.get(r.strike) || { strike: r.strike, C: null, P: null };
    group[r.cp] = finite(r.oi) ? r.oi : null;
    groups.set(r.strike, group);
  }
  return [...groups.values()].sort((a, b) => a.strike - b.strike);
}
export function previousOi(history, current, row) {
  const prev = [...history].filter(d => d.date < current).sort((a, b) => b.date.localeCompare(a.date))[0];
  const item = prev?.rows.find(r => r.contract === row.contract && r.investor === row.investor && r.cp === row.cp);
  return { date: prev?.date, change: finite(row.netOi) && finite(item?.netOi) ? row.netOi - item.netOi : null };
}
export function toCSV(rows, fields) {
  const cell = v => '"' + String(v ?? '').replace(/^[=+@\t\r]/, c => "'" + c).replace(/"/g, '""') + '"';
  return '\uFEFF' + [fields.map(f => cell(f[1])).join(','), ...rows.map(r => fields.map(f => cell(r[f[0]])).join(','))].join('\r\n');
}
