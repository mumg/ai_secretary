const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '../../backend/web');
const settle = () => new Promise(resolve => setTimeout(resolve, 20));

test('model settings save, preserve and remove API key without echoing it', async t => {
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'index.html'), 'utf8'), {
    url: 'http://localhost/admin', runScripts: 'outside-only',
  });
  t.after(() => dom.window.close());
  const w = dom.window;
  w.structuredClone = structuredClone;
  let state = {
    llm_api_key_configured: true, firebase_configured: false,
    settings: {
      identity: { names: [] }, server: { timezone: 'UTC', public_url: 'https://example.test' },
      calendar: { workday_start: '10:00', workday_end: '17:00', daily_plan_time: '08:00' },
      communication_sources: { initial_sync_days: 30 },
      llm: { base_url: 'http://ollama:11434', model: 'qwen', context_length: 16384,
        temperature: 0.1, auto_create_confidence: 0.85, possible_completion_confidence: 0.8,
        request_timeout_seconds: 300 },
      worker: { batch_size: 10, ranking_interval_seconds: 900, poll_interval_seconds: 60 },
      notifications: { due_soon_minutes: 60, overdue_repeat_hour: 10 },
      document_parser: { max_bytes: 1048576, max_characters: 10000, timeout_seconds: 30 },
    },
  };
  const writes = [];
  w.fetch = async (url, options = {}) => {
    if (url.endsWith('/settings')) {
      if (options.method === 'PUT') {
        const body = JSON.parse(options.body);
        writes.push(body);
        state = { ...state, settings: body.settings,
          llm_api_key_configured: body.clear_llm_api_key ? false : Boolean(body.llm_api_key || state.llm_api_key_configured) };
      }
      return { ok: true, json: async () => structuredClone(state) };
    }
    return { ok: true, json: async () => url.endsWith('/status') ? {} : [] };
  };
  w.eval(fs.readFileSync(path.join(root, 'assets/admin.js'), 'utf8'));
  await settle();
  const el = id => w.document.getElementById(id);
  assert.equal(el('llmApiKey').type, 'password');
  assert.equal(el('llmApiKey').value, '');
  assert.equal(el('llmKeyState').textContent, 'Ключ сохранён');
  assert.equal(el('llmProvider').value, 'ollama');
  el('llmProvider').value = 'openai';
  el('llmUrl').value = 'https://model.example.test/v1';
  el('llmApiKey').value = 'test-key-only';
  await w.saveSettings();
  assert.equal(writes[0].settings.llm.provider, 'openai');
  assert.equal(writes[0].llm_api_key, 'test-key-only');
  assert.equal(el('llmApiKey').value, '');
  assert.equal(w.localStorage.length, 0);
  assert.equal(JSON.stringify(state.settings).includes('test-key-only'), false);
  await w.saveSettings();
  assert.equal(writes[1].llm_api_key, null);
  assert.equal(el('llmKeyState').textContent, 'Ключ сохранён');
  el('clearLlmApiKey').checked = true;
  await w.saveSettings();
  assert.equal(writes[2].clear_llm_api_key, true);
  assert.equal(el('clearLlmApiKey').checked, false);
  assert.equal(el('llmKeyState').textContent, 'Ключ не настроен');
  state.local_web_only = true;
  w.populateSettings(structuredClone(state));
  assert.equal(el('firebaseSettings').hidden, true);
  assert.equal(el('devicesMetric').hidden, true);
  assert.equal(el('publicUrl').disabled, true);
  assert.equal(el('localWebNotice').hidden, false);
  assert.match(el('deploymentHint').textContent, /Локальная установка/);
  state.local_web_only = false;
  w.populateSettings(structuredClone(state));
  assert.equal(el('firebaseSettings').hidden, false);
  assert.equal(el('publicUrl').disabled, false);
});

