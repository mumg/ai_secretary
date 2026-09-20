(async () => {
  const select = document.getElementById('language');
  select.value = await SecretaryLanguageReady;
  function render() {
    document.querySelector('h1').textContent = tr('AI Секретарь');
    document.getElementById('language-label').textContent = tr('Язык');
    select.options[0].textContent = tr('Как в системе');
    document.documentElement.lang = select.value === 'system' ? chrome.i18n.getUILanguage() : select.value;
  }
  render();
  select.addEventListener('change', async () => { await chrome.storage.local.set({ language: select.value }); location.reload(); });
})();
