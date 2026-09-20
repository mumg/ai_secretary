const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const background = fs.readFileSync(path.resolve(__dirname, '../../browser-extension/src/background.js'), 'utf8');
const event = () => ({ callbacks: [], addListener(cb) { this.callbacks.push(cb); } });
const settle = () => new Promise(resolve => setTimeout(resolve, 20));
function harness() {
  const state = {}, commands = [], sent = [], removed = [];
  const admin = { id: 1, url: 'https://admin.test/admin' };
  const chrome = {
    storage: { session: {
      async get(key) { return key === null ? { ...state } : { [key]: state[key] }; },
      async set(value) { Object.assign(state, value); },
      async remove(key) { delete state[key]; }
    } },
    action: { onClicked: event(), async setBadgeText() {} },
    scripting: { async executeScript() {} },
    runtime: { onMessage: event() },
    tabs: { onRemoved: event(), async get() { return admin; },
      async sendMessage(id, message) { sent.push({ id, message }); },
      async update(id, value) { commands.push(['navigate', id, value]); },
      async remove(id) { removed.push(id); } },
    windows: { async create() { return { tabs: [{ id: 2 }] }; } },
    debugger: { onEvent: event(), onDetach: event(),
      async attach(target) { commands.push(['attach', target]); },
      async detach(target) { commands.push(['detach', target]); },
      async sendCommand(target, method, params) { commands.push([method, target, params]); } },
    alarms: { onAlarm: event(), async create() {}, async clear() {} }
  };
  chrome.i18n = { getUILanguage: () => 'ru-RU' };
  chrome.storage.local = { get: async () => ({ language: 'ru' }) };
  chrome.storage.onChanged = event();
  const ctx = vm.createContext({ chrome, URL });
  ctx.importScripts = (...files) => files.forEach(file => vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../../browser-extension/src', file), 'utf8'), ctx));
  vm.runInContext(background, ctx);
  const flowId = '12345678-1234-1234-1234-123456789abc';
  async function start(url = 'https://gw.mts-link.ru/sso/saml/login?returnUrl=mtslink%3A%2F%2Fmobile%2Flogin', sender = { tab: admin, url: admin.url, frameId: 0 }) {
    await new Promise(resolve => chrome.runtime.onMessage.callbacks[0]({ action: 'start', flowId, authorizationUrl: url }, sender, resolve));
  }
  return { ctx, chrome, state, commands, sent, removed, admin, flowId, start };
}
test('only valid admin pages and exact mobile auth callback are accepted', () => {
  const { ctx } = harness();
  for (const raw of ['https://evil.test/user', 'http://remote.test/admin', 'https://u:p@admin.test/admin'])
    assert.equal(vm.runInContext(`adminOrigin(${JSON.stringify(raw)})`, ctx), null);
  assert.equal(vm.runInContext(`adminOrigin('http://127.0.0.1:8000/admin')`, ctx), 'http://127.0.0.1:8000');
  for (const raw of ['mtslink://mobile/login?code=wrong', 'mtslink://evil/login?authCode=x',
    'mtslink://mobile/login?authCode=a&authCode=b', 'mtslink://mobile/login?authCode=bad%0Aheader'])
    assert.equal(vm.runInContext(`callbackCode(${JSON.stringify(raw)})`, ctx), null);
  assert.equal(vm.runInContext(`callbackCode('mtslink://mobile/login?authCode=test-code')`, ctx), 'test-code');
});
test('SSO attaches before navigation, aborts callback, sends only to originating admin and closes', async () => {
  const h = harness();
  await h.chrome.action.onClicked.callbacks[0](h.admin);
  await h.start();
  assert.deepEqual(h.commands.slice(0, 3).map(c => c[0]), ['attach', 'Fetch.enable', 'navigate']);
  h.chrome.debugger.onEvent.callbacks[0]({ tabId: 2 }, 'Fetch.requestPaused', {
    requestId: 'request', responseHeaders: [{ name: 'Location', value: 'mtslink://mobile/login?authCode=synthetic' }]
  });
  await settle();
  const aborted = h.commands.findIndex(c => c[0] === 'Fetch.failRequest');
  assert.ok(aborted >= 0);
  assert.ok(h.commands.findIndex(c => c[0] === 'detach') > aborted);
  assert.equal(h.sent.at(-1).id, 1);
  assert.equal(h.sent.at(-1).message.authCode, 'synthetic');
  assert.equal(h.sent.at(-1).message.action, 'complete');
  assert.deepEqual(h.removed, [2]);
  assert.equal(h.state['mts-flow-2'], undefined);
});
test('unapproved tabs and non-MTS auth URLs cannot start capture', async () => {
  const h = harness();
  await h.start();
  assert.equal(h.commands.length, 0);
  await h.chrome.action.onClicked.callbacks[0](h.admin);
  await h.start('https://evil.test/sso/saml/login?returnUrl=mtslink://mobile/login');
  assert.equal(h.commands.length, 0);
  assert.equal(h.sent.at(-1).message.action, 'error');
});
test('a moved admin tab does not receive the authorization code', async () => {
  const h = harness();
  await h.chrome.action.onClicked.callbacks[0](h.admin);
  await h.start();
  h.admin.url = 'https://other.test/admin';
  h.chrome.debugger.onEvent.callbacks[0]({ tabId: 2 }, 'Fetch.requestPaused', {
    requestId: 'request', responseHeaders: [{ name: 'Location', value: 'mtslink://mobile/login?authCode=synthetic' }]
  });
  await settle();
  assert.equal(h.sent.filter(s => s.message.authCode).length, 0);
  assert.deepEqual(h.removed, [2]);
});
test('timeout closes only its SSO tab and reports a retryable error', async () => {
  const h = harness();
  await h.chrome.action.onClicked.callbacks[0](h.admin);
  await h.start();
  h.chrome.alarms.onAlarm.callbacks[0]({ name: 'mts-flow-2' });
  await settle();
  assert.deepEqual(h.removed, [2]);
  assert.equal(h.sent.at(-1).message.action, 'error');
});
