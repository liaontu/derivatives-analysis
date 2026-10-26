// Uses the same published TWSE dates as the Python collectors. Unknown years stay unknown.
let active = null;
const taipeiDay = () => new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Taipei',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const parsed = value => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || '')) return null;
  const ms = Date.parse(value+'T00:00:00Z');
  return Number.isFinite(ms) && new Date(ms).toISOString().slice(0,10) === value ? ms : null;
};
export function setTradingCalendar(data) {
  const object = x => x && typeof x === 'object' && !Array.isArray(x);
  const valid = data?.schemaVersion === 1 && object(data.years) && Object.entries(data.years).every(([year, entry]) =>
    /^\d{4}$/.test(year) && object(entry?.closed) && object(entry.opened) && (!entry.exceptions || object(entry.exceptions)) &&
    [...Object.keys(entry.closed), ...Object.keys(entry.opened), ...Object.keys(entry.exceptions || {})].every(d => parsed(d) !== null && d.startsWith(year+'-')) &&
    !Object.keys(entry.opened).some(d => d in entry.closed || d in (entry.exceptions || {})));
  active = valid ? data : null;
}
export function taiwanTradingAge(source, today = taipeiDay(), calendar = active) {
  const start = parsed(source), end = parsed(today);
  if (start === null || end === null) return null;
  if (start > end) return -1;
  if (!calendar?.years?.[source.slice(0,4)] || !calendar.years[today.slice(0,4)]) return null;
  let age = 0;
  for (let cursor = start+86400000; cursor <= end; cursor += 86400000) {
    const value = new Date(cursor), date = value.toISOString().slice(0,10), year = calendar.years[date.slice(0,4)];
    if (!year || !year.closed || !year.opened) return null;
    if (!(date in year.closed) && !(date in (year.exceptions || {})) && (date in year.opened || ![0,6].includes(value.getUTCDay()))) age++;
  }
  return age;
}
export function taiwanFreshnessMessage(source, today = taipeiDay()) {
  const age = taiwanTradingAge(source, today);
  return age === null ? '交易日曆未驗證或日期無效，無法判斷新鮮度' : age < 0 ? '資料日期在未來，請核對來源' : age > 4 ? '資料距今超過 4 個交易日' : '';
}
