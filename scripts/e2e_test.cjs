// End-to-end check of the site as a person uses it, in a real browser, against a running server.py (README option A).
//   npm install --prefix .runtime playwright && npx --prefix .runtime playwright install chromium
//   python3 server.py & node scripts/e2e_test.cjs
// TEST_BASE_URL points it at another server; PW_CHANNEL=chrome uses the installed Chrome instead of Playwright's Chromium.
const {chromium} = require('../.runtime/node_modules/playwright');
const assert = require('node:assert/strict');
const path = require('node:path');
const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8765';
const ROOT = path.resolve(__dirname, '..');

(async () => {
  const browser = await chromium.launch({channel: process.env.PW_CHANNEL, headless: true});
  const checks = [], problems = [];
  const check = async (name, fn) => {
    try { await fn(); checks.push(name); console.log('ok    ' + name); }
    catch(e) { problems.push(name); console.log('FAIL  ' + name + '\n      ' + e.message.split('\n').slice(0, 8).join('\n      ')); }
  };
  const open = async (url, viewport = {width: 1440, height: 1000}) => {
    const page = await browser.newPage({viewport});
    page.errors = []; page.failed = [];  // Failed requests are tracked by URL; the browser's matching console line adds nothing.
    page.on('pageerror', e => page.errors.push(e.message));
    page.on('console', m => { if(m.type() === 'error' && !m.text().startsWith('Failed to load resource')) page.errors.push(m.text()); });
    page.on('response', r => { if(r.status() >= 400) page.failed.push(r.status() + ' ' + new URL(r.url()).pathname); });
    await page.goto(url);
    return page;
  };
  const visible = (page, selector) => page.locator(selector).waitFor({state: 'visible', timeout: 5000});
  const noOverflow = async page => assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'page scrolls sideways');

  const page = await open(base + '/');

  await check('landing page opens on the call, with the demo customers and test cards loaded', async () => {
    assert.equal(await page.title(), 'Voice Assistant · Prototype');
    await visible(page, '#voice');
    assert.equal(await page.locator('#start-live').innerText(), 'Start call');
    assert.equal(await page.locator('.nav[aria-current="page"]').innerText(), 'Call');
    await page.locator('.customer').nth(1).waitFor();
    assert.deepEqual(await page.locator('.customer summary .muted').allInnerTexts(), ['CUS-10482', 'CUS-20917']);
    assert.deepEqual(await page.locator('#test-cards button').allInnerTexts(), ['Balance', 'Hardship', 'Dispute', 'Loan']);
  });

  await check('a test card opens its customer record, and only one record is open at a time', async () => {
    await page.getByRole('button', {name: 'Balance'}).click();
    const alice = page.locator('.customer[data-customer="CUS-10482"]'), minji = page.locator('.customer[data-customer="CUS-20917"]');
    await visible(page, '.customer[data-customer="CUS-10482"] .customer-body');
    assert.match(await alice.innerText(), /Alice[\s\S]*Accounts[\s\S]*\$[\d,]+\.\d\d[\s\S]*Recent transactions/);
    await page.getByRole('button', {name: 'Hardship'}).click();
    await visible(page, '.customer[data-customer="CUS-20917"] .customer-body');
    await page.waitForFunction(  // The toggle event that closes the other record fires just after the click.
() => !document.querySelector('.customer[data-customer="CUS-10482"]').open, null, {timeout: 3000});
    assert.match(await minji.innerText(), /Minji[\s\S]*Loans/);
  });

  await check('clicking a customer name opens and closes the record', async () => {
    const summary = page.locator('.customer[data-customer="CUS-10482"] summary');
    await summary.click();
    assert.equal(await page.locator('.customer[data-customer="CUS-10482"]').evaluate(d => d.open), true);
    await summary.click();
    assert.equal(await page.locator('.customer[data-customer="CUS-10482"]').evaluate(d => d.open), false);
  });

  await check('Customers, Session and Cases tabs switch panels', async () => {
    await page.getByRole('tab', {name: 'Session'}).click();
    await page.locator('#panel-session', {hasText: /Start a call to see verification|Identity verification/}).waitFor({timeout: 5000});  // After a call, its checks.
    assert.equal(await page.locator('#panel-customers').isHidden(), true);
    await page.getByRole('tab', {name: 'Cases'}).click();
    await page.locator('#panel-cases', {hasText: /Cases and callback requests created during calls appear here|Ref/}).waitFor({timeout: 5000});
    await page.getByRole('tab', {name: 'Customers'}).click();
    await visible(page, '#panel-customers');
    assert.equal(await page.getByRole('tab', {name: 'Customers'}).getAttribute('aria-selected'), 'true');
  });

  await check('Reset data asks first: cancel changes nothing, confirm reloads the records', async () => {
    let resets = 0;
    page.on('request', r => { if(r.url().endsWith('/api/demo/reset')) resets++; });
    page.once('dialog', d => { assert.match(d.message(), /Reset all demo customer data/); d.dismiss(); });
    await page.getByRole('button', {name: 'Reset data'}).click();
    await page.waitForTimeout(300);
    assert.equal(resets, 0);
    page.once('dialog', d => d.accept());
    const reloaded = page.waitForResponse(r => r.url().endsWith('/api/demo/overview'));
    await page.getByRole('button', {name: 'Reset data'}).click();
    assert.equal((await reloaded).status(), 200);
    assert.equal(resets, 1);
    assert.equal(await page.locator('.customer').count(), 2);
  });

  await check('without live keys, Start call explains how to start the voice agent and never asks for the microphone', async () => {
    await page.evaluate(() => { window.__micAsked = false; const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
      navigator.mediaDevices.getUserMedia = (...a) => { window.__micAsked = true; return real(...a); }; });
    await page.getByRole('button', {name: 'Start call'}).click();
    await page.waitForFunction(() => /start_live\.py/.test(document.querySelector('#live-status').textContent), null, {timeout: 5000});
    assert.equal(await page.locator('#end-live').isHidden(), true);
    assert.equal(await page.locator('#start-live').isVisible(), true);
    assert.equal(await page.evaluate(() => window.__micAsked), false);
    assert.deepEqual(page.failed, ['400 /api/live-token']);  // The server's "not configured" answer.
  });

  await check('Security page shows the reply checks, backend duties, agent limits and test counts', async () => {
    await page.getByRole('button', {name: 'Security', exact: true}).click();
    await visible(page, '#security');
    assert.equal(await page.locator('#voice').isHidden(), true);
    assert.equal(await page.locator('.nav[aria-current="page"]').innerText(), 'Security');
    assert.deepEqual(await page.locator('.flow li strong').allInnerTexts(), ['Caller', 'Redact', 'Injection check', 'Backend', 'AI agent', 'Fact check', 'Voice']);
    for(const h of ['How each reply is checked', 'What the backend handles', 'What the AI agent is not allowed to do', 'What the AI agent can do', 'Testing'])
      assert.ok(await page.getByRole('heading', {name: h}).isVisible(), h);
    assert.deepEqual(await page.locator('.stats dd').allInnerTexts(), ['188', '70', '16']);
  });

  await check('Knowledge page lists the source files; a file opens its chunks with safe source links and the raw file', async () => {
    await page.getByRole('button', {name: 'Knowledge', exact: true}).click();
    await page.locator('.file-card').first().waitFor();
    const files = await page.locator('.file-card').count();
    assert.ok(files >= 10, files + ' files');
    assert.match(await page.locator('.file-card .file-meta').first().innerText(), /^Fetched \d{1,2} \w{3} \d{4} · \d+ chunks$/);
    const title = await page.locator('.file-card h2').first().innerText();
    await page.locator('.file-card').first().click();
    await visible(page, '#knowledge-file');
    assert.equal(await page.locator('.file-title').innerText(), title);
    assert.ok(await page.locator('.chunk-card').count() > 0);
    const links = await page.locator('.chunk-card a[href]').evaluateAll(as => as.map(a => [a.href, a.target, a.rel]));
    assert.ok(links.length > 0);
    for(const [href, target, rel] of links) { assert.match(href, /^https:\/\//); assert.equal(target, '_blank'); assert.equal(rel, 'noopener noreferrer'); }
    await page.getByRole('tab', {name: 'Raw file'}).click();
    assert.match(await page.locator('.raw-file').innerText(), /^# /);
    await page.getByRole('button', {name: 'All files'}).click();
    await visible(page, '#knowledge-list');
    assert.equal(await page.locator('.file-card').count(), files);
  });

  await check('Call tab returns to the call with the panel state intact', async () => {
    await page.getByRole('button', {name: 'Call', exact: true}).click();
    await visible(page, '#voice');
    assert.equal(await page.locator('.customer').count(), 2);
  });

  await check('the main navigation works from the keyboard', async () => {
    await page.locator('.nav[data-view="security"]').focus();
    await page.keyboard.press('Enter');
    await visible(page, '#security');
    await page.keyboard.press('Tab');
    await page.keyboard.press('Enter');
    await visible(page, '#knowledge');
  });

  await check('deep links open the named page', async () => {
    for(const view of ['security', 'knowledge']) {
      const p = await open(base + '/#' + view);
      await visible(p, '#' + view);
      assert.equal(await p.locator('#voice').isHidden(), true);
      assert.deepEqual(p.errors, []);
      await p.close();
    }
  });

  await check('at phone width every page fits without sideways scrolling', async () => {
    const p = await open(base + '/', {width: 390, height: 844});
    await p.locator('.customer').nth(1).waitFor();
    await noOverflow(p);
    await p.getByRole('button', {name: 'Balance'}).click();
    await noOverflow(p);
    for(const view of ['Security', 'Knowledge']) {
      await p.getByRole('button', {name: view, exact: true}).click();
      await visible(p, '#' + view.toLowerCase());
      await noOverflow(p);
    }
    await p.locator('.file-card').first().click();
    await noOverflow(p);
    assert.deepEqual(p.errors, []);
    await p.close();
  });

  await check('the architecture, tool-calling and safeguard diagrams open from disk without errors', async () => {
    for(const file of ['architecture.html', 'tool-calling.html', 'tool-safeguards.html']) {
      const p = await open('file://' + path.join(ROOT, file));
      await p.waitForLoadState('load');
      assert.ok((await p.locator('body').innerText()).trim().length > 0, file + ' is empty');
      assert.deepEqual(p.errors, [], file);
      await p.close();
    }
  });

  await check('no script or console errors, and no failed requests other than the expected one', async () => {
    assert.deepEqual(page.errors, []);
    assert.deepEqual(page.failed, ['400 /api/live-token']);
  });

  await browser.close();
  console.log(`\n${checks.length}/${checks.length + problems.length} checks passed`);
  process.exit(problems.length ? 1 : 0);
})();
