'use strict';
const $ = id => document.getElementById(id);
let token = '', previous = null, busy = false;
const initialWelcome = $('messages').firstElementChild.cloneNode(true);
function node(tag, text, cls) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (cls) el.className = cls; return el; }
function officialLink(source) {
  const a = node('a', source.title + ' ↗');
  const allowed = ['https://www.westpac.com.au/personal-banking/online-banking/making-the-most/lock-card/', 'https://www.westpac.com.au/personal-banking/online-banking/making-the-most/update-details/', 'https://www.westpac.com.au/personal-banking/online-banking/making-the-most/red-web-chat/', 'https://www.westpac.com.au/about-westpac/westpac-group/corporate-governance/artificial-intelligence-at-westpac/'];
  if (allowed.includes(source.url)) { a.href = source.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; }
  return a;
}
async function api(path, body, timeout = 55000) {
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(path, {signal: controller.signal, ...(body ? {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': token}, body: JSON.stringify(body)} : {})});
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.error || Object.values(data.errors || {})[0] || 'Request failed.'); error.errors = data.errors; throw error; }
    return data;
  } catch(e) { if (e.name === 'AbortError') throw new Error('Request timed out. No answer produced. Try again or use Evidence mode.'); throw e; }
  finally { clearTimeout(timer); }
}
async function refreshStatus() {
  try {
    const data = await api('/api/status'); token = data.csrf_token;
    window.serviceStatus = data;  // Scripts that load later read this instead of waiting for an event they missed.
    window.dispatchEvent(new CustomEvent('service-status', {detail:data}));
    $('provider-status').textContent = data.provider.configured ? 'OpenAI key set · unverified' : 'OpenAI key not set · Evidence mode ready';
  } catch(e) { $('provider-status').textContent = 'Server unavailable. Start server.py, then refresh.'; }
}
function setBusy(value) { busy = value; $('send').disabled = value; $('reset').disabled = value; $('mode').disabled = value; $('fixture').disabled = value; document.querySelectorAll('[data-question]').forEach(b => b.disabled = value); }
function renderTrace(result) {
  $('trace').replaceChildren();
  for (const event of result.trace) {
    const item = node('div', undefined, 'event'); item.append(node('strong', event.stage), node('p', event.detail));
    if (event.matches) for (const match of event.matches) item.append(node('p', match.chunk_id + ' · BM25 ' + match.score));
    if (event.states) item.append(node('p', event.states.join(', ')));
    $('trace').append(item);
  }
  for (const source of result.sources) {
    const el = node('div', undefined, 'trace-source'); el.append(officialLink(source), node('div', 'v' + source.version + ' · reviewed ' + source.reviewed_at), node('div', 'Review due ' + source.review_due + ' · effective date unknown'));
    $('trace').append(el);
  }
}
function renderAnswer(result) {
  const article = node('article', undefined, 'answer');
  const meta = node('div', undefined, 'answer-meta');
  meta.append(node('span', result.status.replaceAll('_', ' '), 'badge' + (result.status === 'answered' ? '' : ' warning')),
    node('span', result.model_called ? (result.mode === 'openai' ? 'OpenAI API called · ' : 'Local model called · ') + (result.model?.reported_model || result.model?.name || 'unknown') : 'No model call'), node('span', result.latency_ms + ' ms'));
  if (result.fixture !== 'none') meta.append(node('span', 'SYNTHETIC FIXTURE · ' + result.fixture, 'badge error'));
  article.append(meta);
  if (result.status !== 'answered') article.append(node('p', result.message));
  for (const claim of result.claims) {
    article.append(node('h3', claim.section), node('p', claim.text));
    if (claim.conditions.length) { const ul = node('ul'); claim.conditions.forEach(text => ul.append(node('li', text))); article.append(ul); }
    article.append(node('div', claim.chunk_id + ' · source v' + claim.source_version, 'hash'));
  }
  if (result.error) article.append(node('p', result.error, 'error-message'));
  const sources = node('div', undefined, 'sources'); result.sources.forEach(source => sources.append(officialLink(source))); article.append(sources);
  article.append(node('p', result.next_step, 'next-step')); $('messages').append(article);
}
async function submit(question, options = {}) {
  if (busy || (!options.audio && !question.trim())) return;
  window.dispatchEvent(new CustomEvent('assistant-working'));
  setBusy(true); $('question').value = '';
  // Never echo a raw message, even briefly before server redaction.
  const user = node('div', 'Checking your question privately…', 'user-message'); $('messages').append(user);
  $('messages').scrollTop = $('messages').scrollHeight;
  try {
    const result = options.audio
      ? await api('/api/voice-chat', {audio:options.audio, mime:options.mime, mode:options.mode || $('mode').value, previous, fixture:'none'}, 100000)
      : await api('/api/chat', {question, mode:options.mode || $('mode').value, previous, fixture:options.fixture || $('fixture').value});
    user.textContent = result.input_display; previous = result.status === 'answered' ? result.topic : null;
    renderAnswer(result); renderTrace(result);
    if(result.mode === 'openai') $('provider-status').textContent = result.status === 'answered' ? 'OPENAI RESPONSE VERIFIED · ' + (result.model?.reported_model || result.model?.name) : 'OPENAI MODE · ' + result.status.replaceAll('_',' ');
    window.dispatchEvent(new CustomEvent('assistant-answer', {detail:result}));
    return result;
  } catch(e) { user.textContent = 'Question not echoed.'; $('messages').append(node('p', e.message, 'error-message')); window.dispatchEvent(new CustomEvent('assistant-failed', {detail:e.message})); }
  finally { setBusy(false); $('messages').scrollTop = $('messages').scrollHeight; $('question').focus(); }
}
$('chat-form').addEventListener('submit', e => { e.preventDefault(); submit($('question').value); });
$('question').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit($('question').value); } });
$('messages').addEventListener('click', e => { const button = e.target.closest('[data-question]'); if (button) submit(button.dataset.question); });
$('reset').addEventListener('click', () => { previous = null; $('messages').replaceChildren(initialWelcome.cloneNode(true)); $('trace').replaceChildren(node('p', 'Sources and checks appear after each answer.', 'empty')); $('question').value = ''; $('question').focus(); });
$('mode').addEventListener('change', () => { $('cloud-disclosure').hidden = $('mode').value !== 'openai'; refreshStatus(); });
document.querySelectorAll('.nav').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('.view').forEach(view => { view.hidden = view.id !== button.dataset.view; });
  document.querySelectorAll('.nav').forEach(nav => { nav.classList.toggle('active', nav === button); if (nav === button) nav.setAttribute('aria-current','page'); else nav.removeAttribute('aria-current'); });
  if (button.dataset.view === 'knowledge') loadKnowledge();
}));
let knowledgeFiles = [];
function shortDate(iso) { return new Date(iso + 'T00:00:00').toLocaleDateString('en-AU', {day: 'numeric', month: 'short', year: 'numeric'}); }
async function loadKnowledge() {
  try { knowledgeFiles = (await api('/api/knowledge')).files; showFileList(); }
  catch(e) { $('knowledge-list').replaceChildren(node('p', e.message, 'error-message')); }
}
function showFileList() {
  $('knowledge-file').hidden = true; $('knowledge-list').hidden = false; $('knowledge-list').replaceChildren();
  knowledgeFiles.forEach((file, index) => {
    const card = node('button', undefined, 'card file-card'); card.type = 'button'; card.dataset.file = index;
    const topics = node('ul');
    for (const chunk of file.chunks.slice(0, 4)) topics.append(node('li', chunk.title));
    if (file.chunks.length > 4) topics.append(node('li', `+${file.chunks.length - 4} more`, 'more'));
    card.append(node('h2', file.title), node('p', `${file.file} · ${file.chunks.length} chunks · Fetched ${shortDate(file.fetched)}`, 'file-meta'), topics);
    $('knowledge-list').append(card);
  });
}
function showFile(file, tab = 'chunks') {
  $('knowledge-list').hidden = true; const view = $('knowledge-file'); view.hidden = false; view.replaceChildren();
  const back = node('button', 'All files', 'secondary'); back.type = 'button'; back.addEventListener('click', showFileList);
  const tabs = node('div', undefined, 'tabs'); tabs.setAttribute('role', 'tablist');
  for (const [key, label] of [['chunks', `Chunks (${file.chunks.length})`], ['raw', 'Raw file']]) {
    const t = node('button', label, 'tab'); t.type = 'button'; t.setAttribute('role', 'tab'); t.setAttribute('aria-selected', String(key === tab));
    t.addEventListener('click', () => showFile(file, key)); tabs.append(t);
  }
  view.append(back, node('h2', file.title, 'file-title'), node('p', `${file.file} · Fetched ${shortDate(file.fetched)}`, 'file-meta'), tabs);
  if (tab === 'raw') { view.append(node('pre', file.raw, 'raw-file card')); return; }
  for (const chunk of file.chunks) {
    const card = node('article', undefined, 'card chunk-card'), meta = node('p', undefined, 'chunk-meta');
    const link = node('a', chunk.source); if (chunk.url && chunk.url.startsWith('https://')) { link.href = chunk.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; }
    meta.append(node('span', chunk.label, 'badge'), link);
    card.append(node('h3', chunk.title), node('p', chunk.text), meta, node('p', chunk.id, 'hash'));
    view.append(card);
  }
}
$('knowledge-list').addEventListener('click', e => { const card = e.target.closest('[data-file]'); if (card) { showFile(knowledgeFiles[card.dataset.file]); window.scrollTo(0, 0); } });
function renderEvaluation(report) {
  const target = $('eval-results'); target.replaceChildren();
  if (report.error) { target.append(node('p', report.error, 'error-message')); return; }
  target.append(node('p', 'Run ' + report.run_id + ' · ' + report.started_at + ' · Mode: ' + report.mode + ' · Model: ' + (report.model?.name || 'not available / not used'), 'run-meta'));
  target.append(node('p', 'Prompt: ' + report.prompt_version + ' · Corpus SHA-256: ' + report.corpus_hash, 'run-meta'));
  if (report.model?.digest) target.append(node('p', 'Model digest: ' + report.model.digest, 'run-meta'));
  if (report.warning) target.append(node('p', report.warning, 'error-message'));
  const metrics = node('div', undefined, 'metrics');
  for (const [name, metric] of Object.entries(report.metrics)) { const card = node('div', undefined, 'metric'); card.append(node('strong', metric.value === null ? 'Not measured' : String(metric.value)), node('span', (name.charAt(0).toUpperCase() + name.slice(1)).replaceAll('_', ' ').replace(/ ms$/, ' (ms)')), node('p', metric.basis, 'run-meta')); metrics.append(card); }
  target.append(metrics);
  const wrap = node('div', undefined, 'table-wrap'), table = node('table'); const head = node('thead'), tr = node('tr');
  ['Scenario', 'Kind', 'Expected → observed', 'Result', 'Latency'].forEach(t => tr.append(node('th',t))); head.append(tr); table.append(head);
  const tbody = node('tbody');
  for (const row of report.cases) { const tr = node('tr'); tr.append(node('td',row.id, 'run-case'), node('td',row.kind), node('td',row.expected + ' → ' + row.actual), node('td',row.passed ? 'Pass' : 'FAIL', row.passed ? 'pass' : 'fail'), node('td',row.latency_ms + ' ms')); tbody.append(tr); }
  table.append(tbody); wrap.append(table); target.append(wrap);
  const details = node('details'); details.append(node('summary', 'Raw outcomes')); const pre = node('pre', JSON.stringify(report.cases, null, 2)); pre.style.whiteSpace = 'pre-wrap'; details.append(pre); target.append(details);
}
$('run-tests').addEventListener('click', async () => {
  $('run-tests').disabled = true; $('eval-status').textContent = 'Running…';
  try {
    await api('/api/evaluate', {mode: $('eval-mode').value});
    const deadline = Date.now() + 15 * 60 * 1000;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 750)); const state = await api('/api/evaluation');
      if (state.state === 'complete' || state.state === 'error') { renderEvaluation(state.result); $('eval-status').textContent = state.state === 'complete' ? 'Run complete. Saved to evaluations/.' : 'Evaluation failed.'; return; }
    }
    throw new Error('Evaluation polling timed out. The CLI report may still be written; check evaluations/.');
  } catch(e) { $('eval-status').textContent = e.message; }
  finally { $('run-tests').disabled = false; }
});
window.evidenceApp = {ask:submit, api, refreshStatus, isBusy:()=>busy};
refreshStatus();

requestAnimationFrame(() => { const view = location.hash.slice(1); const button = [...document.querySelectorAll('.nav')].find(b => b.dataset.view === view); if(button) button.click(); });
