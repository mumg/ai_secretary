const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { JSDOM, VirtualConsole } = require('jsdom');

const root = path.resolve(__dirname, '../../backend/web');
const settle = () => new Promise(resolve => setTimeout(resolve, 50));
const asset = name => fs.readFileSync(path.join(root, 'assets', name), 'utf8');

function page(t) {
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error));
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'index.html'), 'utf8'), {
    url: 'http://localhost/admin', runScripts: 'outside-only', virtualConsole,
  });
  t.after(() => { dom.window.close(); assert.deepEqual(errors.filter(error => !/navigation/.test(error.message)), []); });
  const w = dom.window;
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.tr = (text, ...values) => text.replace(/\{(\d+)\}/g, (_, index) => String(values[Number(index)]));
  w.toast = () => {};
  w.loadSources = async () => {};
  const el = id => w.document.getElementById(id);
  const boot = () => { w.eval(asset('configuration-widgets.js')); w.eval(asset('setup-wizard.js')); };
  const saved = id => w.document.dispatchEvent(new w.CustomEvent('secretary:source-saved', { detail: { sourceId: id } }));
  return { w, el, boot, saved };
}

test('wizard displays only its shared admin widget', () => {
  const dom = new JSDOM(fs.readFileSync(path.join(root, 'index.html'), 'utf8'));
  try {
    const w = dom.window;
    const style = w.document.createElement('style');
    style.textContent = asset('admin.css');
    w.document.head.append(style);
    const el = id => w.document.getElementById(id);
    for (const id of ['setupWizardConfigure', 'setupWizardVerify', 'setupWizardBack', 'setupWizardNext'])
      assert.equal(el(id), null);
    assert.equal(el('analysis').dataset.configurationWidget, 'analysis');
    assert.equal(el('llmSettings').dataset.configurationWidget, 'llm');
    assert.equal(el('identitySettings').dataset.configurationWidget, 'identity');
    w.document.body.classList.add('setup-mode');
    el('setupWizard').hidden = false;
    assert.equal(w.getComputedStyle(el('sources')).display, 'none');
    assert.equal(w.getComputedStyle(w.document.querySelector('.topbar')).display, 'none');
    el('sourceDialog').setAttribute('open', '');
    assert.equal(w.getComputedStyle(w.document.querySelector('.shell')).visibility, 'hidden');
    el('sourceDialog').removeAttribute('open');
    w.document.body.classList.add('setup-model');
    el('setupWizardModel').hidden = false;
    el('setupWizardModel').append(el('analysisConfig'));
    assert.equal(w.getComputedStyle(el('analysisConfig')).display, 'block');
    assert.equal(w.getComputedStyle(el('llmSettings')).display, 'block');
    assert.equal(w.getComputedStyle(el('analysis')).display, 'none');
    w.document.body.classList.remove('setup-model');
    w.document.body.classList.add('setup-identity');
    el('setupWizardModel').append(el('analysis'));
    assert.equal(w.getComputedStyle(el('identitySettings')).display, 'block');
    assert.equal(w.getComputedStyle(el('analysisConfig')).display, 'none');
  } finally { dom.window.close(); }
});

test('saving people verifies the shared identity widget and opens the model step', async t => {
  const { w, el, boot } = page(t);
  const calls = [];
  let configured = false;
  w.request = async (url, options = {}) => {
    calls.push(`${options.method || 'GET'} ${url}`);
    if (url === '/setup-wizard') return { required: true, steps: [
      { title: 'Пользователь и сотрудники', widgets: [{ id: 'identity', configured: false, verified: false }] },
      { title: 'Модель', widgets: [{ id: 'llm', configured: false, verified: false }] },
    ] };
    if (url === '/configuration-widgets') return { widgets: {
      identity: { configured, verified: false }, llm: { configured: false, verified: false },
    } };
    return { status: 'ok' };
  };
  boot();
  await settle();
  assert.equal(el('analysis').parentElement, el('setupWizardModel'));
  assert.equal(el('identityNames').required, true);
  assert.equal(w.document.body.classList.contains('setup-identity'), true);
  assert.equal(el('setupWizardStatus').textContent, '');
  configured = true;
  w.document.dispatchEvent(new w.CustomEvent('secretary:settings-saved', { detail: { panel: 'identity' } }));
  await settle();
  assert.ok(calls.includes('POST /setup-wizard/identity/confirm'));
  assert.equal(el('setupWizardTitle').textContent, 'Модель');
  assert.equal(el('identityNames').required, false);
  assert.equal(w.document.body.classList.contains('setup-model'), true);
});

