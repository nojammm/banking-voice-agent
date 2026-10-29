'use strict';
(() => {
  const el = id => document.getElementById(id);
  const sdk = window.LivekitClient;
  let room = null, microphone = null, generation = 0, busy = false, ready = false;
  let connectTimer, limitTimer, audioContext = null;
  const status = text => { el('live-status').textContent = text; };
  const STATES = {idle:'Ready', connecting:'Connecting', listening:'Listening', thinking:'Thinking', speaking:'Speaking'};
  const GUARDS = {permission_denied:'Account action blocked', blocked_injection:'Override attempt blocked',
    unsupported_number:'Unverified detail withheld', unknown_source:'Unverified source withheld'};
  const AUTH = {in_progress:'Verifying identity', paused:'Verification paused', verified:'Identity verified', failed:'Verification unsuccessful'};
  const CASES = {complaint:'Complaint', dispute:'Dispute', fraud:'Fraud report', hardship:'Hardship notice',
    escalation:'Follow-up request', callback:'Callback request'};

  // Avatar: the three parts of the W ripple in sequence; amplitude follows the live audio level.
  const parts = [...document.querySelectorAll('#avatar .w-part')];
  const MOTION = {idle:{amp:1.1, speed:1.6}, connecting:{amp:1.8, speed:4}, listening:{amp:1.2, speed:2.2},
    thinking:{amp:2.2, speed:6.5}, speaking:{amp:1.2, speed:5.2}};
  const still = matchMedia('(prefers-reduced-motion: reduce)');
  const meters = {user:null, agent:null};
  let mode = 'idle', amp = 1, phase = 0, last = performance.now();
  function setMode(next) {
    mode = next;
    el('live-state').textContent = STATES[next];
  }
  function level(meter) {
    if(!meter) return 0;
    meter.analyser.getByteTimeDomainData(meter.buffer);
    let sum = 0;
    for(const v of meter.buffer) { const x = (v - 128) / 128; sum += x * x; }
    return Math.min(1, Math.sqrt(sum / meter.buffer.length) * 5);
  }
  function animate(now) {
    const dt = Math.min(.05, (now - last) / 1000); last = now;
    const motion = MOTION[mode];
    const live = mode === 'speaking' ? level(meters.agent) : mode === 'listening' ? level(meters.user) : 0;
    amp += ((still.matches ? 0 : motion.amp + live * 7) - amp) * .12;
    phase += motion.speed * dt;
    parts.forEach((part, i) => { part.style.transform = `translateY(${(-amp * Math.sin(phase - i * .85)).toFixed(2)}px)`; });
    requestAnimationFrame(animate);
  }
  requestAnimationFrame(animate);
  function meter(track) {
    try {
      audioContext ??= new AudioContext();
      const analyser = audioContext.createAnalyser(); analyser.fftSize = 512;
      audioContext.createMediaStreamSource(new MediaStream([track])).connect(analyser);
      return {analyser, buffer:new Uint8Array(analyser.fftSize)};
    } catch { return null; }
  }

  // Transcript: one bubble per LiveKit segment; customer on the right, agent on the left.
  const segments = new Map();
  let currentAgent = null, pending = {};
  function scroll() { el('transcript').scrollTop = el('transcript').scrollHeight; }
  function message(id, role) {
    let item = segments.get(id);
    if(item) return item;
    // Consecutive customer segments (pauses mid-turn) share one bubble.
    const previous = el('transcript').lastElementChild;
    if(role === 'user' && previous?.classList.contains('user')) { segments.set(id, previous); return previous; }
    item = document.createElement('li'); item.className = 'msg ' + role;
    const bubble = document.createElement('p'); bubble.className = 'bubble';
    const meta = document.createElement('div'); meta.className = 'msg-meta';
    item.append(bubble, meta); el('transcript').append(item); segments.set(id, item);
    if(role === 'agent') { currentAgent = item; attach(pending); pending = {}; }
    else currentAgent = null;
    return item;
  }
  function attach(info) {
    if(!currentAgent) {
      for(const [key, value] of Object.entries(info)) pending[key] = Array.isArray(value) ? [...(pending[key] || []), ...value] : value;
      return;
    }
    const meta = currentAgent.querySelector('.msg-meta');
    if(info.guard) { const g = document.createElement('span'); g.className = 'guard'; g.textContent = GUARDS[info.guard] || 'Blocked'; meta.prepend(g); }
    for(const source of info.sources || []) meta.append(sourceLink(source));
    for(const item of info.cases || []) { const c = document.createElement('span'); c.className = 'case-chip'; c.textContent = (CASES[item.kind] || 'Record') + ' ' + item.ref; meta.append(c); }
    if(info.latency) { const t = document.createElement('span'); t.className = 'latency'; t.textContent = (info.latency / 1000).toFixed(1) + ' s'; meta.append(t); }
    scroll();
  }
  function sourceLink(source) {
    const a = document.createElement('a');
    a.textContent = source.title;
    if(/^https:\/\//.test(source.url || '')) { a.href = source.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    return a;
  }
  function setAuth(state) {
    const chip = el('auth-state');
    chip.hidden = !AUTH[state]; chip.textContent = AUTH[state] || ''; chip.dataset.state = state || '';
  }

  // Callback forms: the backend validates and saves, then tells the agent what happened. A lending callback asks for
  // consent too; a call back for a caller who couldn't be verified asks only for a name and number.
  const dialog = el('callback-dialog');
  const FORMS = {loan_callback: {title: 'Talk to a lending specialist', submit: 'Request callback', consent: true},
                 contact_callback: {title: 'Request a call back', submit: 'Request call back', consent: false}};
  let activeForm = null, activeKind = 'loan_callback';
  function fieldErrors(errors = {}) {
    for(const field of ['name', 'phone', 'consent', 'form']) el('cb-' + field + '-error').textContent = errors[field] || '';
  }
  function openForm(formId, kind) {
    activeForm = formId; activeKind = kind;
    el('callback-form').reset(); fieldErrors(); el('callback-submit').disabled = false;
    el('callback-title').textContent = FORMS[kind].title; el('callback-submit').textContent = FORMS[kind].submit;
    el('cb-consent-row').hidden = !FORMS[kind].consent;
    if(!dialog.open) dialog.showModal();
    el('cb-name').focus();
  }
  function closeForm(reason) {
    const formId = activeForm; activeForm = null;
    if(dialog.open) dialog.close();
    if(formId && reason) window.evidenceApp.api('/api/forms/close', {form_id: formId, reason}).catch(() => {});
  }
  function localErrors() {
    const errors = {}, phone = el('cb-phone').value.replace(/[\s()-]/g, '').replace(/^\+61/, '0');
    if(el('cb-name').value.trim().length < 2) errors.name = 'Enter your name.';
    if(!/^(0[2378]\d{8}|04\d{8})$/.test(phone)) errors.phone = 'Enter an Australian mobile or landline number.';
    if(FORMS[activeKind].consent && !el('cb-consent').checked) errors.consent = 'Consent is required for a callback.';
    return errors;
  }
  el('callback-form').addEventListener('submit', async event => {
    event.preventDefault();
    if(!activeForm) return;
    const errors = localErrors(); fieldErrors(errors);
    if(Object.keys(errors).length) return;
    el('callback-submit').disabled = true;
    try {
      const body = {form_id: activeForm, name: el('cb-name').value, phone: el('cb-phone').value};
      if(FORMS[activeKind].consent) body.consent = el('cb-consent').checked;
      await window.evidenceApp.api('/api/forms/submit', body);
      activeForm = null; dialog.close();
    } catch(error) {
      fieldErrors(error.errors || {form: error.message});
      el('callback-submit').disabled = false;
    }
  });
  el('callback-cancel').addEventListener('click', () => closeForm('cancelled'));
  el('callback-close').addEventListener('click', () => closeForm('closed'));
  dialog.addEventListener('cancel', event => { event.preventDefault(); closeForm('closed'); });

  // Customer number box: the backend checks the format; the Worker looks the number up and tells the agent.
  const numberDialog = el('number-dialog'), NUMBER = /^(CUS)?-?\d{5}$/;
  let numberForm = null, typingSent = null;
  function openNumber(formId, notice) {
    numberForm = formId;
    el('number-form').reset(); el('number-submit').disabled = false;
    el('cn-number-error').textContent = notice === 'not_found' ? 'We couldn’t find that customer number.' : '';
    if(!numberDialog.open) numberDialog.showModal();
    el('cn-number').focus();
  }
  function closeNumber(reason) {
    const formId = numberForm; numberForm = null;
    if(numberDialog.open) numberDialog.close();
    if(formId && reason) window.evidenceApp.api('/api/forms/close', {form_id: formId, reason}).catch(() => {});
  }
  el('number-form').addEventListener('submit', async event => {
    event.preventDefault();
    if(!numberForm) return;
    const value = el('cn-number').value;
    if(!NUMBER.test(value.replace(/\s+/g, '').toUpperCase())) { el('cn-number-error').textContent = 'Enter a customer number like CUS-12345.'; return; }
    el('number-submit').disabled = true;
    try {
      await window.evidenceApp.api('/api/forms/submit', {form_id: numberForm, customer_number: value});
      numberForm = null; numberDialog.close();
    } catch(error) {
      el('cn-number-error').textContent = (error.errors || {}).customer_number || (error.errors || {}).form || error.message;
      el('number-submit').disabled = false;
    }
  });
  el('cn-number').addEventListener('input', () => {  // Once per box: the agent stops talking over someone typing.
    if(!numberForm || typingSent === numberForm) return;
    typingSent = numberForm;
    window.evidenceApp.api('/api/forms/typing', {form_id: numberForm}).catch(() => {});
  });
  el('number-cancel').addEventListener('click', () => closeNumber('cancelled'));
  el('number-close').addEventListener('click', () => closeNumber('closed'));
  numberDialog.addEventListener('cancel', event => { event.preventDefault(); closeNumber('closed'); });

  function setUserText(item, id, text, final) {
    const bubble = item.querySelector('.bubble');
    let span = [...bubble.children].find(child => child.dataset.segment === id);
    if(!span) { span = document.createElement('span'); span.dataset.segment = id; bubble.append(span); }
    span.replaceChildren();
    text.trim().split('[REDACTED]').forEach((part, i) => {
      if(i) { const m = document.createElement('span'); m.className = 'mask'; m.textContent = 'masked'; span.append(m); }
      span.append(part);
    });
    if(span.previousElementSibling) span.prepend(' ');
    span.classList.toggle('interim', !final);
    item.classList.toggle('interim', !!bubble.querySelector('.interim'));
  }
  async function transcription(reader, participant, connection, valid) {
    const attrs = reader.info.attributes || {};
    const id = attrs['lk.segment_id'] || reader.info.id;
    const user = participant.identity === connection.localParticipant.identity;
    const item = message(id, user ? 'user' : 'agent');
    if(user) {
      const text = await reader.readAll();
      if(!valid()) return;
      setUserText(item, id, text, attrs['lk.transcription_final'] === 'true');
    } else {
      for await (const chunk of reader) { if(!valid()) return; item.querySelector('.bubble').append(chunk); scroll(); }
    }
    scroll();
  }

  function lock(value) {
    busy = value;
    el('start-live').disabled = value; el('start-live').hidden = value;
    el('end-live').disabled = !value; el('end-live').hidden = !value;
    window.dispatchEvent(new CustomEvent('live-voice-active', {detail: value}));
  }
  function end(message = '') {
    generation++;
    clearTimeout(connectTimer); clearTimeout(limitTimer);
    microphone?.stop(); microphone = null;
    const old = room; room = null; ready = false;
    old?.disconnect();
    el('live-audio').replaceChildren();
    el('enable-live-audio').hidden = true;
    meters.user = meters.agent = null; pending = {}; currentAgent = null;
    closeForm(activeForm ? 'closed' : null);
    closeNumber(numberForm ? 'closed' : null);
    setMode('idle'); lock(false); status(message);
  }
  async function start() {
    if(busy) return;
    lock(true);
    try { audioContext ??= new AudioContext(); } catch {}  // Created inside the click so the browser allows audio.
    const current = ++generation;
    segments.clear(); el('transcript').replaceChildren(); setAuth(null);
    setMode('connecting'); status('');
    connectTimer = setTimeout(() => { if(current === generation) end('The agent did not become ready. Check the launch terminal, then retry.'); },45000);
    try {
      const auth = await window.evidenceApp.api('/api/live-token', {});
      if(current !== generation) return;
      const track = await sdk.createLocalAudioTrack({echoCancellation:true,noiseSuppression:true,autoGainControl:true});
      if(current !== generation) { track.stop(); return; }
      microphone = track;
      const connection = new sdk.Room({adaptiveStream:true,dynacast:true});
      room = connection;
      const valid = () => current === generation && room === connection;
      const isAgent = participant => participant?.kind === sdk.ParticipantKind.AGENT;
      async function checkReady() {
        if(!valid() || ready) return;
        const agent = [...connection.remoteParticipants.values()].find(p => isAgent(p) && p.attributes['evidence.ready'] === 'true');
        if(!agent) return;
        ready = true;
        try {
          await connection.localParticipant.publishTrack(track);
          if(!valid()) return;
          clearTimeout(connectTimer);
          if(track.mediaStreamTrack) meters.user = meter(track.mediaStreamTrack);
          limitTimer = setTimeout(() => end('Ten-minute limit reached.'),auth.max_session_seconds*1000);
          setMode('listening');
        } catch { if(valid()) end('Microphone publishing failed. Check permission and retry.'); }
      }
      connection.registerTextStreamHandler?.('lk.transcription', (reader, participant) => {
        if(valid()) transcription(reader, participant, connection, valid).catch(() => {});
      });
      connection.on(sdk.RoomEvent.ParticipantAttributesChanged,checkReady);
      connection.on(sdk.RoomEvent.ParticipantConnected,checkReady);
      connection.on(sdk.RoomEvent.ParticipantDisconnected,p => { if(valid() && isAgent(p)) end('The agent disconnected. Start a new call to retry.'); });
      connection.on(sdk.RoomEvent.Disconnected,() => { if(valid()) end('Call ended.'); });
      connection.on(sdk.RoomEvent.Reconnecting,() => { if(valid()) status('Reconnecting…'); });
      connection.on(sdk.RoomEvent.Reconnected,() => { if(valid()) status(''); });
      connection.on(sdk.RoomEvent.TrackSubscribed,(remoteTrack, publication, participant) => {
        if(!valid() || !isAgent(participant) || remoteTrack.kind !== sdk.Track.Kind.Audio) return;
        el('live-audio').append(remoteTrack.attach());
        // The avatar follows the voice; the faint office and typing track is heard but not metered.
        if(remoteTrack.mediaStreamTrack && publication.trackName !== 'background_audio') meters.agent = meter(remoteTrack.mediaStreamTrack);
      });
      connection.on(sdk.RoomEvent.TrackUnsubscribed,track => track.detach().forEach(node => node.remove()));
      connection.on(sdk.RoomEvent.AudioPlaybackStatusChanged,() => { if(valid()) el('enable-live-audio').hidden = connection.canPlaybackAudio; });
      connection.on(sdk.RoomEvent.DataReceived,(bytes, participant, kind, topic) => {
        if(!valid() || !isAgent(participant) || topic !== 'evidence') return;
        try {
          const event = JSON.parse(new TextDecoder().decode(bytes));
          if(event.type === 'agent_state' && ready) setMode(event.state === 'thinking' || event.state === 'speaking' ? event.state : 'listening');
          if(event.type === 'user_state' && event.state === 'speaking') { pending = {}; currentAgent = null; status(''); }
          if(event.type === 'sources') attach({sources: event.sources});
          if(event.type === 'case') attach({cases: [{kind: event.kind, ref: event.ref}]});
          if(event.type === 'auth') setAuth(event.state);
          if(event.type === 'form' && event.action === 'open' && FORMS[event.kind]) openForm(event.form_id, event.kind);
          if(event.type === 'form' && event.action === 'open' && event.kind === 'customer_number') openNumber(event.form_id, event.notice);
          if(event.type === 'form' && event.action === 'close' && event.form_id === numberForm) closeNumber(null);
          if(event.type === 'guard') attach({guard: event.reason});
          if(event.type === 'latency') attach({latency: event.ms});
          if(event.type === 'error') status(event.message);
        } catch { status('A live update could not be displayed.'); }
      });
      await connection.connect(auth.url, auth.token);
      if(!valid()) { await connection.disconnect(); return; }
      await audioContext?.resume();
      await connection.startAudio();
      await checkReady();
    } catch(error) {
      if(current === generation) end(error.name === 'NotAllowedError' ? 'Microphone permission denied. Allow access and retry.' :
        (error.message?.startsWith('Start scripts/') ? error.message : 'The call could not connect. Check configuration, network and microphone permission.'));
    }
  }
  el('start-live').addEventListener('click',start);
  el('end-live').addEventListener('click',() => end());
  el('enable-live-audio').addEventListener('click',() => room?.startAudio().catch(() => status('Allow audio playback in your browser, then try again.')));
  const serviceStatus = detail => { if(!busy) status(detail.live_voice?.configured ? '' : 'Not configured. Run python3 scripts/start_live.py --port 8766'); };
  window.addEventListener('service-status',event => serviceStatus(event.detail));
  if(window.serviceStatus) serviceStatus(window.serviceStatus);
  document.querySelectorAll('.nav').forEach(button => button.addEventListener('click',() => { if(button.dataset.view !== 'voice' && busy) end(); }));
  el('reset').addEventListener('click',() => { if(busy) end(); });
  window.addEventListener('pagehide',() => { if(busy) end(); });
})();
