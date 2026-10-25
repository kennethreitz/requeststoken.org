import test from 'node:test';
import assert from 'node:assert/strict';
import { DAY, bounds, dateKey, historyCSV, historyRows, missingDays, periodTotal } from '../static/js/observatory-stats.mjs';

function snapshot(last = '2026-10-25') {
  const end = Date.parse(last + 'T00:00:00Z');
  const daily = Array.from({ length: 60 }, (_, i) => ({ date: dateKey(end - (59 - i) * DAY), downloads: 100 }));
  return { daily, firstDate: daily[0].date, latestDate: last, rawTotal: 6000 };
}
test('clock ticks never generate downloads and corrections replace counts', () => {
  const s = snapshot(), now = Date.parse('2026-10-25T12:00:00Z');
  for (const p of ['day', 'week', 'month', 'lifetime']) assert.equal(periodTotal(s, p, now), periodTotal(s, p, now + 1000));
  const corrected = { ...s, rawTotal: 5980, daily: s.daily.map((r) => r.date === '2026-10-25' ? { ...r, downloads: 80 } : r) };
  assert.equal(periodTotal(corrected, 'day', now), 80);
  assert.equal(periodTotal(corrected, 'lifetime', now), 5980);
});
test('new UTC day and Monday reset the period without deleting history', () => {
  const s = snapshot(), before = Date.parse('2026-10-25T23:59:59Z'), after = before + 1000;
  assert.equal(periodTotal(s, 'day', before), 100);
  assert.equal(periodTotal(s, 'day', after), null);
  assert.equal(periodTotal(s, 'week', before), 700);
  assert.equal(periodTotal(s, 'week', after), null);
  assert.equal(periodTotal(s, 'lifetime', after), 6000);
  assert.equal(historyRows(s, 'day', after).find((r) => r.date === '2026-10-25').downloads, 100);
});
test('calendar month, year and leap boundaries do not use rolling windows', () => {
  const s = snapshot('2026-10-31'), t = Date.parse('2026-11-01T00:00:00Z');
  assert.equal(periodTotal(s, 'month', t - 1000), 3100);
  assert.equal(periodTotal(s, 'month', t), null);
  assert.equal(bounds('month', Date.parse('2028-02-29T23:59:59Z')).end, Date.parse('2028-03-01T00:00:00Z'));
  assert.equal(bounds('week', Date.parse('2027-01-01T00:00:00Z')).start, Date.parse('2026-12-28T00:00:00Z'));
});
test('missing, pending, partial and explicit zero remain distinguishable in CSV', () => {
  const s = snapshot(); s.daily = s.daily.filter((r) => r.date !== '2026-10-20');
  s.daily[s.daily.length - 1].downloads = 0;
  assert.equal(missingDays(s), 1);
  const rows = historyRows(s, 'day', Date.parse('2026-10-26T00:00:00Z'));
  assert.equal(rows[0].status, 'pending');
  assert.equal(rows.find((r) => r.date === '2026-10-20').downloads, null);
  assert.equal(rows.find((r) => r.date === '2026-10-25').downloads, 0);
  const csv = historyCSV(rows);
  assert.match(csv, /2026-10-20,2026-10-20,,0,1,missing/);
  assert.match(csv, /2026-10-25,2026-10-25,0,1,1,reported/);
  assert.equal(historyRows(s, 'week', Date.parse('2026-10-25T12:00:00Z'))[0].status, 'partial');
});
