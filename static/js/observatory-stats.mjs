// Counts are observations. The clock only selects a calendar period.
export const DAY = 86_400_000;
export const dateKey = (time) => new Date(time).toISOString().slice(0, 10);
export const dateTime = (date) => Date.parse(`${date}T00:00:00Z`);
export function bounds(period, now) {
  const d = new Date(now), day = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  if (period === 'week') {
    const start = day - ((d.getUTCDay() + 6) % 7) * DAY;
    return { start, end: start + 7 * DAY };
  }
  if (period === 'month') return {
    start: Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1),
    end: Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1),
  };
  return { start: day, end: day + DAY };
}
export function periodTotal(snapshot, period, now) {
  if (period === 'lifetime') return snapshot.rawTotal;
  const { start, end } = bounds(period, now), today = dateKey(now);
  const rows = snapshot.daily.filter((r) => dateTime(r.date) >= start && dateTime(r.date) < end && r.date <= today);
  return rows.length ? rows.reduce((sum, r) => sum + r.downloads, 0) : null;
}
const SHORT = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
export const shortDate = (date) => SHORT.format(dateTime(date));
// The most recent reported day, and the 7 calendar days ending on it. Never estimated.
export function latestDay(snapshot) {
  const row = snapshot.daily.find((r) => r.date === snapshot.latestDate);
  return row ? { date: row.date, downloads: row.downloads } : null;
}
export function trailingWeek(snapshot) {
  if (!latestDay(snapshot)) return null;
  const end = dateTime(snapshot.latestDate), start = end - 6 * DAY;
  const rows = snapshot.daily.filter((r) => dateTime(r.date) >= start && dateTime(r.date) <= end);
  return { start: dateKey(start), end: snapshot.latestDate, downloads: rows.reduce((sum, r) => sum + r.downloads, 0), available: rows.length };
}
export function historyRows(snapshot, period, now) {
  const today = bounds('day', now).start;
  const values = new Map(snapshot.daily.map((r) => [r.date, r.downloads]));
  const groups = new Map();
  for (let t = dateTime(snapshot.firstDate); t <= today; t += DAY) {
    const { start, end } = bounds(period, t), key = dateKey(start);
    if (!groups.has(key)) groups.set(key, {
      date: key, end: dateKey(end - DAY), downloads: null,
      available: 0, expected: Math.round((end - start) / DAY), status: 'missing',
    });
    const row = groups.get(key), value = values.get(dateKey(t));
    if (value !== undefined) { row.downloads = (row.downloads ?? 0) + value; row.available++; }
  }
  for (const row of groups.values()) {
    if (dateTime(row.end) >= today || row.date > snapshot.latestDate) row.status = row.downloads === null ? 'pending' : 'partial';
    else if (row.available === row.expected) row.status = 'reported';
    else if (row.available) row.status = 'incomplete';
  }
  return [...groups.values()].reverse();
}
export function missingDays(snapshot) {
  return Math.round((dateTime(snapshot.latestDate) - dateTime(snapshot.firstDate)) / DAY) + 1 - snapshot.daily.length;
}
export function countdown(end, now) {
  const seconds = Math.max(0, Math.ceil((end - now) / 1000));
  const days = Math.floor(seconds / 86400);
  return `${days ? `${days}d ` : ''}${String(Math.floor(seconds % 86400 / 3600)).padStart(2, '0')}:${String(Math.floor(seconds % 3600 / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}
export function historyCSV(rows) {
  return 'period_start,period_end,downloads,reported_days,expected_days,status\r\n' + rows.map((r) =>
    [r.date, r.end, r.downloads ?? '', r.available, r.expected, r.status].join(',')
  ).join('\r\n');
}

// Chart y-scales. Values that cannot be plotted (null, NaN, and ≤0 on log) return null so callers draw a gap.
export function scaleDomain(values, mode) {
  const usable = values.filter((v) => Number.isFinite(v) && (mode === 'log' ? v > 0 : v >= 0));
  if (mode !== 'log') return { mode: 'linear', min: 0, max: Math.max(1, ...usable) };
  if (!usable.length) return { mode: 'log', min: 1, max: 10 };
  const lo = Math.floor(Math.log10(Math.min(...usable))), hi = Math.ceil(Math.log10(Math.max(...usable)));
  return { mode: 'log', min: 10 ** lo, max: 10 ** Math.max(hi, lo + 1) };
}
export function scaleY(value, domain, top = 25, bottom = 194) {
  if (!Number.isFinite(value)) return null;
  if (domain.mode === 'log') {
    if (value <= 0) return null;
    const lo = Math.log10(domain.min), hi = Math.log10(domain.max);
    return bottom - (Math.log10(value) - lo) / (hi - lo) * (bottom - top);
  }
  if (value < 0) return null;
  return bottom - value / domain.max * (bottom - top);
}
export function logTicks(domain) {
  const ticks = [];
  for (let k = Math.round(Math.log10(domain.min)); k <= Math.round(Math.log10(domain.max)); k++) ticks.push(10 ** k);
  return ticks;
}
export function tickLabel(value) {
  for (const [size, suffix] of [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']]) {
    if (value >= size) return `${+(value / size).toFixed(1)}${suffix}`;
  }
  return String(value);
}