test('mobile QR is requested explicitly, never persisted, and erased on leaving the tab', async t => {
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'index.html'), 'utf8'), {
    url: 'https://example.test/admin', runScripts: 'outside-only', pretendToBeVisual: true,
  });
  t.after(() => dom.window.close());
  const w = dom.window;
  let issueCount = 0;
  w.fetch = async (url, options = {}) => {
    if (url.endsWith('/mobile-identity')) {
      issueCount++;
      assert.equal(options.method, 'POST');
      assert.equal(options.cache, 'no-store');
      assert.deepEqual(JSON.parse(options.body), {});
      return { ok: true, json: async () => ({ qr_image: 'data:image/png;base64,dGVzdA==', server_url: 'https://example.test', expires_at: '2027-09-18T12:00:00Z' }) };
    }
    return { ok: true, json: async () => url.endsWith('/settings') ? {settings: {}} : [] };
  };
  w.eval(fs.readFileSync(path.join(root, 'assets/admin.js'), 'utf8'));
  const el = id => w.document.getElementById(id);
  await settle();
  assert.equal(issueCount, 0);
  el('mobileTab').click();
  assert.equal(el('mobileDeviceLabel'), null);
  assert.equal(el('createMobileIdentity').textContent, 'Показать QR-код');
  await w.createMobileIdentity({ preventDefault() {} });
  assert.equal(issueCount, 1);
  assert.equal(el('mobileIdentityResult').hidden, false);
  assert.ok(el('mobileIdentityQR').src.startsWith('data:image/png;base64,'));
  // The browser must preserve integer QR module widths at desktop and mobile sizes.
  const qr = el('mobileIdentityQR');
  Object.defineProperty(qr, 'naturalWidth', {value: 774});
  Object.defineProperty(qr.parentElement, 'clientWidth', {value: 620, configurable: true});
  qr.dispatchEvent(new w.Event('load'));
  assert.equal(qr.style.width, '516px');
  Object.defineProperty(qr.parentElement, 'clientWidth', {value: 360});
  w.dispatchEvent(new w.Event('resize'));
  assert.equal(qr.style.width, '258px');
  assert.equal(w.localStorage.length, 0);
  assert.equal(w.sessionStorage.length, 0);
  w.document.querySelector('[data-panel="sources"]').click();
  assert.equal(el('mobileIdentityResult').hidden, true);
  assert.equal(el('mobileIdentityQR').getAttribute('src'), null);
  assert.equal(el('mobileIdentityInfo').textContent, '');
  // A response arriving after the user hides the QR must never display the key.
  el('mobileTab').click();
  const pending = w.createMobileIdentity({ preventDefault() {} });
  w.hideMobileIdentity();
  await pending;
  assert.equal(el('mobileIdentityResult').hidden, true);
});

test('connection tabs isolate direct settings, identify gateway QR and clear late secrets', async t => {
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'index.html'), 'utf8'), {
    url: 'http://localhost/admin', runScripts: 'outside-only', pretendToBeVisual: true,
  });
  t.after(() => dom.window.close());
  const w = dom.window;
  const el = id => w.document.getElementById(id);
  let qrCount = 0;
  const writes = [];
  let releaseQR;
  w.fetch = async (url, options = {}) => {
    if (url.endsWith('/gateway')) return { ok: true, json: async () => ({state: 'connected', gateway: 'https://gateway.test', installation_id: 'test-uid', qr_available: true, enabled: true}) };
    if (url.endsWith('/gateway/qr')) {
      qrCount++;
      assert.equal(options.cache, 'no-store');
      await new Promise(resolve => { releaseQR = resolve; });
      return { ok: true, json: async () => ({ qr_image: 'data:image/png;base64,Z2F0ZXdheQ==' }) };
    }
    if (url.endsWith('/settings') && options.method === 'PUT') {
      writes.push(JSON.parse(options.body));
      return { ok: true, json: async () => ({settings: {server: {public_url: 'https://direct.test'}}, firebase_configured: true}) };
    }
    return { ok: true, json: async () => url.endsWith('/settings') ? {settings: {server: {public_url: 'https://old.test'}}} : [] };
  };
  w.eval(fs.readFileSync(path.join(root, 'assets/admin.js'), 'utf8'));
  await settle();
  assert.ok(el('directConnection').contains(el('publicUrl')));
  assert.ok(el('directConnection').contains(el('firebaseSettings')));
  assert.equal(el('notifications').contains(el('firebaseSettings')), false);
  el('publicUrl').value = 'https://direct.test';
  el('firebaseJson').value = '{"project_id":"test"}';
  await w.saveDirectSettings({preventDefault() {}});
  assert.deepEqual(writes[0], {settings: {server: {public_url: 'https://direct.test'}}, firebase_credentials_json: '{"project_id":"test"}'});
  assert.equal(el('firebaseJson').value, '');
  el('mobileTab').click();
  el('gatewayConnectionTab').click();
  await settle();
  assert.equal(qrCount, 0);
  assert.equal(el('gatewayConnection').hidden, false);
  assert.equal(el('directConnection').hidden, true);
  assert.equal(el('gatewayShowQR').disabled, false);
  el('gatewayShowQR').click();
  await settle();
  el('directConnectionTab').click();
  releaseQR();
  await settle();
  assert.equal(el('gatewayQRResult').hidden, true);
  assert.equal(el('gatewayQR').getAttribute('src'), null);
  assert.equal(w.localStorage.length, 0);
  assert.equal(w.sessionStorage.length, 0);
});

