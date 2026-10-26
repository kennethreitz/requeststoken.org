import { DAY, bounds, countdown, dateKey, dateTime, historyCSV, historyRows, latestDay, missingDays, periodTotal, shortDate, trailingWeek } from './observatory-stats.mjs?v=3';

const $ = (id) => document.getElementById(id);
const format = (value) => value === null ? 'Pending' : value.toLocaleString('en-US');
const manifest = JSON.parse($('adoption-data').textContent);
let response = JSON.parse($('download-data').textContent);
let snapshot = response.snapshot;
let offset = Date.parse(response.serverTime) - Date.now();
let activeDay = '', historyPage = 0, projectPage = 0, filteredHistory = [];
let pendingFetch = false, retryTimer = null, dialogOpener = null;
const now = () => Date.now() + offset;
const node = (tag, text, className) => {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (className) item.className = className;
  return item;
};
function link(label, url) {
  const item = node('a', label);
  const parsed = new URL(url, document.baseURI);
  if (parsed.protocol !== 'https:' && parsed.origin !== window.location.origin) return node('span', label);
  item.href = parsed.href; item.target = '_blank'; item.rel = 'noopener noreferrer';
  return item;
}
function statusText() {
  const prefix = response.message ? `${response.message} ` : 'Source check succeeded. ';
  $('source-status').textContent = `${prefix}Data through ${snapshot.latestDate} UTC. Snapshot: ${snapshot.fetchedAt}. Last check: ${response.checkedAt}.${response.refreshing ? ' A shared source check is running.' : ''}`;
}
function renderCounts() {
  for (const period of ['month', 'lifetime']) $('count-' + period).textContent = format(periodTotal(snapshot, period, now()));
  const day = latestDay(snapshot), week = trailingWeek(snapshot);
  $('count-day').textContent = format(day ? day.downloads : null);
  $('when-day').textContent = day ? `${shortDate(day.date)} UTC · latest reported day` : 'No reported day yet';
  $('count-week').textContent = format(week ? week.downloads : null);
  $('when-week').textContent = week ? `${shortDate(week.start)} – ${shortDate(week.end)} UTC${week.available < 7 ? ` · ${week.available} of 7 days reported` : ''}` : 'No reported days yet';
  $('total-coverage').textContent = `From ${snapshot.firstDate} · ${missingDays(snapshot)} missing dates`;
  const aggregate = snapshot.daily.reduce((sum, row) => sum + row.downloads, 0);
  const difference = aggregate - snapshot.rawTotal;
  $('coverage-note').textContent = `Coverage: ${snapshot.firstDate}–${snapshot.latestDate}. ${format(snapshot.daily.length)} reported dates; ${missingDays(snapshot)} missing dates. Raw historical count: ${format(snapshot.rawTotal)}. Daily aggregate sum: ${format(aggregate)}. Difference (aggregate minus raw): ${difference > 0 ? '+' : ''}${format(difference)}.`;
  statusText();
}
function tick() {
  const moment = now();
  for (const period of ['month']) if ($('reset-' + period)) $('reset-' + period).textContent = countdown(bounds(period, moment).end, moment);
  if (activeDay !== dateKey(moment)) {
    activeDay = dateKey(moment); historyPage = 0;
    setRange(); renderCounts(); renderHistory();
  }
}
async function refresh() {
  if (pendingFetch) return;
  pendingFetch = true; $('refresh-counts').disabled = true;
  try {
    const result = await fetch('/api/observatory', { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!result.ok) throw new Error('Source API unavailable');
    const body = await result.json();
    if (!body.snapshot || !Array.isArray(body.snapshot.daily) || !Number.isFinite(Date.parse(body.serverTime))) throw new Error('Invalid response');
    response = body; snapshot = body.snapshot; offset = Date.parse(body.serverTime) - Date.now();
    clearTimeout(retryTimer);
    if (body.refreshing && document.visibilityState === 'visible') retryTimer = setTimeout(refresh, 10_000);
  } catch (error) {
    console.error(error);
    $('source-status').textContent = `Could not reach the statistics service. Showing the retained snapshot from ${snapshot.fetchedAt}, through ${snapshot.latestDate} UTC.`;
  } finally { pendingFetch = false; $('refresh-counts').disabled = false; }
  // Render outside the network try, so a display bug is never reported as a source outage.
  renderCounts(); renderHistory(); renderQueries(); tick();
}

function setRange() {
  const range = $('history-range').value;
  if (range === 'custom') return;
  const end = dateKey(now());
  $('history-to').value = end;
  $('history-from').value = range === 'all' ? snapshot.firstDate : dateKey(dateTime(end) - (Number(range) - 1) * DAY);
}
function renderHistory() {
  const from = $('history-from').value || snapshot.firstDate, to = $('history-to').value || dateKey(now());
  const invalid = from > to;
  $('history-error').hidden = !invalid;
  $('history-error').textContent = invalid ? 'Choose an end date on or after the start date.' : '';
  filteredHistory = invalid ? [] : historyRows(snapshot, $('history-period').value, now()).filter((r) => r.date <= to && r.end >= from);
  const pages = Math.max(1, Math.ceil(filteredHistory.length / 25));
  historyPage = Math.min(historyPage, pages - 1);
  const body = $('history-body'); body.replaceChildren();
  for (const row of filteredHistory.slice(historyPage * 25, historyPage * 25 + 25)) {
    const tr = node('tr');
    const value = row.downloads === null ? (row.status === 'pending' ? 'Pending' : 'Unknown') : format(row.downloads);
    for (const cell of [row.date, row.end, value, `${row.available} / ${row.expected} days`, row.status]) tr.append(node('td', cell));
    body.append(tr);
  }
  if (!filteredHistory.length) {
    const tr = node('tr'), td = node('td', 'No dates in this range.'); td.colSpan = 5; tr.append(td); body.append(tr);
  }
  $('history-page').textContent = `${filteredHistory.length} periods · ${historyPage + 1} / ${pages}`;
  $('history-prev').disabled = historyPage === 0; $('history-next').disabled = historyPage + 1 >= pages;
  $('export-history').disabled = !filteredHistory.length;
  renderChart([...filteredHistory].reverse());
}
function renderChart(rows) {
  const svg = $('history-chart');
  for (const child of [...svg.children]) if (!['title', 'desc'].includes(child.localName)) child.remove();
  const element = (tag, attrs, text) => {
    const child = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [key, value] of Object.entries(attrs)) child.setAttribute(key, value);
    if (text !== undefined) child.textContent = text;
    svg.append(child); return child;
  };
  const max = Math.max(1, ...rows.map((r) => r.downloads ?? 0));
  element('line', { x1: 65, x2: 975, y1: 194, y2: 194, class: 'chart-axis' });
  element('line', { x1: 65, x2: 975, y1: 25, y2: 25, class: 'chart-axis' });
  const compact = (v) => Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(v);
  element('text', { x: 5, y: 30 }, compact(max)); element('text', { x: 5, y: 198 }, '0');
  let segment = [];
  const draw = () => {
    if (segment.length === 1) { const [cx, cy] = segment[0].split(','); element('circle', { cx, cy, r: 3 }); }
    else if (segment.length > 1) element('polyline', { points: segment.join(' '), class: 'chart-line' });
    segment = [];
  };
  rows.forEach((r, i) => {
    if (r.downloads === null) { draw(); return; }
    const x = 65 + i / Math.max(1, rows.length - 1) * 910, y = 194 - r.downloads / max * 169;
    segment.push(`${x.toFixed(2)},${y.toFixed(2)}`);
  });
  draw();
  if (rows.length) {
    element('text', { x: 65, y: 224 }, rows[0].date);
    element('text', { x: 975, y: 224, 'text-anchor': 'end' }, rows[rows.length - 1].end);
  }
  if (!rows.some((r) => r.downloads !== null)) element('text', { x: 500, y: 110, 'text-anchor': 'middle' }, 'No reported counts in this range');
}
function renderQueries() {
  const container = $('source-queries'); container.replaceChildren();
  for (const [name, sql] of Object.entries(snapshot.queries)) {
    const block = node('div', undefined, 'obs-query');
    const pre = node('pre', sql); block.append(node('h3', name), pre);
    block.append(link('Run on public ClickHouse', 'https://sql-clickhouse.clickhouse.com/?' + new URLSearchParams({ user: 'demo', query: sql })));
    const button = node('button', 'Copy SQL'); button.type = 'button';
    button.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(sql); button.textContent = 'Copied'; }
      catch { button.textContent = 'Select the SQL above to copy'; }
    });
    block.append(button); container.append(block);
  }
}