test('already verified widgets finish automatically without navigation buttons', async t => {
  const { w, el, boot } = page(t);
  const calls = [];
  w.request = async (url, options = {}) => {
    calls.push(`${options.method || 'GET'} ${url}`);
    if (url === '/setup-wizard') return { required: true, steps: [
      { title: 'Модель', widgets: [{ id: 'llm', configured: true, verified: true }] },
    ] };
    return {};
  };
  boot();
  await settle();
  assert.ok(calls.includes('POST /setup-wizard/finish'));
  assert.equal(el('setupWizard').hidden, true);
});

test('application opens setup only while preconfigured work is incomplete', async () => {
  for (const required of [false, true]) {
    const navigations = [];
    vm.runInNewContext(asset('setup-wizard-redirect.js'), {
      fetch: async () => ({ ok: true, json: async () => ({ required }) }),
      location: { replace: value => navigations.push(value) },
    });
    await settle();
    assert.deepEqual(navigations, required ? ['/admin?setup=1'] : []);
  }
});

test('two source widgets on one step save, verify and advance in order', async t => {
  const { w, el, boot, saved } = page(t);
  const opened = [];
  const calls = [];
  const configured = { mail: false, meetings: false };
  const verified = { mail: false, meetings: false };
  const steps = [
    { title: 'Источники', instructions: 'Откройте https://example.org/mail/setup. <script>plain text</script> javascript:alert(1)', widgets: [
      { id: 'source:mail', configured: false, verified: false },
      { id: 'source:meetings', configured: false, verified: false },
    ] },
    { title: 'Модель', instructions: 'Введите токен', widgets: [{ id: 'llm', configured: false, verified: false }] },
  ];
  w.request = async (url, options = {}) => {
    calls.push(`${options.method || 'GET'} ${url}`);
    if (url === '/setup-wizard') return { required: true, steps };
    if (url === '/sources') return [
      { id: 'mail', source_type: 'imap', enabled: false },
      { id: 'meetings', source_type: 'mts_link', enabled: false },
    ];
    if (url === '/configuration-widgets') return { widgets: {
      'source:mail': { configured: configured.mail, verified: verified.mail },
      'source:meetings': { configured: configured.meetings, verified: verified.meetings },
      llm: { configured: false, verified: false },
    } };
    if (url === '/sources/mail/test') verified.mail = true;
    if (url === '/sources/meetings/test') verified.meetings = true;
    return { status: 'ok' };
  };
  w.openSourceDialog = source => { opened.push(source.id); el('sourceEnabled').checked = false; };
  boot();
  await settle();
  assert.deepEqual(opened, ['mail']);
  assert.equal(el('sourceEnabled').checked, true);
  for (const id of ['setupWizardInstructions', 'sourceWizardInstructions']) {
    const instructions = el(id);
    assert.equal(instructions.textContent, steps[0].instructions);
    assert.equal(instructions.querySelector('script'), null);
    const links = instructions.querySelectorAll('a');
    assert.equal(links.length, 1);
    assert.equal(links[0].href, 'https://example.org/mail/setup');
    assert.equal(links[0].target, '_blank');
    assert.equal(links[0].rel, 'noopener noreferrer');
  }
  assert.equal(await w.ConfigurationWidgets.get('source:mail').isConfigured(), false);
  configured.mail = true;
  saved('mail');
  await settle();
  assert.ok(calls.includes('POST /sources/mail/test'));
  assert.deepEqual(opened, ['mail', 'meetings']);
  assert.equal(await w.ConfigurationWidgets.get('source:mail').isConfigured(), true);
  configured.meetings = true;
  saved('meetings');
  await settle();
  assert.ok(calls.includes('POST /sources/meetings/test'));
  assert.equal(el('setupWizardTitle').textContent, 'Модель');
  assert.equal(el('analysisConfig').parentElement, el('setupWizardModel'));
});

test('an existing source is checked in place before opening the next editor', async t => {
  const { w, boot } = page(t);
  const opened = [];
  const calls = [];
  w.request = async (url, options = {}) => {
    calls.push(`${options.method || 'GET'} ${url}`);
    if (url === '/setup-wizard') return { required: true, steps: [
      { title: 'Почта', instructions: 'Mail', widgets: [{ id: 'source:mail', configured: true, verified: false }] },
      { title: 'Встречи', instructions: 'SSO', widgets: [{ id: 'source:meetings', configured: false, verified: false }] },
    ] };
    if (url === '/sources') return [
      { id: 'mail', source_type: 'imap', enabled: true },
      { id: 'meetings', source_type: 'mts_link', enabled: false },
    ];
    if (url === '/configuration-widgets') return { widgets: {
      'source:mail': { configured: true, verified: false },
      'source:meetings': { configured: false, verified: false },
    } };
    return { status: 'ok' };
  };
  w.openSourceDialog = source => opened.push(source.id);
  boot();
  await settle();
  assert.ok(calls.includes('POST /sources/mail/test'));
  assert.deepEqual(opened, ['meetings']);
});