test('gateway enrolls without invitation and rotates the identity, clearing the previous QR', async t => {
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'index.html'), 'utf8'), {
    url: 'http://localhost/admin', runScripts: 'outside-only', pretendToBeVisual: true,
  });
  t.after(() => dom.window.close());
  const w = dom.window;
  const el = id => w.document.getElementById(id);
  const writes = [];
  let state = {state: 'not_configured', gateway: 'https://connect.ai-secretary.co', installation_id: 'auto-uid', qr_available: false, enabled: false};
  let failRotation = false;
  w.fetch = async (url, options = {}) => {
    if (url.endsWith('/gateway/enroll') || url.endsWith('/gateway/reregister')) {
      writes.push({url, body: JSON.parse(options.body)});
      if (failRotation) return {ok: false, status: 409, json: async () => ({detail: 'Гейтвей недоступен'})};
      state = {...state, state: 'connected', enabled: true, qr_available: true, installation_id: url.endsWith('/reregister') ? 'new-uid' : 'auto-uid'};
      return {ok: true, json: async () => state};
    }
    if (url.endsWith('/gateway')) return {ok: true, json: async () => state};
    if (url.endsWith('/gateway/qr')) return {ok: true, json: async () => ({qr_image: 'data:image/png;base64,c2VjcmV0'})};
    return {ok: true, json: async () => url.endsWith('/settings') ? {settings: {}} : []};
  };
  w.eval(fs.readFileSync(path.join(root, 'assets/admin.js'), 'utf8'));
  await settle();
  assert.equal(el('gatewayAddress').value, 'https://connect.ai-secretary.co');
  for (const id of ['gatewayName', 'gatewayInvitation', 'gatewayPrepare']) assert.equal(el(id), null);
  el('mobileTab').click(); el('gatewayConnectionTab').click();
  await settle();
  assert.equal(el('gatewayUID').value, 'auto-uid');
  assert.equal(writes.length, 0);
  el('gatewayAddress').value = 'https://edited.test';
  await w.loadGateway();
  assert.equal(el('gatewayAddress').value, 'https://edited.test');
  el('gatewayAddress').value = state.gateway;
  el('gatewayForm').dispatchEvent(new w.Event('submit', {cancelable: true}));
  await settle();
  assert.deepEqual(writes[0].body, {gateway: 'https://connect.ai-secretary.co'});
  assert.equal(el('gatewayEnroll').hidden, true);
  assert.equal(el('gatewayReregister').hidden, false);
  el('gatewayShowQR').click(); await settle();
  assert.equal(el('gatewayQRResult').hidden, false);
  failRotation = true;
  el('gatewayReregister').click(); await settle();
  assert.equal(el('gatewayUID').value, 'auto-uid');
  assert.equal(el('gatewayQR').getAttribute('src'), null);
  assert.equal(el('gatewayShowQR').disabled, false);
  failRotation = false;
  el('gatewayReregister').click(); await settle();
  assert.equal(el('gatewayUID').value, 'new-uid');
  assert.equal(el('gatewayQRResult').hidden, true);
  assert.ok(writes[2].url.endsWith('/gateway/reregister'));
  assert.deepEqual(writes[2].body, {gateway: 'https://connect.ai-secretary.co'});
});

