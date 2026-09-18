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
  assert.match(el('deploymentHint').textContent, /Локальный WEB/);
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
      assert.equal(JSON.parse(options.body).label, 'My phone');
      return { ok: true, json: async () => ({ qr_image: 'data:image/png;base64,dGVzdA==', server_url: 'https://example.test', expires_at: '2027-09-18T12:00:00Z' }) };
    }
    return { ok: true, json: async () => url.endsWith('/settings') ? {settings: {}} : [] };
  };
  w.eval(fs.readFileSync(path.join(root, 'assets/admin.js'), 'utf8'));
  const el = id => w.document.getElementById(id);
  await settle();
  assert.equal(issueCount, 0);
  el('mobileTab').click();
  el('mobileDeviceLabel').value = 'My phone';
  await w.createMobileIdentity({ preventDefault() {} });
  assert.equal(issueCount, 1);
  assert.equal(el('mobileIdentityResult').hidden, false);
  assert.ok(el('mobileIdentityQR').src.startsWith('data:image/png;base64,'));
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
