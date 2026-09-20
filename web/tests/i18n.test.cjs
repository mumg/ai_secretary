const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
function setup(t, system, preference) {
  const dom = new JSDOM('<html><body><h1>Настройки</h1><input placeholder="Поиск по поручениям"><div data-language-settings></div><div id="archive"></div></body></html>', {url:'https://example.test/app/', runScripts:'outside-only'});
  const w = dom.window;
  Object.defineProperty(w.navigator, 'language', {value:system});
  if (preference) w.localStorage.setItem('secretary.language', preference);
  for (const file of ['translations.js','i18n.js']) w.eval(fs.readFileSync(path.resolve(__dirname, '../../backend/web/assets', file),'utf8'));
  t.after(()=>w.close());
  return w;
}
for (const [system,expected] of [['ru-RU','ru'], ['en-GB','en'], ['zh-TW','zh'], ['zh-Hans-CN','zh'], ['de-DE','en']]) {
  test(`system language ${system} resolves to ${expected}`,t=>{
    const w=setup(t,system); assert.equal(w.SecretaryI18n.language,expected);
    assert.equal(w.document.querySelector('select').value,'system');
    assert.equal(w.document.querySelector('h1').textContent, {ru:'Настройки',en:'Settings',zh:'设置'}[expected]);
  });
}
test('manual selection wins and substitutions do not translate or re-interpret user text',t=>{
  const w=setup(t,'ru-RU','zh');assert.equal(w.SecretaryI18n.language,'zh');
  assert.equal(w.tr('Вопрос: {0}', 'Настройки {1} <img>'), '问题：Настройки {1} <img>');
  w.document.getElementById('archive').textContent='Настройки';
  assert.equal(w.document.getElementById('archive').textContent,'Настройки');
});
test('all catalog entries preserve placeholders in both translations',()=>{
  const catalog=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../../localization/catalog.json'),'utf8'));
  for(const [source,translations] of Object.entries(catalog)) for(const locale of ['en','zh']) {
    assert.ok(translations[locale],source);
    assert.deepEqual((translations[locale].match(/\{\d+\}|%[sdw]/g)||[]).sort(),(source.match(/\{\d+\}|%[sdw]/g)||[]).sort(),source);
  }
});
