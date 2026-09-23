/* Desktop-only optional component. Installation privileges never cross this UI. */
(() => {
  'use strict';
  const el = id => document.getElementById(id), channel = 'improver-local-ollama-v1';
  const card = el('localOllama');
  if (!card) return;
  let state = null, saving = false, installing = false, awaiting = false;
  let inspectedChoice = null, inspectTimer, percent = 0, connected = false;
  const choice = () => ({ model: el('localOllamaModel').value.trim(), contextLength: Number(el('localOllamaContext').value) });
  const send = action => window.postMessage({ channel, from: 'admin', action, ...choice() }, location.origin);
  const matches = () => {
    const selected = choice(), model = selected.model.includes(':') ? selected.model : selected.model + ':latest';
    return model === state?.selected?.model && selected.contextLength === state?.selected?.contextLength;
  };
  const issues = {
    platform: 'Локальная установка поддерживается только в Windows и macOS.',
    architecture: 'Нужен 64-битный процессор x64 или ARM64.',
    macos: 'Для этой версии Ollama требуется macOS 14 или новее.',
    windows: 'Требуется Windows 10 22H2 или новее.',
    avx2: 'Для локального анализа на x64 требуется процессор с AVX2.',
    cores: 'Для локального анализа нужны минимум 4 логических ядра.',
    ram: 'Нужно не менее {0} ГиБ оперативной памяти.',
    model_ram: 'Для выбранной модели и контекста нужно примерно {0} ГиБ оперативной памяти. Выберите меньшую модель или контекст.',
    model_unknown: 'Не удалось проверить размер модели в каталоге Ollama. Проверьте название модели и доступ к интернету.',
    disk_unknown: 'Не удалось определить свободное место на диске. Установка заблокирована.',
    disk: 'Для установки и модели нужно {0} ГиБ свободного места на диске; доступно {1} ГиБ.',
    cpu_speed: 'Без совместимого GPU анализ может выполняться медленно. Видеопамять не прибавляется к оперативной памяти.',
    estimate: 'Расход памяти модели рассчитан с запасом. Перед переключением будет выполнен пробный запуск.',
  };
  const phases = {
    idle: 'Проверка компьютера и требований модели…', inspecting: 'Проверка компьютера и требований модели…',
    checked: 'Проверка пройдена. Можно продолжить.', blocked: 'Установка недоступна: требования не выполнены.',
    downloading: 'Загрузка Ollama…', installing: 'Установка Ollama в профиль пользователя…', starting: 'Запуск Ollama…',
    ready: 'Ollama запущена. Загрузите и проверьте выбранную модель.', pulling: 'Загрузка модели…',
    verifying: 'Пробный запуск модели…', model_ready: 'Модель проверена и готова к использованию.', cancelled: 'Загрузка отменена. Текущий LLM не изменён.',
    error: 'Не удалось завершить операцию. Можно повторить попытку.',
  };
  const errors = {
    model_invalid: 'Укажите название модели из каталога Ollama, например qwen3:4b.', context_invalid: 'Контекст должен быть от 4096 до 131072 токенов.',
    inspection: 'Не удалось проверить компьютер. Установка заблокирована.', integrity: 'Проверка загруженного файла не пройдена. Файл не запускался.',
    download: 'Не удалось загрузить Ollama. Проверьте соединение и повторите попытку.',
    installer: 'Установка в профиль пользователя не завершена. Проверьте доступ к папке пользователя.',
    not_installed: 'Ollama ещё не установлена.', not_running: 'Сначала запустите Ollama.',
    start_timeout: 'Ollama установлена, но не ответила вовремя. Повторите запуск.',
    model_pull: 'Не удалось загрузить модель. Проверьте соединение и свободное место.',
    model_test: 'Пробный запуск модели не удался. Выберите меньшую модель или контекст.',
    operation: 'Не удалось завершить операцию. Можно повторить попытку.', platform: issues.platform,
  };
  let runtimeRequested = false;
  function refreshRuntime() {
    if (document.hidden || card.hidden || runtimeRequested) return;
    runtimeRequested = true;
    send('runtime');
  }
  function renderRuntime(runtime) {
    runtimeRequested = false;
    el('localOllamaRuntimeStatus').textContent = tr(!runtime.available ? 'Ollama не отвечает' : runtime.models === null
      ? 'Ollama работает. Параметры моделей недоступны.' : runtime.models.length ? 'Ollama работает' : 'Ollama работает. Модели пока не загружены в память.');
    el('localOllamaRuntimeVersion').textContent = runtime.available ? tr('Версия: {0} · Адрес: {1}', runtime.version, runtime.endpoint) : '';
    const models = el('localOllamaRuntimeModels'); models.replaceChildren();
    const memory = bytes => Number.isFinite(bytes) ? tr('{0} ГиБ', (bytes / 1024 ** 3).toFixed(2)) : tr('Нет данных');
    for (const model of runtime.available && runtime.models || []) {
      const item = document.createElement('article'); item.className = 'ollama-runtime-model';
      const title = document.createElement('strong'); title.textContent = model.name; item.append(title);
      let processor = tr('Нет данных');
      if (model.size > 0 && Number.isFinite(model.sizeVRAM)) {
        const gpu = Math.max(0, Math.min(100, Math.round(model.sizeVRAM / model.size * 100)));
        processor = gpu === 100 ? '100% GPU' : gpu === 0 ? '100% CPU' : `${100 - gpu}% CPU / ${gpu}% GPU`;
      }
      for (const text of [tr('Размещение модели: {0}', processor), tr('Память: {0} · На GPU: {1}', memory(model.size), memory(model.sizeVRAM)),
        tr('Контекст: {0} · Квантизация: {1}', model.contextLength ?? tr('Нет данных'), model.quantization || tr('Нет данных'))]) {
        const line = document.createElement('p'); line.textContent = text; item.append(line);
      }
      models.append(item);
    }
  }
  setInterval(refreshRuntime, 10000);
  document.addEventListener('visibilitychange', refreshRuntime);
  function render() {
    if (!state) return;
    const inUse = localOllamaInUse();
    const busy = state.busy || saving || installing || awaiting, valid = matches(), allowed = valid && state.report?.eligible;
    el('localOllamaModel').disabled = busy;
    el('localOllamaContext').disabled = busy;
    el('llmProvider').disabled = installing || saving;
    el('localOllamaUse').disabled = inUse || busy || (!allowed && !state.error);
    el('localOllamaUse').textContent = tr(!inUse && state.error && !allowed ? 'Повторить проверку' : 'Использовать локально');
    el('localOllamaCancel').hidden = !state.busy || !['inspecting', 'downloading', 'pulling', 'verifying'].includes(state.phase);
    el('localOllamaCancel').textContent = tr('Отмена');
    el('localOllamaStatus').textContent = tr(saving ? 'Подключение локальной модели…' : inUse || connected ? 'Локальная модель подключена.' : state.error ? errors[state.error] || errors.operation : (!valid && !busy ? phases.idle : phases[state.phase] || phases.idle));
    // Stage weights show overall progress; percentages advance only on real events.
    // Installation and the inference check have no byte counters, so hold their milestone.
    const fraction = Number.isFinite(state.progress) ? Math.max(0, Math.min(1, state.progress)) : 0;
    const stages = { inspecting: 0, downloading: 5 + 25 * fraction, installing: 30,
      starting: 35, ready: 40, pulling: 40 + 50 * fraction, verifying: 90, model_ready: 95 };
    if ((installing && !awaiting) || saving) percent = Math.max(percent, saving ? 97 : stages[state.phase] || 0);
    if (connected) percent = 100;
    el('localOllamaProgressGroup').hidden = !installing && !saving && !connected;
    el('localOllamaProgress').value = Math.floor(percent);
    el('localOllamaPercent').textContent = Math.floor(percent) + '%';
    const checks = el('localOllamaChecks'); checks.replaceChildren();
    el('localOllamaSummary').textContent = '';
    if (valid && state.report) {
      const system = state.report.system;
      el('localOllamaSummary').textContent = tr('Память: {0} ГиБ · Логических ядер: {1} · Модель: {2} ГиБ',
        (system.ram / 1024 ** 3).toFixed(1), system.threads, ((state.report.modelBytes || 0) / 1024 ** 3).toFixed(1));
      for (const issue of [...state.report.blockers, ...state.report.warnings]) {
        const item = document.createElement('li'); item.textContent = tr(issues[issue.code] || issues.model_unknown, ...issue.args); checks.append(item);
      }
    }
  }
  window.addEventListener('message', event => {
    if (event.source !== window || event.origin !== location.origin || event.data?.channel !== channel || event.data?.from !== 'desktop' || !event.data.state) return;
    if (event.data.state.runtimeOnly) {
      setLocalOllamaRuntime(event.data.state.runtime);
      connected = localOllamaInUse();
      renderRuntime(event.data.state.runtime);
      render(); scheduleInspection();
      return;
    }
    if (!state && event.data.state.selected) {
      el("localOllamaModel").value=event.data.state.selected.model; el("localOllamaContext").value=event.data.state.selected.contextLength;
    }
    state = event.data.state;
    awaiting = false;
    enableLocalOllama();
    if (installing && !state.busy) {
      if (state.phase === 'model_ready' && matches() && state.modelReady && state.ready) void connect();
      else if (['error', 'blocked', 'cancelled'].includes(state.phase)) installing = false;
    }
    render();
    scheduleInspection();
  });
  function inspect() {
    if (localOllamaInUse() || !state || state.busy || installing || saving || awaiting || el('llmProvider').value !== 'local') return;
    const key = JSON.stringify(choice());
    if (key === inspectedChoice) return;
    inspectedChoice = key; awaiting = true; connected = false;
    send('inspect'); render();
  }
  function scheduleInspection() {
    clearTimeout(inspectTimer);
    inspectTimer = setTimeout(inspect, 350);
  }
  window.addEventListener('model-settings-rendered', () => {
    connected = localOllamaInUse();
    refreshRuntime();
    render();
    scheduleInspection();
  });
  el('llmProvider').addEventListener('change', () => {
    inspectedChoice = null;
    inspect();
  });
  for (const id of ['localOllamaModel', 'localOllamaContext']) el(id).addEventListener('input', () => {
    connected = false; render(); scheduleInspection();
  });
  el('localOllamaCancel').onclick = () => send('cancel');
  el('localOllamaUse').onclick = () => {
    if (localOllamaInUse() || state?.busy || installing || saving || awaiting || el('llmProvider').value !== 'local') return;
    if (!matches() || !state?.report?.eligible) { inspectedChoice = null; inspect(); return; }
    inspectedChoice = JSON.stringify(choice());
    installing = true; connected = false; percent = 0; awaiting = true;
    send('setup'); render();
  };
  async function connect() {
    if (saving) return;
    saving = true; render();
    try {
      const llm = { provider: 'ollama', base_url: 'http://127.0.0.1:11434', model: state.selected.model, context_length: state.selected.contextLength };
      const result = await request('/settings', { method: 'PUT', body: JSON.stringify({ settings: { llm } }) });
      // Preserve unrelated unsaved form edits, as well as existing API keys.
      settingsState = result.settings;
      setValue('llmProvider', 'local'); setValue('llmUrl', llm.base_url);
      setValue('llmModel', llm.model); setValue('contextLength', llm.context_length);
      setLocalOllamaRuntime({ available: true, installedModels: [llm.model], models: [] });
      connected = true;
      toast(tr('Локальная модель подключена.')); loadStatus();
      document.dispatchEvent(new CustomEvent('secretary:settings-saved', { detail: { llm: true } }));
    } catch (error) { toast(error.message, true); }
    finally { saving = false; installing = false; renderModelSettings(); render(); }
  }
  send('status');
})();