test('failed automatic check keeps the same widget and explains the error', async t => {
  const { w, el, boot, saved } = page(t);
  w.request = async url => {
    if (url === '/setup-wizard') return { required: true, steps: [
      { title: 'Почта', instructions: 'Введите пароль', widgets: [{ id: 'source:mail', configured: false, verified: false }] },
    ] };
    if (url === '/sources') return [{ id: 'mail', source_type: 'imap', enabled: false }];
    if (url === '/configuration-widgets') return { widgets: { 'source:mail': { configured: true, verified: false } } };
    if (url === '/sources/mail/test') throw Error('Connection unavailable');
    return {};
  };
  w.openSourceDialog = () => {};
  boot();
  await settle();
  saved('mail');
  await settle();
  assert.equal(el('setupWizardTitle').textContent, 'Почта');
  assert.equal(el('setupWizardStatus').textContent, 'Connection unavailable');
  assert.equal(el('sourceSubmit').disabled, false);
});

test('admin source save reports an automatic connection failure', async t => {
  const { w, saved } = page(t);
  const messages = [];
  w.toast = (message, error) => messages.push([message, error]);
  w.request = async url => {
    if (url === '/configuration-widgets') return { widgets: { 'source:mail': { configured: true, verified: false } } };
    if (url === '/sources/mail/test') throw Error('Server unavailable');
    return [];
  };
  w.eval(asset('configuration-widgets.js'));
  saved('mail');
  await settle();
  assert.deepEqual(messages, [['Server unavailable', true]]);
});

test('configured and verified sources resume directly at the same LLM widget', async t => {
  const { w, el, boot } = page(t);
  w.request = async url => url === '/setup-wizard'
    ? { required: true, steps: [
      { title: 'Почта', widgets: [{ id: 'source:mail', configured: true, verified: true }] },
      { title: 'Модель', instructions: 'Введите токен', widgets: [{ id: 'llm', configured: false, verified: false }] },
    ] }
    : { widgets: { llm: { configured: false, verified: false } } };
  w.openSourceDialog = () => { throw Error('verified source reopened'); };
  boot();
  await settle();
  assert.equal(el('setupWizardTitle').textContent, 'Модель');
  assert.equal(el('analysisConfig').parentElement, el('setupWizardModel'));
});

test('MTS widget opens native SSO with source enabled', async t => {
  const { w, el, boot } = page(t);
  const signins = [];
  w.request = async url => url === '/setup-wizard'
    ? { required: true, steps: [{ title: 'Встречи', instructions: 'SSO', widgets: [
      { id: 'source:meetings', configured: false, verified: false },
    ] }] }
    : url === '/sources' ? [{ id: 'meetings', source_type: 'mts_link', enabled: false }]
      : { widgets: { 'source:meetings': { configured: false, verified: false } } };
  w.openSourceDialog = () => {};
  w.openMtsSso = (source, enabled) => signins.push([source.id, enabled]);
  boot();
  await settle();
  assert.equal(el('setupWizardSSO').hidden, false);
  el('setupWizardSSO').click();
  await settle();
  assert.deepEqual(signins, [['meetings', true]]);
});

test('the same MTS widget preserves a disabled source when reauthenticating from admin', async t => {
  const { w } = page(t);
  const signins = [];
  w.request = async () => [{ id: 'meetings', source_type: 'mts_link', enabled: false }];
  w.openMtsSso = (source, enabled) => signins.push([source.id, enabled]);
  w.eval(asset('configuration-widgets.js'));
  await w.ConfigurationWidgets.get('source:meetings').beginSSO();
  assert.deepEqual(signins, [['meetings', false]]);
});

test('saving the shared LLM form probes the model and completes setup', async t => {
  const { w, el, boot } = page(t);
  const calls = [];
  let configured = false;
  w.request = async (url, options = {}) => {
    calls.push(`${options.method || 'GET'} ${url}`);
    if (url === '/setup-wizard') return { required: true, steps: [
      { title: 'Модель', instructions: 'Введите токен', widgets: [{ id: 'llm', configured: false, verified: false }] },
    ] };
    if (url === '/configuration-widgets') return { widgets: { llm: { configured, verified: false } } };
    return { status: 'ok' };
  };
  boot();
  await settle();
  configured = true;
  w.document.dispatchEvent(new w.CustomEvent('secretary:settings-saved', { detail: { llm: true } }));
  await settle();
  assert.ok(calls.includes('POST /setup-wizard/llm/test'));
  assert.ok(calls.includes('POST /setup-wizard/finish'));
  assert.equal(el('setupWizard').hidden, true);
});
