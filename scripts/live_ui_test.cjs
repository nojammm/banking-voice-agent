const {chromium}=require('../.runtime/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base=process.env.TEST_BASE_URL||'http://127.0.0.1:8766';
(async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1100}});
 const errors=[],checks=[]; page.on('pageerror',e=>errors.push(e.message));
 try {
  await page.goto(base+'/');
  await page.locator('#start-live').waitFor();
  assert.equal(await page.locator('#voice').isVisible(),true);
  assert.equal(await page.evaluate(()=>LivekitClient.ParticipantKind.AGENT),4);
  await page.locator('#start-live').click();
  await page.waitForFunction(()=>document.querySelector('#live-status').textContent.includes('Start scripts/start_live.py'));
  assert.equal(await page.locator('#end-live').isHidden(),true);
  checks.push('call is the landing view; real browser SDK loads; unconfigured session fails before microphone permission');
  const fake=`
   window.__stopped=0; window.__published=0;
   class Room {
    constructor(){this.handlers={};this.streams={};this.remoteParticipants=new Map();this.localParticipant={identity:'customer-1',publishTrack:async()=>{window.__published++}};window.__room=this;}
    on(n,f){(this.handlers[n]??=[]).push(f);return this;}
    emit(n,...args){for(const f of this.handlers[n]||[])f(...args);}
    registerTextStreamHandler(topic,f){this.streams[topic]=f;}
    async connect(){if(window.__failConnect)throw Error('offline');}
    async startAudio(){}
    async disconnect(){this.emit('disconnect');}
   }
   window.__say=(identity,segment,chunks,final)=>window.__room.streams['lk.transcription']({
     info:{id:segment+Math.random(),attributes:{'lk.segment_id':segment,'lk.transcription_final':final?'true':'false'}},
     readAll:async()=>chunks.join(''),
     async*[Symbol.asyncIterator](){for(const c of chunks)yield c;}
   },{identity});
   window.__data=e=>window.__room.emit('data',new TextEncoder().encode(JSON.stringify(e)),{kind:4},null,'evidence');
   window.LivekitClient={Room,ParticipantKind:{AGENT:4},Track:{Kind:{Audio:'audio'}},RoomEvent:{ParticipantAttributesChanged:'attrs',ParticipantConnected:'joined',ParticipantDisconnected:'left',Disconnected:'disconnect',Reconnecting:'reconnecting',Reconnected:'reconnected',TrackSubscribed:'track',TrackUnsubscribed:'untrack',AudioPlaybackStatusChanged:'audio',DataReceived:'data'},createLocalAudioTrack:async()=>{if(window.__delayMic)await new Promise(r=>window.__resolveMic=r);return{stop(){window.__stopped++}}}};
  `;
  await page.route('**/livekit-client.js',route=>route.fulfill({contentType:'application/javascript',body:fake}));
  await page.route('**/api/live-token',route=>route.fulfill({json:{url:'wss://test.livekit.cloud',token:'synthetic',max_session_seconds:600}}));
  await page.reload();
  await page.locator('#start-live').click();
  await page.waitForFunction(()=>window.__room);
  assert.equal(await page.evaluate(()=>window.__published),0);
  assert.equal(await page.locator('#live-state').innerText(),'Connecting');
  await page.evaluate(()=>{const p={kind:4,attributes:{'evidence.ready':'true'}};__room.remoteParticipants.set('agent',p);__room.emit('attrs',{},p)});
  await page.waitForFunction(()=>window.__published===1);
  assert.equal(await page.locator('#live-state').innerText(),'Listening');
  checks.push('microphone is published only after the agent is ready');

  await page.evaluate(()=>__say('agent-1','SG_greet',['Hi, you are ','speaking with an AI.'],false));
  await page.evaluate(()=>__say('customer-1','SG_u1',['I lost my'],false));
  assert.equal(await page.locator('.msg.user.interim .bubble').innerText(),'I lost my');
  await page.evaluate(()=>__say('customer-1','SG_u0',['Hi.'],true));
  await page.evaluate(()=>__say('customer-1','SG_u1',['I lost my card, number [REDACTED]'],true));
  assert.equal(await page.locator('.msg.user').count(),1);
  assert.equal(await page.locator('.msg.user .bubble').innerText(),'I lost my card, number masked Hi.');
  assert.equal(await page.locator('.msg.user.interim').count(),0);
  assert.equal(await page.locator('.msg.user .mask').innerText(),'masked');
  checks.push('customer speech streams live on the right, finalises in place and stays one bubble per turn; redactions render as masks');
  await page.evaluate(()=>{__data({type:'agent_state',state:'thinking'});__data({type:'sources',sources:[{title:'Lock and unlock your card',url:'https://www.westpac.com.au/personal-banking/online-banking/making-the-most/lock-card/'}]});__data({type:'latency',ms:1240});});
  assert.equal(await page.locator('#live-state').innerText(),'Thinking');
  await page.evaluate(()=>{__data({type:'agent_state',state:'speaking'});__say('agent-1','SG_a1',['Lock it ','in the app.'],false);});
  await page.waitForFunction(()=>document.querySelectorAll('.msg.agent').length===2);
  const reply=page.locator('.msg.agent').last();
  assert.equal(await reply.locator('.bubble').innerText(),'Lock it in the app.');
  assert.equal(await reply.locator('a').getAttribute('href'),'https://www.westpac.com.au/personal-banking/online-banking/making-the-most/lock-card/');
  assert.equal(await reply.locator('.latency').innerText(),'1.2 s');
  assert.equal(await page.locator('.msg.agent').first().locator('a').count(),0);
  await page.evaluate(()=>{__data({type:'user_state',state:'speaking'});__data({type:'guard',reason:'permission_denied'});__say('customer-1','SG_u2',['Lock it for me'],true);__say('agent-1','SG_a2',['I can’t make changes here.'],false);});
  await page.waitForFunction(()=>document.querySelectorAll('.msg.agent').length===3);
  assert.equal(await page.locator('.msg.agent').last().locator('.guard').innerText(),'Account action blocked');
  assert.deepEqual(await page.locator('.msg').evaluateAll(n=>n.map(m=>m.classList.contains('user')?'user':'agent')),['agent','user','agent','user','agent']);
  assert.equal(await page.locator('#live-state').innerText(),'Speaking');
  checks.push('agent replies stream on the left with their sources, response time and code-enforced guard marker');

  await page.waitForFunction(()=>document.querySelectorAll('#customer-cards .customer').length===2);
  assert.equal(await page.locator('#test-cards .test-card').count(),4);
  await page.locator('.test-card button',{hasText:'Hardship'}).click();
  assert.equal(await page.locator('.customer[data-customer="CUS-20917"]').evaluate(n=>n.open),true);
  assert.equal(await page.locator('.customer[data-customer="CUS-10482"]').evaluate(n=>n.open),false);
  checks.push('inspector shows both mock customers from the backend and a test card opens the matching record');
  await page.evaluate(()=>__data({type:'auth',state:'in_progress',step:'full name'}));
  assert.equal(await page.locator('#auth-state').innerText(),'Verifying identity');
  await page.evaluate(()=>{__data({type:'auth',state:'verified',step:null});__data({type:'case',kind:'complaint',ref:'CMP-1234'});});
  assert.equal(await page.locator('#auth-state').innerText(),'Identity verified');
  assert.equal(await page.locator('.msg.agent').last().locator('.case-chip').innerText(),'Complaint CMP-1234');
  checks.push('identity status and created case references appear for the customer, with no identity values');
  const posted=[];
  await page.route('**/api/forms/*',route=>{const body=JSON.parse(route.request().postData());posted.push([route.request().url().split('/').pop(),body]);
    if(route.request().url().endsWith('/submit')&&posted.filter(p=>p[0]==='submit').length===1) return route.fulfill({status:400,json:{errors:{phone:'Enter an Australian mobile or landline number.'}}});
    return route.fulfill({json:route.request().url().endsWith('/submit')?{reference:'CBK-4321',agent_notified:true}:{closed:true,agent_notified:true}});});
  const formId='F'+'a'.repeat(16);
  await page.evaluate(id=>__data({type:'form',action:'open',form_id:id,kind:'loan_callback',purpose:'home_loan'}),formId);
  await page.locator('#callback-dialog').waitFor();
  await page.locator('#callback-submit').click();
  assert.equal(await page.locator('#cb-consent-error').innerText(),'Consent is required for a callback.');
  assert.equal(posted.length,0);
  await page.fill('#cb-name','Alice Wonderland'); await page.fill('#cb-phone','0491 570 006'); await page.check('#cb-consent');
  await page.locator('#callback-submit').click();
  await page.waitForFunction(()=>document.querySelector('#cb-phone-error').textContent.length>0);
  assert.equal(await page.locator('#callback-dialog').isVisible(),true);
  await page.locator('#callback-submit').click();
  await page.waitForFunction(()=>!document.querySelector('#callback-dialog').open);
  assert.deepEqual(posted.map(p=>p[0]),['submit','submit']);
  assert.deepEqual(posted[1][1],{form_id:formId,name:'Alice Wonderland',phone:'0491 570 006',consent:true});
  checks.push('agent-opened callback form validates locally, shows backend errors, and closes only after the backend saves it');
  await page.evaluate(id=>__data({type:'form',action:'open',form_id:id,kind:'loan_callback',purpose:'home_loan'}),'F'+'b'.repeat(16));
  await page.locator('#callback-dialog').waitFor(); await page.keyboard.press('Escape');
  await page.evaluate(id=>__data({type:'form',action:'open',form_id:id,kind:'loan_callback',purpose:'refinance'}),'F'+'c'.repeat(16));
  await page.locator('#callback-cancel').click();
  await page.waitForFunction(()=>!document.querySelector('#callback-dialog').open);
  await page.waitForTimeout(100);
  assert.deepEqual(posted.slice(2).map(p=>[p[0],p[1].reason]),[['close','closed'],['close','cancelled']]);
  checks.push('closing or cancelling the form reports the real outcome to the backend');
  await page.screenshot({path:'evaluations/live-call-desktop.png',fullPage:true});

  await page.locator('#end-live').click();
  assert.equal(await page.evaluate(()=>window.__stopped),1);
  assert.equal(await page.locator('#start-live').isVisible(),true);
  assert.equal(await page.locator('.msg').count(),5);
  checks.push('ending the call releases the microphone and keeps the transcript for review');
  await page.evaluate(()=>window.__failConnect=true);
  await page.locator('#start-live').click();
  await page.waitForFunction(()=>window.__stopped===2);
  assert.equal(await page.locator('#start-live').isEnabled(),true);
  checks.push('connection failure cleans up acquired microphone');
  await page.evaluate(()=>{window.__failConnect=false;window.__delayMic=true});
  await page.locator('#start-live').click();
  await page.waitForFunction(()=>window.__resolveMic);
  await page.locator('#end-live').click();
  await page.evaluate(()=>window.__resolveMic());
  await page.waitForFunction(()=>window.__stopped===3);
  assert.equal(await page.evaluate(()=>window.__published),1);
  checks.push('cancellation while permission is pending stops late microphone without publishing');
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:'evaluations/live-voice-mobile.png',fullPage:true});
  checks.push('call view fits 390px viewport');
  assert.deepEqual(errors,[]);
  const result={executed_at:new Date().toISOString(),passed:true,checks,console_errors:errors,limits:'Mock LiveKit transport and provider boundaries; no live cloud call or measured audio latency.'};
  fs.writeFileSync('evaluations/live-ui-results.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
