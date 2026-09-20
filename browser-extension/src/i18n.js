(() => {
  if (globalThis.SecretaryLanguageReady) return;
  let choice = 'system';
  const code = () => {
    const raw = choice === 'system' ? chrome.i18n.getUILanguage() : choice;
    const base = raw.toLowerCase().replaceAll('_', '-').split('-')[0];
    return ['ru', 'en', 'zh'].includes(base) ? base : 'en';
  };
  globalThis.tr = source => code() === 'ru' ? source : globalThis.SecretaryTranslations?.[source]?.[code()] || source;
  globalThis.SecretaryLanguageReady = chrome.storage.local.get('language').then(values => {
    if (['system', 'ru', 'en', 'zh'].includes(values.language)) choice = values.language;
    return choice;
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && changes.language) choice = changes.language.newValue || 'system';
  });
})();
