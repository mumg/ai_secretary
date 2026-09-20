const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const i18n = require('../i18n.cjs');
test('desktop detects OS language, persists override and interpolates once',t=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'secretary-language-'));
  t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const app={getLocale:()=> 'zh-TW',getPath:()=>dir};
  i18n.configure(app);assert.equal(i18n.tr('Настройки'),'设置');
  i18n.set('en');i18n.configure(app);assert.equal(i18n.tr('Настройки'),'Settings');
  assert.equal(i18n.tr('Вопрос: {0}','Настройки {1}'),'Question: Настройки {1}');
  assert.equal(i18n.set('../../ru'),false);
  i18n.set('system');assert.equal(i18n.language,'zh');
});

test('language IPC accepts only the trusted main frame and valid preferences', t => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'secretary-language-ipc-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  i18n.configure({ getLocale: () => 'en', getPath: () => dir });
  let handler;
  const frame = { url: 'http://127.0.0.1:12345/admin' };
  const contents = { mainFrame: frame };
  i18n.registerIPC({ on(channel, listener) {
    assert.equal(channel, 'secretary:language');
    handler = listener;
  } }, () => contents, url => new URL(url).origin === 'http://127.0.0.1:12345');
  const call = (sender, senderFrame, value) => {
    const event = { sender, senderFrame };
    handler(event, value);
    return event.returnValue;
  };
  assert.equal(call(contents, frame), 'system');
  assert.equal(call(contents, frame, 'ru'), 'ru');
  assert.equal(call(contents, frame, '../../en'), 'ru');
  assert.equal(call({ mainFrame: frame }, frame, 'en'), null);
  assert.equal(call(contents, { ...frame }, 'en'), null);
  frame.url = 'https://example.com/admin';
  assert.equal(call(contents, frame, 'en'), null);
  assert.equal(i18n.preference, 'ru');
});
