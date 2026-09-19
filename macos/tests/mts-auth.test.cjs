const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { MtsAuth, CHANNEL, loginURL, callbackCode } = require('../mts-auth.cjs');
const { connectionOrigin } = require('../windows-services.cjs');
const origin = 'http://127.0.0.1:18000';
const login = 'https://gw.mts-link.ru/sso/saml/login?returnUrl=mtslink%3A%2F%2Fmobile%2Flogin&email=test%40example.org';
const flowId = 'd5452c0a-99d1-4c3f-a1dc-6205806f7401';
function fixture() {
  const windows = [], partitions = [], sent = [];
  const owner = new EventEmitter(); owner.isDestroyed = () => false;
  owner.webContents = new EventEmitter();
  owner.webContents.mainFrame = { url: origin + '/admin' };
  owner.webContents.getURL = () => owner.webContents.mainFrame.url;
  owner.webContents.send = (channel, message) => sent.push({ channel, ...message });
  class Popup extends EventEmitter {
    constructor(options) { super(); this.options = options; this.webContents = new EventEmitter();
      this.webContents.setWindowOpenHandler = f => { this.open = f; };
      this.webContents.getURL = () => this.url; windows.push(this); }
    removeMenu() {} setTitle() {} isDestroyed() { return this.destroyed; }
    destroy() { this.destroyed = true; this.emit('closed'); }
    loadURL(url) { this.url = url; return Promise.resolve(); }
  }
  const session = { fromPartition(name) {
    const p = { name, setPermissionRequestHandler() {}, setPermissionCheckHandler() {},
      webRequest: { onHeadersReceived(...args) { p.handler = args.at(-1); } },
      closeAllConnections: async () => {}, clearAuthCache: async () => {},
      clearStorageData: async () => { p.cleared = true; }, clearCache: async () => {} };
    partitions.push(p); return p;
  } };
  const ipcMain = new EventEmitter();
  const auth = new MtsAuth({ BrowserWindow: Popup, session, ipcMain }, owner, () => origin);
  const event = { sender: owner.webContents, senderFrame: owner.webContents.mainFrame };
  return { auth, event, owner, windows, partitions, sent, ipcMain };
}
test('SSO endpoints and mobile callbacks reject lookalikes and malformed codes', () => {
  assert.equal(loginURL(login), login);
  for (const url of [login.replace('gw.mts-link.ru', 'gw.mts-link.ru.evil.org'), login.replace('https:', 'http:'),
    login.replace('/sso/saml/login', '/evil'), login + '&returnUrl=mtslink%3A%2F%2Fmobile%2Flogin']) assert.throws(() => loginURL(url));
  assert.equal(callbackCode('mtslink://mobile/login?authCode=fake-test-code'), 'fake-test-code');
  for (const url of ['mtslink://mobile.evil/login?authCode=x', 'mtslink://mobile:42/login?authCode=x',
    'mtslink://mobile/login?authCode=x&authCode=y', 'mtslink://mobile/login?authCode=%0A',
    'mtslink://mobile/login?authCode=x#fragment', 'https://mobile/login?authCode=x']) assert.equal(callbackCode(url), null);
});
test('only the local admin main frame can start SSO; no Node/preload in remote login', async () => {
  const f = fixture();
  const message = { action: 'start', flowId, authorizationUrl: login };
  f.auth.handle({ ...f.event, senderFrame: { url: origin + '/admin' } }, message);
  assert.equal(f.windows.length, 0);
  f.owner.webContents.mainFrame.url = 'https://evil.org/admin';
  f.auth.handle(f.event, message); assert.equal(f.windows.length, 0);
  f.owner.webContents.mainFrame.url = origin + '/admin';
  f.auth.handle(f.event, message);
  assert.equal(f.windows.length, 1);
  assert.equal(f.windows[0].options.webPreferences.nodeIntegration, false);
  assert.equal(f.windows[0].options.webPreferences.sandbox, true);
  assert.equal(f.windows[0].options.webPreferences.preload, undefined);
  assert.ok(!f.partitions[0].name.startsWith('persist:'));
  await f.auth.finish({ action: 'cancelled' });
  assert.ok(f.partitions[0].cleared);
});
test('capture gateway Location once, cancel redirect, close window and clear isolated session', async () => {
  const f = fixture();
  f.auth.handle(f.event, { action: 'start', flowId, authorizationUrl: login });
  let response;
  f.partitions[0].handler({ url: 'https://gw.mts-link.ru/sso/callback', statusCode: 302,
    responseHeaders: { Location: ['mtslink://mobile/login?authCode=fake-test-code'] } }, r => { response = r; });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(response, { cancel: true });
  assert.equal(f.sent.filter(m => m.action === 'complete').length, 1);
  assert.equal(f.sent.at(-1).authCode, 'fake-test-code');
  assert.equal(f.sent.at(-1).flowId, flowId);
  assert.equal(f.sent.at(-1).transport, 'desktop');
  assert.ok(f.windows[0].destroyed); assert.ok(f.partitions[0].cleared);
  assert.equal(f.auth.flow, null);
});
test('untrusted redirect cannot complete a flow; cancel/reload releases listeners and credentials', async () => {
  const f = fixture();
  f.auth.handle(f.event, { action: 'start', flowId, authorizationUrl: login });
  let response;
  f.partitions[0].handler({ url: 'https://evil.org/sso/callback', statusCode: 302,
    responseHeaders: { location: ['mtslink://mobile/login?authCode=fake'] } }, r => { response = r; });
  assert.equal(response.cancel, false); assert.ok(f.auth.flow);
  f.owner.webContents.emit('did-start-navigation', {}, origin + '/admin', false, true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.auth.flow, null); assert.ok(f.partitions[0].cleared);
  f.owner.emit('closed'); assert.equal(f.ipcMain.listenerCount(CHANNEL), 0);
});
test('Windows desktop accepts only its installer loopback endpoint', () => {
  assert.equal(connectionOrigin('[InternetShortcut]\r\nURL=http://127.0.0.1:18000/app\r\n'), origin);
  for (const raw of ['URL=https://evil.org/app', 'URL=http://127.0.0.1.evil.org:18000/app',
    'URL=file:///C:/private', 'URL=http://user:pass@127.0.0.1:18000/app', 'URL=http://127.0.0.1:18000/admin'])
    assert.throws(() => connectionOrigin(raw));
});
