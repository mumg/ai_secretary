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
