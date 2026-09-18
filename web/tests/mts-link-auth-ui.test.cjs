const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '../../backend/web');
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
test('fresh MTS source has default rules; custom and cleared rules survive reopening and type switches', t => {
  const { w, el, source } = setup(t);
  const selectType = type => {
    el('sourceType').value = type;
    el('sourceType').dispatchEvent(new w.Event('change'));
  };
  w.openSourceDialog();
  assert.equal(el('sourceLinkPatterns').value, '');
  selectType('mts_link');
  const standard = String.raw`^https://mts\.mts-link\.ru/j/MTC/(?P<meeting_id>\d+)(?:/[^?#]*)?(?:\?[^#]*)?(?:#.*)?$`;
  assert.equal(el('sourceLinkPatterns').value, standard);
  assert.equal(w.readSourceSettings().link_patterns[0], standard);
  selectType('imap');
  assert.equal(el('sourceLinkPatterns').value, '');
  selectType('mts_link');
  el('sourceLinkPatterns').value = '';
  selectType('exchange');
  selectType('mts_link');
  assert.equal(el('sourceLinkPatterns').value, '');
  const cleared = w.readSourceSettings();
  assert.equal(cleared.link_patterns.length, 0);
  w.openSourceDialog({ ...source, settings: cleared });
  assert.equal(el('sourceLinkPatterns').value, '');
  const custom = String.raw`^https://example\.test/(?P<meeting_id>\d+)$`;
  w.openSourceDialog({ ...source, settings: { link_patterns: [custom] } });
  assert.equal(el('sourceLinkPatterns').value, custom);
  w.openSourceDialog(source);
  assert.equal(el('sourceLinkPatterns').value, standard);
});
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
  assert.equal(fresh.w.localStorage.getItem('improver-mts-sso-email:mts'), '');
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

test('SSO restores existing account email, but never overwrites typing or another source', async t => {
  const { w, el, source } = setup(t);
  const replies = [];
  w.fetch = async url => {
    if (url.endsWith('/login-email')) return new Promise(resolve => replies.push(email => resolve({ ok: true, json: async () => ({ email }) })));
    return { ok: true, json: async () => [] };
  };
  w.openMtsSso(source);
  replies.shift()('existing@example.test');
  await settle(20);
  assert.equal(el('mtsSsoEmail').value, 'existing@example.test');
  w.openMtsSso({ ...source, id: 'second' });
  el('mtsSsoEmail').value = 'typed@example.test';
  el('mtsSsoEmail').dispatchEvent(new w.Event('input'));
  replies.shift()('old@example.test');
  await settle(20);
  assert.equal(el('mtsSsoEmail').value, 'typed@example.test');
  w.openMtsSso({ ...source, id: 'third' });
  const stale = replies.shift();
  w.openMtsSso(source);
  stale('stale@example.test');
  await settle(20);
  assert.equal(el('mtsSsoEmail').value, 'existing@example.test');
});

test('source link editor restores patterns, previews unsaved regex and saves it intact', async t => {
  const { w, el, source } = setup(t);
  const pattern = String.raw`^https://mts\.mts-link\.ru/j/MTC/(?P<meeting_id>\d{1,20})(?:/.*)?$`;
  w.openSourceDialog({ ...source, settings: { link_patterns: [pattern] } });
  assert.equal(el('sourceLinkPatterns').value, pattern);
  assert.deepEqual(Array.from(w.readSourceSettings().link_patterns), [pattern]);
  el('sourceLinkTestUrl').value = 'https://mts.mts-link.ru/j/MTC/23854886808/sesssion/XXX';
  let saved;
  w.fetch = async (url, options = {}) => {
    if (url.endsWith('/source-link-preview')) {
      assert.deepEqual(JSON.parse(options.body).patterns, [pattern]);
      return { ok: true, json: async () => ({ matches: [{ source_id: source.id, source_type: 'mts_link', meeting_id: '23854886808' }] }) };
    }
    if (options.method === 'PUT') saved = JSON.parse(options.body);
    return { ok: true, json: async () => url.endsWith('/sources') ? [source] : url.endsWith('/tags') ? [] : {} };
  };
  await w.testSourceLink();
  assert.match(el('sourceLinkTestResult').textContent, /meeting_id: 23854886808/);
  await w.saveSource({ preventDefault() {} });
  assert.deepEqual(saved.settings.link_patterns, [pattern]);
});