function renderProjects() {
  const query = $('project-search').value.trim().toLowerCase();
  const sector = $('project-sector').value, category = $('project-category').value, sort = $('project-sort').value;
  const projects = manifest.entries.filter((e) => {
    const text = [e.organization, e.project, e.summary, e.category, e.useScope, e.usageDetails.title, e.usageDetails.explanation, e.usageDetails.scopeNote, ...e.usageDetails.flow].join(' ').toLowerCase();
    return (sector === 'all' || e.sector === sector) && (category === 'all' || e.category === category) && (!query || text.includes(query));
  }).sort((a, b) => a[sort].localeCompare(b[sort]) || a.project.localeCompare(b.project));
  const pages = Math.max(1, Math.ceil(projects.length / 12)); projectPage = Math.min(projectPage, pages - 1);
  $('project-count').textContent = `${projects.length} of ${manifest.entries.length} projects · reviewed source snapshots`;
  const grid = $('project-grid'); grid.replaceChildren();
  for (const entry of projects.slice(projectPage * 12, projectPage * 12 + 12)) {
    const card = node('article', undefined, 'obs-panel obs-project');
    const head = node('div', undefined, 'obs-project-head'), img = node('img');
    img.src = entry.avatarPath; img.alt = ''; img.loading = 'lazy'; img.width = 40; img.height = 40;
    head.append(img, node('h3', entry.organization));
    card.append(head, node('p', entry.project, 'obs-project-name'), node('p', `${entry.category} · ${entry.useScope}`, 'obs-muted'), node('p', entry.summary));
    const button = node('button', 'How it uses Requests'); button.type = 'button';
    button.setAttribute('aria-label', `How ${entry.organization} ${entry.project} uses Requests`);
    button.addEventListener('click', () => openProject(entry, button)); card.append(button); grid.append(card);
  }
  if (!projects.length) grid.append(node('p', 'No matching projects. Try another term or reset the sector/category filters.', 'obs-muted'));
  $('project-page').textContent = `${projectPage + 1} / ${pages}`;
  $('project-prev').disabled = projectPage === 0; $('project-next').disabled = projectPage + 1 >= pages;
}
function openProject(entry, opener) {
  dialogOpener = opener;
  $('project-dialog-title').textContent = `${entry.organization} · ${entry.project}`;
  const content = $('project-dialog-content'); content.replaceChildren();
  content.append(node('p', `${entry.category} · ${entry.useScope} · ${entry.evidenceType}`, 'obs-muted'));
  content.append(node('h3', entry.usageDetails.title), node('p', entry.usageDetails.explanation));
  const flow = node('ol'); for (const step of entry.usageDetails.flow) flow.append(node('li', step));
  content.append(flow, node('p', entry.usageDetails.scopeNote, 'obs-warning'));
  if (entry.usageDetails.verification === 'dependency') content.append(node('p', 'Dependency declaration only; no concrete runtime call is asserted.', 'obs-warning'));
  content.append(node('h3', 'Check the source'));
  const sources = node('ul');
  for (const source of entry.usageDetails.sources) {
    const li = node('li'); li.append(link(source.label, source.evidenceUrl), node('p', `${source.sourcePath}:${source.lineStart}–${source.lineEnd} · reviewed ${source.checkedAt}`, 'obs-muted')); sources.append(li);
  }
  content.append(sources);
  const details = node('details'); details.append(node('summary', 'Original evidence excerpt'), node('pre', entry.evidenceSnippet), link('Pinned source file', entry.evidenceUrl));
  content.append(details, node('p', `Commit: ${entry.commitSha}`, 'obs-muted'), node('p', `Source SHA-256: ${entry.sourceSha256}`, 'obs-muted'));
  $('project-dialog').showModal(); $('close-project').focus();
}

