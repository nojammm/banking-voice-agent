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

  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const day = (iso, year = true) => { const [y, m, d] = iso.split('-'); return `${+d} ${MONTHS[m - 1]}` + (year ? ' ' + y : ''); };
  function rows(items) {
    const list = make('ul', undefined, 'rows');
    for(const [title, sub, amount, amountSub] of items) {
      const li = make('li'), main = make('div', undefined, 'row-main'), side = make('div', undefined, 'row-side');
      main.append(make('span', title, 'row-title'));
      if(sub) main.append(make('span', sub, 'row-sub'));
      if(amount) side.append(make('span', amount, 'row-amount'));
      if(amountSub) side.append(make('span', amountSub, 'row-sub'));
      li.append(main, side); list.append(li);
    }
    return list;
  }
  function section(title, content) { const s = make('section', undefined, 'customer-section'); s.append(make('h3', title), content); return s; }
  function renderCustomers() {
    const cards = el('customer-cards'); cards.replaceChildren();
    for(const c of overview.customers) {
      const card = make('details', undefined, 'customer'); card.dataset.customer = c.id;
      const summary = make('summary'); summary.append(make('strong', [c.first_name, c.middle_name, c.last_name].filter(Boolean).join(' ')), make('span', c.id, 'muted'));
      const body = make('div', undefined, 'customer-body');
      body.append(make('p', c.profile, 'profile'),
        facts([['Date of birth', day(c.dob)], ['Mobile', c.mobile], ['Address', address(c.address)], ['Email', c.email]]));
      body.append(section('Accounts', rows(c.accounts.map(a => [a.name, a.bsb + ' ' + a.number, money(a.balance), 'Available ' + money(a.available)]))));
      if(c.loans.length) body.append(section('Loans', rows(c.loans.map(l =>
        [l.product, l.rate.toFixed(2) + '% ' + l.rate_type + ' · ' + money(l.repayment) + ' ' + l.frequency, money(l.balance), 'Due ' + day(l.next_due, false)]))));
      if(c.applications.length) body.append(section('Applications', rows(c.applications.map(a => [a.product, a.status, money(a.amount)]))));
      body.append(section('Recent transactions', rows(c.transactions.map(t =>
        [t.description, day(t.posted, false) + (t.status === 'pending' ? ' · pending' : ''), money(t.amount)]))));
      if(c.cases.length) body.append(section('Cases', rows(c.cases.map(k => [k.ref, k.summary, k.status]))));
      card.append(summary, body);
      card.addEventListener('toggle', () => {
        if(card.open) document.querySelectorAll('.customer').forEach(other => { if(other !== card) other.open = false; });
        card.classList.toggle('selected', card.open);
      });
      cards.append(card);
    }
  }
  function renderTests() {
    el('test-cards').replaceChildren(...overview.scenarios.map(s => {
      const card = make('li', undefined, 'test-card');
      const button = make('button', s.title);
      button.type = 'button';
      button.addEventListener('click', () => showCustomer(s.customer));
      card.append(button);
      return card;
    }));
  }
  function showCustomer(id) {
    el('tab-customers').click();
    const card = document.querySelector(`.customer[data-customer="${id}"]`);
    if(card) { card.open = true; card.scrollIntoView({block: 'nearest', behavior: 'smooth'}); }
  }

  function renderCases(cases) {
    const list = el('case-list'); list.replaceChildren();
    const fresh = cases.filter(c => !c.seeded);
    if(!fresh.length) { list.append(make('p', 'Cases and callback requests created during calls appear here.', 'empty')); }
    else list.append(table(['Ref', 'Type', 'Customer', 'Status', 'Summary'], fresh.map(c => [c.ref, c.type, c.customer_id || 'Unverified caller', c.status, c.summary])));
  }
  async function loadOverview() {
    try { overview = await window.evidenceApp.api('/api/demo/overview'); renderCustomers(); renderTests(); renderCases(overview.cases); }
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
  const start = () => { if(!overview) { loadOverview(); poll(); } };
  if(window.serviceStatus) start(); else window.addEventListener('service-status', start, {once: true});
})();