test('employee tables preserve structured records, edit, add, delete and reject duplicates', async t => {
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'index.html'), 'utf8'), {
    url: 'http://localhost/admin', runScripts: 'outside-only',
  });
  t.after(() => dom.window.close());
  const w = dom.window;
  w.structuredClone = structuredClone;
  let state = { settings: {
    relationships: { managers: [{ name: 'Анна | <b>Иванова</b>', emails: ['anna@example.org', 'work@example.org'] }], reports: [] },
    identity: { names: [] }, server: { timezone: 'UTC' },
    calendar: { workday_start: '10:00', workday_end: '17:00', daily_plan_time: '08:00' },
    communication_sources: { initial_sync_days: 30 },
    llm: { base_url: 'http://ollama:11434', model: 'qwen', context_length: 16384, temperature: 0.1, auto_create_confidence: 0.85, possible_completion_confidence: 0.8, request_timeout_seconds: 300 },
    worker: { batch_size: 10, ranking_interval_seconds: 900, poll_interval_seconds: 60 },
    document_parser: { max_bytes: 1048576, max_characters: 10000, timeout_seconds: 30 },
    notifications: { due_soon_minutes: 60, overdue_repeat_hour: 10 },
  }};
  const writes = [];
  w.fetch = async (url, options = {}) => {
    if (url.endsWith('/settings')) {
      if (options.method === 'PUT') { const body = JSON.parse(options.body); writes.push(body); state = { settings: body.settings }; }
      return { ok: true, json: async () => structuredClone(state) };
    }
    return { ok: true, json: async () => url.endsWith('/status') ? {} : [] };
  };
  w.eval(fs.readFileSync(path.join(root, 'assets/admin.js'), 'utf8'));
  await settle();
  const doc = w.document;
  const table = doc.getElementById('relationshipManagers');
  assert.equal(table.rows.length, 1);
  assert.equal(table.querySelector('b'), null);
  assert.equal(table.querySelector('input').value, 'Анна | <b>Иванова</b>');
  assert.deepEqual(JSON.parse(JSON.stringify(w.readEmployees('relationshipManagers'))), state.settings.relationships.managers);
  table.querySelector('input').value = 'Анна Петрова';
  doc.querySelector('[data-add-employee="relationshipReports"]').click();
  const report = doc.getElementById('relationshipReports').rows[0];
  report.querySelector('[data-employee-field="name"]').value = 'Иван';
  const addresses = report.querySelector('[data-employee-field="emails"]');
  addresses.value = 'ivan@example.org, IVAN@example.org';
  assert.throws(() => w.readEmployees('relationshipReports'), /без повторов/);
  addresses.value = 'ivan@example.org, second@example.org';
  await w.saveSettings();
  assert.equal(writes.length, 1);
  assert.equal(writes[0].settings.relationships.managers[0].name, 'Анна Петрова');
  assert.deepEqual(writes[0].settings.relationships.reports[0].emails, ['ivan@example.org', 'second@example.org']);
  doc.getElementById('relationshipReports').querySelector('button').click();
  await w.saveSettings();
  assert.deepEqual(writes[1].settings.relationships.reports, []);
  const added = w.addEmployeeRow('relationshipManagers', { name: 'Другой', emails: ['ANNA@example.org'] });
  await w.saveSettings();
  assert.equal(writes.length, 2, 'duplicate email must prevent a request');
  added.querySelector('[data-employee-field="emails"]').value = 'invalid';
  await w.saveSettings();
  assert.equal(writes.length, 2, 'invalid email must prevent a request');
  added.querySelector('input').value = '  ';
  added.querySelector('[data-employee-field="emails"]').value = 'other@example.org';
  await w.saveSettings();
  assert.equal(writes.length, 2, 'blank employee name must prevent a request');
});