$('refresh-counts').addEventListener('click', refresh);
$('history-range').addEventListener('change', () => { historyPage = 0; setRange(); renderHistory(); });
$('history-period').addEventListener('change', () => { historyPage = 0; renderHistory(); });
for (const id of ['history-from', 'history-to']) $(id).addEventListener('change', () => { $('history-range').value = 'custom'; historyPage = 0; renderHistory(); });
$('history-prev').addEventListener('click', () => { historyPage--; renderHistory(); });
$('history-next').addEventListener('click', () => { historyPage++; renderHistory(); });
$('export-history').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([historyCSV(filteredHistory)], { type: 'text/csv;charset=utf-8' }));
  const a = node('a'); a.href = url; a.download = `requests-${$('history-period').value}-history.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
for (const id of ['project-search', 'project-sector', 'project-category', 'project-sort']) $(id).addEventListener(id === 'project-search' ? 'input' : 'change', () => { projectPage = 0; renderProjects(); });
$('project-prev').addEventListener('click', () => { projectPage--; renderProjects(); });
$('project-next').addEventListener('click', () => { projectPage++; renderProjects(); });
$('close-project').addEventListener('click', () => $('project-dialog').close());
$('project-dialog').addEventListener('click', (event) => {
  if (event.target !== $('project-dialog')) return;
  const rect = event.target.getBoundingClientRect();
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close();
});
$('project-dialog').addEventListener('close', () => dialogOpener?.focus());
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') { tick(); refresh(); } else clearTimeout(retryTimer); });
tick(); renderQueries(); renderProjects();
setInterval(tick, 1000);
setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, 300_000);
if (response.refreshing) retryTimer = setTimeout(refresh, 10_000);
