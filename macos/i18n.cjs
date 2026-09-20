'use strict';
const fs = require('node:fs');
const path = require('node:path');
const catalog = require('./translations.json');
let language = 'en', preference = 'system', file, systemLanguage = 'en';
const normalize = value => { const code = String(value).replaceAll('_', '-').toLowerCase().split('-')[0]; return ['ru', 'en', 'zh'].includes(code) ? code : 'en'; };
function set(value, persist = true) {
  if (!['system', 'ru', 'en', 'zh'].includes(value)) return false;
  preference = value; language = normalize(value === 'system' ? systemLanguage : value);
  if (persist && file) fs.writeFileSync(file, JSON.stringify({ language: value }), { mode: 0o600 });
  return true;
}
module.exports = {
  normalize,
  configure(app) {
    systemLanguage = app.getLocale();
    file = path.join(app.getPath('userData'), 'language.json');
    fs.mkdirSync(path.dirname(file), { recursive: true });
    let value = 'system'; try { value = JSON.parse(fs.readFileSync(file, 'utf8')).language; } catch { /* First launch. */ }
    if (!set(value, false)) set('system', false);
  },
  registerIPC(ipcMain, getContents, isInternal) {
    ipcMain.on('secretary:language', (event, value) => {
      if (event.sender !== getContents() || event.senderFrame !== event.sender.mainFrame || !isInternal(event.senderFrame.url)) {
        event.returnValue = null;
        return;
      }
      if (value !== undefined) set(value);
      event.returnValue = preference;
    });
  },
  set,
  get preference() { return preference; },
  get language() { return language; },
  tr(source, ...args) {
    const template = language === 'ru' ? source : catalog[source]?.[language] || source;
    return template.replace(/\{(\d+)\}/g, (match, i) => i < args.length ? String(args[i]) : match);
  }
};
