const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '../../backend/src/improver/web');
const settle = ms => new Promise(resolve => setTimeout(resolve, ms));
function setup(t) {
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'index.html'), 'utf8'), {
    url: 'http://localhost/admin', runScripts: 'outside-only',
  });
  t.after(async () => { await settle(20); dom.window.close(); });
  const w = dom.window;
  w.structuredClone = structuredClone;
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  w.fetch = async url => ({ ok: true, json: async () => url.endsWith('/status') ? {} : [] });
  for (const file of ['admin.js', 'mts-link-auth.js']) require('node:vm').runInContext(fs.readFileSync(path.join(root, 'assets', file), 'utf8'), dom.getInternalVMContext());
  const el = id => w.document.getElementById(id);
  const message = (action, origin = w.location.origin) => w.dispatchEvent(new w.MessageEvent('message', {
    source: w, origin, data: { channel: 'improver-mts-sso-v1', from: 'extension', action },
  }));
  const source = { id: 'mts', label: 'МТС', source_type: 'mts_link', enabled: true, settings: {}, tags: [], credential_configured: true };
  return { w, el, message, source };
}
test('missing extension offers manual access token and installation; live connection enables SSO without clearing input', async t => {
  const { w, el, message, source } = setup(t);
  w.openSourceDialog(source);
  el('sourceCredential').value = 'synthetic-only';
  await settle(850);
  assert.equal(el('sourceMtsFallback').hidden, false);
  assert.equal(el('sourceMtsSso').disabled, true);
  assert.equal(el('credentialLabel').textContent, 'Access token');
  assert.match(el('sourceFields').textContent, /84 часа с момента выдачи/);
  assert.ok(el('sourceMtsFallback').querySelector('a[href$="/extension.zip"]'));
  message('ready', 'https://untrusted.example');
  assert.equal(el('sourceMtsSso').disabled, true);
  message('ready');
  assert.equal(el('sourceMtsFallback').hidden, true);
  assert.equal(el('sourceMtsSso').disabled, false);
  assert.equal(el('sourceCredential').value, 'synthetic-only');
  message('unavailable');
  assert.equal(el('sourceMtsFallback').hidden, false);
  assert.equal(el('sourceMtsSso').disabled, true);
  el('sourceType').value = 'imap';
  el('sourceType').dispatchEvent(new w.Event('change'));
  assert.equal(el('credentialLabel').textContent, 'Пароль или токен');
});
test('SSO dialog without extension can switch to the same source manual token form', async t => {
  const { w, el, source } = setup(t);
  w.openMtsSso(source);
  await settle(850);
  assert.equal(el('mtsSsoFallback').hidden, false);
  assert.equal(el('mtsSsoFind').disabled, true);
  el('mtsSsoManual').click();
  assert.equal(el('mtsSsoDialog').open, false);
  assert.equal(el('sourceDialog').open, true);
  assert.equal(el('sourceId').value, source.id);
  assert.equal(w.document.activeElement.id, 'sourceCredential');
});

test('SSO remembers email on reopening, isolates sources and restores browser storage', t => {
  const { w, el, source } = setup(t);
  w.openMtsSso(source);
  el('mtsSsoEmail').value = 'work@example.test';
  el('mtsSsoEmail').dispatchEvent(new w.Event('input'));
  w.cancelMtsLogin();
  w.openMtsSso(source);
  assert.equal(el('mtsSsoEmail').value, 'work@example.test');
  w.openMtsSso({ ...source, id: 'other' });
  assert.equal(el('mtsSsoEmail').value, '');
  const fresh = setup(t);
  fresh.w.localStorage.setItem('improver-mts-sso-email:mts', w.localStorage.getItem('improver-mts-sso-email:mts'));
  fresh.w.openMtsSso(source);
  assert.equal(fresh.el('mtsSsoEmail').value, 'work@example.test');
  fresh.el('mtsSsoEmail').value = '';
  fresh.el('mtsSsoEmail').dispatchEvent(new fresh.w.Event('input'));
  fresh.w.cancelMtsLogin();
  fresh.w.openMtsSso(source);
  assert.equal(fresh.el('mtsSsoEmail').value, '');
  assert.equal(fresh.w.localStorage.getItem('improver-mts-sso-email:mts'), null);
});

test('SSO preserves email in memory when browser storage is blocked', t => {
  const { w, el, source } = setup(t);
  Object.defineProperty(w, 'localStorage', { get() { throw new Error('Blocked'); } });
  w.openMtsSso(source);
  el('mtsSsoEmail').value = 'work@example.test';
  el('mtsSsoEmail').dispatchEvent(new w.Event('input'));
  w.cancelMtsLogin();
  w.openMtsSso(source);
  assert.equal(el('mtsSsoEmail').value, 'work@example.test');
});
