'use strict';
// Demonstrator inspector. Reads the local demo backend only; nothing here reaches the voice session.
(() => {
  const el = id => document.getElementById(id);
  const make = (tag, text, cls) => { const n = document.createElement(tag); if(text !== undefined && text !== null) n.textContent = text; if(cls) n.className = cls; return n; };
  const money = v => (v < 0 ? '-' : '') + '$' + Math.abs(v).toLocaleString('en-AU', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const STEP = {full_name: 'Full name', dob: 'Date of birth', address: 'Address'};
  let overview = null, sessionId = null, lastSeq = 0, polling = null;

  document.querySelectorAll('.inspector .tab').forEach(tab => tab.addEventListener('click', () => {
    document.querySelectorAll('.inspector .tab').forEach(t => {
      t.setAttribute('aria-selected', String(t === tab));
      el(t.getAttribute('aria-controls')).hidden = t !== tab;
    });
  }));

  function table(headers, rows) {
    const t = make('table'), head = make('tr');
    headers.forEach(h => head.append(make('th', h)));
    t.append(make('thead')); t.tHead.append(head);
    const body = make('tbody');
    rows.forEach(r => { const tr = make('tr'); r.forEach(v => tr.append(make('td', v))); body.append(tr); });
    t.append(body);
    const wrap = make('div', undefined, 'mini-table'); wrap.append(t);
    return wrap;
  }
  function facts(pairs) {
    const dl = make('dl', undefined, 'facts');
    for(const [k, v] of pairs) { const d = make('div'); d.append(make('dt', k), make('dd', v)); dl.append(d); }
    return dl;
  }
  function address(a) {
    return (a.unit ? a.unit + '/' : '') + a.number + ' ' + a.street + ' ' + a.street_type + ', ' + a.suburb + ' ' + a.state + ' ' + a.postcode;
  }

  function renderCustomers() {
    const cards = el('customer-cards'); cards.replaceChildren();
    for(const c of overview.customers) {
      const card = make('details', undefined, 'customer'); card.dataset.customer = c.id;
      const summary = make('summary'); summary.append(make('strong', [c.first_name, c.middle_name, c.last_name].filter(Boolean).join(' ')), make('span', c.id, 'muted'));
      card.append(summary, make('p', c.profile, 'profile'));
      card.append(facts([['Date of birth', c.dob], ['Address', address(c.address)], ['Mobile', c.mobile], ['Email', c.email]]));
      card.append(make('h3', 'Accounts'), table(['Account', 'Number', 'Balance', 'Available'],
        c.accounts.map(a => [a.name, a.bsb + ' ' + a.number, money(a.balance), money(a.available)])));
      if(c.loans.length) card.append(make('h3', 'Loans'), table(['Loan', 'Owing', 'Rate', 'Repayment', 'Next due'],
        c.loans.map(l => [l.product, money(l.balance), l.rate.toFixed(2) + '% ' + l.rate_type, money(l.repayment) + ' ' + l.frequency, l.next_due])));
      if(c.applications.length) card.append(make('h3', 'Applications'), table(['Application', 'Amount', 'Status'],
        c.applications.map(a => [a.product, money(a.amount), a.status])));
      card.append(make('h3', 'Transactions'), table(['Date', 'Description', 'Amount'],
        c.transactions.map(t => [t.posted, t.description, money(t.amount)])));
      if(c.cases.length) card.append(make('h3', 'Cases'), table(['Ref', 'Status', 'Summary'], c.cases.map(k => [k.ref, k.status, k.summary])));
      cards.append(card);
    }
  }
  function renderScenarios() {
    const select = el('scenario'), current = select.value;
    select.replaceChildren(make('option', 'Choose a scenario'));
    select.firstChild.value = '';
    for(const s of overview.scenarios) { const o = make('option', s.title); o.value = s.id; select.append(o); }
    select.value = current;
    showScenario();
  }
  function showScenario() {
    const s = overview?.scenarios.find(x => x.id === el('scenario').value);
    el('scenario-lines').replaceChildren(...(s ? s.lines.map(line => make('li', line)) : []));
    document.querySelectorAll('.customer').forEach(card => { card.open = !!s && card.dataset.customer === s.customer; card.classList.toggle('selected', !!s && card.dataset.customer === s.customer); });
  }
  el('scenario').addEventListener('change', showScenario);

  function renderCases(cases) {
    const list = el('case-list'); list.replaceChildren();
    const fresh = cases.filter(c => !c.seeded);
    if(!fresh.length) { list.append(make('p', 'Cases and callback requests created during calls appear here.', 'empty')); }
    else list.append(table(['Ref', 'Type', 'Customer', 'Status', 'Summary'], fresh.map(c => [c.ref, c.type, c.customer_id || 'Unverified caller', c.status, c.summary])));
  }
  async function loadOverview() {
    try { overview = await window.evidenceApp.api('/api/demo/overview'); renderCustomers(); renderScenarios(); renderCases(overview.cases); }
    catch(e) { el('customer-cards').replaceChildren(make('p', e.message, 'error-message')); }
  }

  function badge(status) { return make('span', status.replace('_', ' '), 'badge state-' + status); }
  function renderIdv(state) {
    const idv = state.idv, box = el('idv-summary'); box.replaceChildren();
    const head = make('div', undefined, 'idv-head'); head.append(make('h3', 'Identity verification'), badge(idv.status));
    box.append(head);
    const rows = Object.entries(idv.steps).map(([step, s]) => {
      const tries = idv.history.filter(h => h.step === step), last = tries[tries.length - 1];
      return [STEP[step], s.status, String(s.attempts) + (s.incomplete ? ' +' + s.incomplete + ' incomplete' : '') + (s.uncertain ? ' +' + s.uncertain + ' uncertain' : ''), s.activated_by || '–', last ? last.method + (last.score != null ? ' ' + last.score : '') : '–'];
    });
    box.append(table(['Step', 'Status', 'Failed tries', 'Activated by', 'Last check'], rows));
    const th = idv.thresholds?.name_match;
    if(th) box.append(make('p', `Name: pass ≥ ${th.high_threshold}, fail < ${th.low_threshold}, LLM fallback ${th.llm_fallback_enabled ? 'on' : 'off'} · Address names: pass ≥ ${idv.thresholds.address_match.high_threshold}`, 'run-meta'));
    if(idv.history.length) {
      const list = make('ol', undefined, 'attempts');
      for(const h of idv.history) {
        const item = make('li');
        item.append(make('strong', STEP[h.step] + ' · ' + h.outcome), make('span', h.method + (h.score != null ? ' · score ' + h.score : '')));
        if(h.normalized) item.append(make('code', h.normalized));
        if(h.missing?.length) item.append(make('span', 'missing: ' + h.missing.join(', ')));
        const comps = Object.entries(h.components || {}).filter(([k]) => k !== 'candidates');
        if(comps.length) item.append(make('span', comps.map(([k, v]) => k + ' ' + (typeof v === 'number' ? v : v)).join(' · '), 'muted'));
        if(h.components?.candidates?.length) item.append(make('span', 'readings: ' + h.components.candidates.join(' or '), 'muted'));
        const calls = h.llm ? (h.llm.calls || [h.llm]) : [];
        for(const call of calls) {
          const verdict = call.response ? (call.response.verdict || call.response.status + (call.response.date ? ' ' + call.response.date : '')) : (call.error || 'no response');
          item.append(make('span', `LLM fallback (${call.component || call.task}): ${verdict}${call.valid ? '' : ' · rejected by Worker'} · ${call.latency_ms} ms` + (call.response?.evidence ? ' · ' + call.response.evidence : ''), 'llm'));
        }
        list.append(item);
      }
      box.append(list);
    }
  }
  const DESCRIBE = {
    session_started: d => 'Call started · ' + d.assistant,
    session_closed: () => 'Call ended',
    idv_started: d => 'ID&V started by ' + d.source,
    idv_step_active: d => STEP[d.step] + ' active (' + d.source + ')',
    idv_step_asked: d => 'Agent asked for ' + STEP[d.step].toLowerCase() + ' (' + d.source + ')',
    idv_attempt: d => STEP[d.step] + ': ' + d.outcome + ' via ' + d.method + (d.llm ? ' + LLM' : ''),
    idv_regex_ignored: d => 'Regex match ignored: ' + d.reason,
    idv_transition_blocked: d => 'Transition blocked: ' + d.reason,
    idv_complete: d => 'ID&V ' + d.status,
    idv_paused: d => 'ID&V paused by ' + d.source + (d.reason ? ': ' + d.reason : ''),
    idv_resumed: d => 'ID&V resumed by ' + d.source + ' at ' + STEP[d.step].toLowerCase(),
    tool_call: d => d.tool + '(' + Object.entries(d.args || {}).map(([k, v]) => k + '=' + JSON.stringify(v)).join(', ') + ')',
    tool_result: d => d.tool + (d.ok ? ' ok' : ' → ' + d.error),
    tool_blocked: d => d.tool + ' blocked: ID&V ' + d.idv,
    tool_error: d => d.tool + ' failed: ' + d.error,
    rag_search: d => 'RAG "' + d.query + '" → ' + (d.results.join(', ') || 'no results'),
    case_created: d => d.type + ' ' + d.ref + ' created' + (d.linked_customer ? '' : ' (unverified caller)'),
    form_opened: d => 'Callback form opened (' + d.purpose + ')',
    form_event_sent: d => 'Backend → agent: ' + d.status + (d.reference ? ' ' + d.reference : '') + (d.delivered ? ' · delivered' : ' · NOT delivered'),
    form_event_received: d => 'Agent received form ' + d.status,
    form_event_ignored: d => 'Agent ignored form event: ' + d.reason,
  };
  const ERRORS = new Set(['tool_error', 'idv_transition_blocked', 'form_event_ignored']);
  function renderEvents(events) {
    const log = el('event-log');
    for(const e of events) {
      const text = (DESCRIBE[e.kind] || (() => e.kind))(e.data);
      const item = make('li', undefined, ERRORS.has(e.kind) || (e.kind === 'form_event_sent' && !e.data.delivered) || e.kind === 'tool_blocked' ? 'warn' : '');
      item.append(make('time', e.ts.slice(11, 19)), make('span', text));
      log.prepend(item);
    }
  }
  async function poll() {
    try {
      const {session} = await window.evidenceApp.api('/api/demo/session?after=' + (sessionId ? lastSeq : 0));
      if(!session) return;
      if(session.id !== sessionId) { sessionId = session.id; lastSeq = 0; el('event-log').replaceChildren(); }
      renderIdv(session.state);
      renderEvents(session.events);
      if(session.events.length) {
        lastSeq = session.events[session.events.length - 1].seq;
        if(session.events.some(e => e.kind === 'case_created' || e.kind === 'form_event_sent')) loadOverview();
      }
    } catch {}
  }
  function setPolling(on) {
    clearInterval(polling); polling = on ? setInterval(poll, 1200) : null;
    if(on) poll();
  }
  window.addEventListener('live-voice-active', event => { if(event.detail) setPolling(true); else { poll(); setTimeout(() => setPolling(false), 4000); } });
  el('reset-demo').addEventListener('click', async () => {
    if(!confirm('Reset all demo customer data and cases?')) return;
    try { await window.evidenceApp.api('/api/demo/reset', {confirm: true}); sessionId = null; lastSeq = 0; el('event-log').replaceChildren();
      el('idv-summary').replaceChildren(make('p', 'Start a call to see verification, tools and events.', 'empty')); await loadOverview(); }
    catch(e) { alert(e.message); }
  });
  window.addEventListener('service-status', () => { if(!overview) { loadOverview(); poll(); } }, {once: true});
})();
