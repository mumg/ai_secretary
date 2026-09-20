/* UI text only. Never translate values received from the user's archive. */
(() => {
  'use strict';
  const key = 'secretary.language';
  const choices = ['system', 'ru', 'en', 'zh'];
  const normalize = value => {
    const base = String(value || '').toLowerCase().replaceAll('_', '-').split('-')[0];
    return ['ru', 'en', 'zh'].includes(base) ? base : 'en';
  };
  const read = () => { try { return localStorage.getItem(key) || 'system'; } catch { return 'system'; } };
  let preference = choices.includes(read()) ? read() : 'system';
  const resolve = () => normalize(preference === 'system' ? navigator.language : preference);
  const api = {
    get preference() { return preference; },
    get language() { return resolve(); },
    get locale() { return { ru: 'ru-RU', en: 'en-US', zh: 'zh-CN' }[resolve()]; },
    normalize,
    t(source, ...args) {
      const template = resolve() === 'ru' ? source : (globalThis.SecretaryTranslations?.[source]?.[resolve()] || source);
      return template.replace(/\{(\d+)\}/g, (match, index) => index < args.length ? String(args[index]) : match);
    },
    setLanguage(value) {
      if (!choices.includes(value)) return;
      preference = value;
      try { localStorage.setItem(key, value); } catch { /* Private browsing may block storage. */ }
      window.dispatchEvent(new CustomEvent('secretary-language', { detail: value }));
      location.reload();
    },
    translateStatic(root = document) {
      // Called once on the static shell, before API data or user content renders.
      const walk = node => {
        if (node.nodeType === 3) {
          const text = node.nodeValue.trim();
          if (globalThis.SecretaryTranslations?.[text]) node.nodeValue = node.nodeValue.replace(text, api.t(text));
          return;
        }
        if (['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE', 'PRE'].includes(node.nodeName)) return;
        for (const attr of ['title', 'placeholder', 'aria-label', 'alt']) {
          const value = node.getAttribute?.(attr);
          if (value && globalThis.SecretaryTranslations?.[value]) node.setAttribute(attr, api.t(value));
        }
        for (const child of node.childNodes || []) walk(child);
      };
      walk(root);
      document.documentElement.lang = api.language === 'zh' ? 'zh-CN' : api.language;
    },
    mountSettings() {
      for (const host of document.querySelectorAll('[data-language-settings]')) {
        const label = document.createElement('label');
        label.textContent = api.t('Язык') + ' ';
        const select = document.createElement('select');
        select.setAttribute('aria-label', api.t('Язык'));
        for (const [value, title] of [['system', api.t('Как в системе')], ['ru', 'Русский'], ['en', 'English'], ['zh', '简体中文']]) {
          const option = document.createElement('option'); option.value = value; option.textContent = title; select.append(option);
        }
        select.value = preference;
        select.addEventListener('change', () => api.setLanguage(select.value));
        label.append(select); host.append(label);
      }
    }
  };
  globalThis.SecretaryI18n = api;
  globalThis.tr = api.t;
  api.translateStatic();
  api.mountSettings();
  window.addEventListener('storage', event => { if (event.key === key && event.newValue !== preference) location.reload(); });
  window.addEventListener('languagechange', () => { if (preference === 'system') location.reload(); });
})();
