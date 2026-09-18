const $ = (id) => document.getElementById(id);
let settingsState = null;
let sourcesState = [];
let tagsState = [];
let editingSourceId = null;

async function request(path, options = {}) {
  const response = await fetch(`/api/v1/admin${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = Array.isArray(body.detail)
      ? body.detail.map((item) => item.msg || String(item)).join("; ")
      : body.detail;
    throw new Error(detail || `Ошибка ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

function toast(message, error = false) {
  const node = $("toast");
  node.textContent = message;
  node.classList.toggle("error", error);
  node.classList.add("show");
  setTimeout(() => node.classList.remove("show"), 3200);
}

function setSourceFormError(message = "") {
  const node = $("sourceFormError");
  node.textContent = message;
  node.hidden = !message;
  if (message) {
    node.focus({ preventScroll: true });
    node.scrollIntoView({ block: "nearest" });
  }
}

const csv = (value) => (value || "").split(",").map((item) => item.trim()).filter(Boolean);
const listValues = (value) => (value || "").split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
const setValue = (id, value) => { $(id).value = value ?? ""; };

function populateSettings(data) {
  settingsState = data.settings;
  const s = data.settings;
  const localWeb = Boolean(data.local_web_only);
  $("mobileTab").hidden = false;
  $("directUnavailable").hidden = !localWeb;
  $("saveDirectSettings").disabled = localWeb;
  $("createMobileIdentity").disabled = localWeb;
  $("deploymentHint").textContent = localWeb
    ? "Локальная установка. Мобильный доступ через шлюз настраивается в разделе «Удалённое подключение»."
    : "Подключайте источники переписки и управляйте анализом прямо здесь. Доступ к панели защищён клиентским сертификатом.";
  $("devicesMetric").hidden = localWeb;
  $("statusMetrics").classList.toggle("local-web", localWeb);
  $("firebaseSettings").hidden = localWeb;
  $("localWebNotice").hidden = !localWeb;
  $("notificationsEyebrow").textContent = localWeb ? "WEB" : "FCM";
  $("dueSoonSetting").hidden = false;
  $("overdueSetting").hidden = false;
  $("scheduleLegend").textContent = localWeb ? "Обработка" : "Сроки";
  $("publicUrl").disabled = localWeb;
  setValue("identityNames", (s.identity.names || []).join(", "));
  setValue("timezone", s.server.timezone);
  setValue("publicUrl", s.server.public_url);
  setValue("workStart", s.calendar.workday_start);
  setValue("workEnd", s.calendar.workday_end);
  setValue("planTime", s.calendar.daily_plan_time);
  setValue("initialDays", s.communication_sources.initial_sync_days);
  setValue("nonWorkingDates", (s.calendar.non_working_dates || []).join(", "));
  setValue("workingDates", (s.calendar.working_dates || []).join(", "));
  setValue("llmUrl", s.llm.base_url);
  setValue("llmProvider", s.llm.provider || "ollama");
  setValue("llmApiKey", "");
  $("clearLlmApiKey").checked = false;
  $("llmKeyState").textContent = data.llm_api_key_configured ? "Ключ сохранён" : "Ключ не настроен";
  setValue("llmModel", s.llm.model);
  setValue("contextLength", s.llm.context_length);
  setValue("temperature", s.llm.temperature);
  setValue("autoConfidence", s.llm.auto_create_confidence);
  setValue("completionConfidence", s.llm.possible_completion_confidence);
  setValue("llmTimeout", s.llm.request_timeout_seconds);
  setValue("batchSize", s.worker.batch_size);
  setValue("attachmentMb", Math.round(s.document_parser.max_bytes / 1048576));
  setValue("attachmentCharacters", s.document_parser.max_characters);
  setValue("parserTimeout", s.document_parser.timeout_seconds);
  setValue("analysisStopWords", (s.analysis_filters?.stop_words || []).join("\n"));
  setValue("analysisExcludedAddresses", (s.analysis_filters?.excluded_addresses || []).join("\n"));
  setValue("dueSoonMinutes", s.notifications.due_soon_minutes);
  setValue("overdueHour", s.notifications.overdue_repeat_hour);
  setValue("rankingInterval", s.worker.ranking_interval_seconds);
  setValue("pollInterval", s.worker.poll_interval_seconds);
  $("firebaseState").textContent = data.firebase_configured ? "Ключ настроен" : "Ключ не настроен";
  $("firebaseState").classList.toggle("ok", data.firebase_configured);
}

async function saveSettings() {
  try {
    if (!settingsState) throw new Error("Настройки ещё не загружены");
    const fields = document.querySelectorAll("#analysis input, #notifications input");
    if (![...fields].every((field) => field.reportValidity())) return;
    const s = structuredClone(settingsState);
    s.identity.names = csv($("identityNames").value);
    s.server.timezone = $("timezone").value.trim();

    s.calendar.workday_start = $("workStart").value;
    s.calendar.workday_end = $("workEnd").value;
    s.calendar.daily_plan_time = $("planTime").value;
    s.calendar.non_working_dates = csv($("nonWorkingDates").value);
    s.calendar.working_dates = csv($("workingDates").value);
    s.communication_sources.initial_sync_days = Number($("initialDays").value);
    s.llm.base_url = $("llmUrl").value.trim();
    s.llm.provider = $("llmProvider").value;
    s.llm.model = $("llmModel").value.trim();
    s.llm.context_length = Number($("contextLength").value);
    s.llm.temperature = Number($("temperature").value);
    s.llm.auto_create_confidence = Number($("autoConfidence").value);
    s.llm.possible_completion_confidence = Number($("completionConfidence").value);
    s.llm.request_timeout_seconds = Number($("llmTimeout").value);
    s.worker.batch_size = Number($("batchSize").value);
    s.document_parser.max_bytes = Number($("attachmentMb").value) * 1048576;
    s.document_parser.max_characters = Number($("attachmentCharacters").value);
    s.document_parser.timeout_seconds = Number($("parserTimeout").value);
    s.analysis_filters = s.analysis_filters || {};
    s.analysis_filters.stop_words = listValues($("analysisStopWords").value);
    s.analysis_filters.excluded_addresses = listValues($("analysisExcludedAddresses").value);
    s.notifications.due_soon_minutes = Number($("dueSoonMinutes").value);
    s.notifications.overdue_repeat_hour = Number($("overdueHour").value);
    s.worker.ranking_interval_seconds = Number($("rankingInterval").value);
    s.worker.poll_interval_seconds = Number($("pollInterval").value);
    const result = await request("/settings", { method: "PUT", body: JSON.stringify({
      settings: s,

      llm_api_key: $("llmApiKey").value.trim() || null,
      clear_llm_api_key: $("clearLlmApiKey").checked,
    }) });

    populateSettings(result);
    const scan = result.filter_reconciliation;
    const scanMessage = scan
      ? ` Архив проверен: исключено ${scan.skipped + scan.ignored}, возвращено в очередь ${scan.requeued}.`
      : "";
    toast(`Настройки сохранены.${scanMessage}`);
    loadStatus();
  } catch (error) { toast(error.message, true); }
}

const sourceKind = { imap: "IMAP", exchange: "Exchange EWS", mts_link: "МТС Линк", external_tasks: "Внешний API задач" };
function sourceEndpoint(source) {
  return source.settings.host || source.settings.ews_url || source.settings.base_url || "Адрес не указан";
}
function escapeText(value) {
  const node = document.createElement("span"); node.textContent = value ?? ""; return node.innerHTML;
}
function escapeAttr(value) {
  return escapeText(value).replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}
function renderSources() {
  const list = $("sourceList");
  if (!sourcesState.length) {
    list.innerHTML = '<div class="empty">Источников пока нет. Подключите почту или коммуникационную платформу.</div>';
    return;
  }
  list.innerHTML = sourcesState.map((source) => `
    <article class="source-card">
      <div class="source-card-header"><div><h3>${escapeText(source.label)}</h3><p>${sourceKind[source.source_type] || source.source_type}</p></div><span class="badge ${source.enabled ? "" : "off"}">${source.enabled ? "ВКЛЮЧЁН" : "ВЫКЛЮЧЕН"}</span></div>
      ${source.tags.length ? `<div class="source-tags">${source.tags.map((tag) => `<span>${escapeText(tag.name)}</span>`).join("")}</div>` : ""}
      <div class="source-details"><div>Подключение<strong>${escapeText(sourceEndpoint(source))}</strong></div><div>Последняя синхронизация<strong>${source.last_sync_at ? new Date(source.last_sync_at).toLocaleString() : "ещё не запускалась"}</strong></div></div>
      ${source.source_type === "mts_link" ? `<p class="credential-state">${source.refresh_token_configured ? "SSO подключён · токены обновляются автоматически" : "Автоматическое обновление токенов не настроено"}</p>` : ""}
      ${source.last_error ? `<div class="source-error">Последняя ошибка: ${escapeText(source.last_error)}</div>` : ""}
      <div class="card-actions">${source.source_type === "mts_link" ? `<button data-action="mts-sso" data-id="${source.id}">Войти через SSO</button>` : ""}<button data-action="test" data-id="${source.id}">Проверить</button><button data-action="edit" data-id="${source.id}">Изменить</button><button class="danger" data-action="delete" data-id="${source.id}">Удалить</button></div>
    </article>`).join("");
}

async function loadSources() {
  try { sourcesState = await request("/sources"); renderSources(); } catch (error) { toast(error.message, true); }
}

function renderTags() {
  const list = $("tagList");
  if (!tagsState.length) {
    list.innerHTML = '<div class="empty">Справочник пуст. Добавьте первый тег.</div>';
    return;
  }
  list.innerHTML = tagsState.map((tag) => `
    <article class="tag-row" data-tag-id="${tag.id}">
      <input class="tag-name" maxlength="100" value="${escapeAttr(tag.name)}" aria-label="Название тега">
      <span class="tag-usage">Источников: ${tag.source_count}</span>
      <div class="card-actions"><button data-tag-action="save" data-id="${tag.id}">Сохранить</button><button class="danger" data-tag-action="delete" data-id="${tag.id}">Удалить</button></div>
    </article>`).join("");
}

async function loadTags() {
  try { tagsState = await request("/tags"); renderTags(); } catch (error) { toast(error.message, true); }
}

async function createTag(event) {
  event.preventDefault();
  const input = $("newTagName");
  const name = input.value.trim();
  if (!name) return;
  try {
    await request("/tags", { method: "POST", body: JSON.stringify({ name }) });
    input.value = "";
    toast("Тег добавлен");
    await loadTags();
  } catch (error) { toast(error.message, true); }
}

async function tagAction(event) {
  const button = event.target.closest("button[data-tag-action]");
  if (!button) return;
  const tag = tagsState.find((item) => item.id === button.dataset.id);
  if (!tag) return;
  if (button.dataset.tagAction === "delete") {
    const suffix = tag.source_count
      ? ` Он будет снят с ${tag.source_count} источник(ов).`
      : "";
    if (!confirm(`Удалить тег «${tag.name}»?${suffix}`)) return;
    try {
      await request(`/tags/${tag.id}`, { method: "DELETE" });
      toast("Тег удалён");
      await Promise.all([loadTags(), loadSources()]);
    } catch (error) { toast(error.message, true); }
    return;
  }
  const row = button.closest(".tag-row");
  const name = row.querySelector(".tag-name").value.trim();
  if (!name) return toast("Название тега не может быть пустым", true);
  button.disabled = true;
  try {
    await request(`/tags/${tag.id}`, { method: "PUT", body: JSON.stringify({ name }) });
    toast("Тег сохранён");
    await Promise.all([loadTags(), loadSources()]);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

function renderSourceTagPicker(selectedTags = []) {
  const selected = new Set(selectedTags.map((tag) => tag.id));
  const picker = $("sourceTagPicker");
  if (!tagsState.length) {
    picker.innerHTML = '<p class="hint">Справочник пуст. Сначала добавьте тег во вкладке «Теги».</p>';
    return;
  }
  picker.innerHTML = tagsState.map((tag) => `
    <label class="tag-option"><input type="checkbox" value="${tag.id}" ${selected.has(tag.id) ? "checked" : ""}><span>${escapeText(tag.name)}</span></label>`).join("");
}

function sourceFields(type, values = {}) {
  if (type === "external_tasks") return `<p class="hint">Задачи загружаются через защищённый API по идентификатору источника. Пароль или токен не требуется; доступ защищён клиентским сертификатом.</p>`;
  if (type === "imap") return `
    <div class="inline"><label>IMAP-сервер<input data-setting="host" value="${escapeAttr(values.host || "")}" required></label><label>Порт<input data-setting="port" type="number" value="${Number(values.port) || 993}" required></label></div>
    <label class="toggle"><input data-setting="tls" type="checkbox" ${values.tls !== false ? "checked" : ""}><span>TLS включён</span></label>
    <label>Имя пользователя<input data-setting="username" value="${escapeAttr(values.username || "")}" required></label>
    <div class="inline"><label>Входящие<input data-setting="inbox_folder" value="${escapeAttr(values.inbox_folder || "INBOX")}"></label><label>Отправленные<input data-setting="sent_folder" value="${escapeAttr(values.sent_folder || "Sent")}"></label></div>`;
  if (type === "exchange") return `
    <label>URL EWS<input data-setting="ews_url" type="url" value="${escapeAttr(values.ews_url || "")}" placeholder="https://mail.example.ru/EWS/Exchange.asmx" required></label>
    <label>Основной почтовый адрес<input data-setting="primary_smtp_address" type="email" value="${escapeAttr(values.primary_smtp_address || "")}" required></label>
    <div class="inline"><label>Имя пользователя<input data-setting="username" value="${escapeAttr(values.username || "")}" required></label><label>Аутентификация<select data-setting="auth_type"><option value="ntlm">NTLM</option><option value="basic">Basic</option><option value="digest">Digest</option></select></label></div>`;
  return `<label>URL шлюза МТС Линк<input data-setting="base_url" type="url" value="${escapeAttr(values.base_url || "https://gw.mts-link.ru")}" required></label><label>Интервал опроса, секунд<input data-setting="poll_interval_seconds" type="number" min="10" value="${Number(values.poll_interval_seconds) || 900}" required></label><section class="mts-auth-options"><p id="sourceMtsExtensionStatus" role="status" aria-live="polite">Проверяем расширение «AI Секретарь»…</p><button type="button" id="sourceMtsSso" class="primary" disabled>Войти через SSO</button><p id="sourceMtsSsoHint" class="hint" hidden>SSO сохранит access token и refresh token для автоматического обновления. Перед входом настройки источника сохранятся.</p><div id="sourceMtsFallback" hidden><p>Введите access token ниже или установите расширение «AI Секретарь» для входа через корпоративный SSO.</p><details><summary>Установить расширение «AI Секретарь»</summary><p class="hint"><a href="/api/v1/admin/mts-link/extension.zip">Скачать расширение</a>. Распакуйте архив, откройте chrome://extensions или edge://extensions, включите режим разработчика и выберите «Загрузить распакованное расширение». Затем нажмите значок «AI Секретарь» на этой странице.</p></details><p class="hint">Если расширение уже установлено, нажмите его значок в панели браузера, чтобы подключить к странице.</p><button type="button" id="sourceMtsRecheck">Проверить ещё раз</button></div><p class="hint">Access token, введённый вручную, действует только 84 часа с момента выдачи, а не сохранения в админке. Затем его нужно заменить. Замена вручную отключает автоматическое обновление токенов.</p></section>`;
}

const MTS_LINK_DEFAULT_PATTERN = String.raw`^https://mts\.mts-link\.ru/j/MTC/(?P<meeting_id>\d+)(?:/[^?#]*)?(?:\?[^#]*)?(?:#.*)?$`;
let sourceLinkDrafts = {};
let sourceLinkType = "imap";
function defaultSourceLinkPatterns(type) {
  return type === "mts_link" ? [MTS_LINK_DEFAULT_PATTERN] : [];
}

function openSourceDialog(source = null) {
  sourceLinkCheckSerial++;
  editingSourceId = source?.id || null;
  setSourceFormError();
  $("sourceDialogTitle").textContent = source ? "Изменить источник" : "Подключить источник";
  $("sourceType").value = source?.source_type || "imap";
  $("sourceType").disabled = Boolean(source);
  setValue("sourceId", source?.id || ""); $("sourceId").readOnly = Boolean(source);
  setValue("sourceLabel", source?.label || ""); $("sourceEnabled").checked = source?.enabled ?? true;
  renderSourceTagPicker(source?.tags || []);
  $("sourceCredential").value = "";
  sourceLinkType = $("sourceType").value;
  sourceLinkDrafts = {};
  const patterns = source?.settings?.link_patterns ?? defaultSourceLinkPatterns(sourceLinkType);
  $("sourceLinkPatterns").value = patterns.join("\n");
  $("sourceLinkRules").open = Boolean(patterns.length);
  $("sourceLinkTestUrl").value = "";
  $("sourceLinkTestResult").textContent = "";
  $("credentialHint").textContent = source?.credential_configured ? "оставьте пустым, чтобы сохранить текущий" : "обязателен при первом подключении";
  $("sourceFields").innerHTML = sourceFields($("sourceType").value, source?.settings || {});
  const authType = document.querySelector('#sourceFields [data-setting="auth_type"]');
  if (authType) authType.value = source?.settings?.auth_type || "ntlm";
  $("sourceDialog").showModal();
  checkMtsExtension();
}

function readSourceSettings() {
  const result = {};
  document.querySelectorAll("#sourceFields [data-setting]").forEach((input) => {
    let value = input.type === "checkbox" ? input.checked : input.value.trim();
    if (input.type === "number") value = Number(value);
    result[input.dataset.setting] = value;
  });
  const patterns = listValuesByLine($("sourceLinkPatterns").value);
  result.link_patterns = patterns;
  return result;
}

function listValuesByLine(value) {
  return value.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
}

let sourceLinkCheckSerial = 0;
async function testSourceLink() {
  const serial = ++sourceLinkCheckSerial;
  const url = $("sourceLinkTestUrl").value.trim();
  if (!url) { $("sourceLinkTestResult").textContent = "Введите ссылку для проверки."; return; }
  $("sourceLinkTestResult").textContent = "Проверяем…";
  const body = { source_id: $("sourceId").value.trim() || "new_source", source_type: $("sourceType").value,
    patterns: listValuesByLine($("sourceLinkPatterns").value), url };
  try {
    const data = await request("/source-link-preview", { method: "POST", body: JSON.stringify(body) });
    if (serial !== sourceLinkCheckSerial) return;
    $("sourceLinkTestResult").textContent = data.matches.length
      ? data.matches.map(match => `Источник: ${$("sourceType").selectedOptions[0].textContent} (${match.source_id}); meeting_id: ${match.meeting_id}`).join("\n")
      : "Ни одно правило не подошло к ссылке.";
  } catch (error) { if (serial === sourceLinkCheckSerial) $("sourceLinkTestResult").textContent = error.message; }
}

async function saveSource(event) {
  event.preventDefault();
  setSourceFormError();
  const submit = $("sourceSubmit");
  submit.disabled = true;
  submit.textContent = "Сохраняем…";
  const tag_ids = [...document.querySelectorAll('#sourceTagPicker input[type="checkbox"]:checked')].map((input) => input.value);
  const payload = { id: $("sourceId").value.trim(), label: $("sourceLabel").value.trim(), source_type: $("sourceType").value, enabled: $("sourceEnabled").checked, settings: readSourceSettings(), credential: $("sourceCredential").value || null, tag_ids };
  try {
    await request(editingSourceId ? `/sources/${editingSourceId}` : "/sources", { method: editingSourceId ? "PUT" : "POST", body: JSON.stringify(payload) });
    $("sourceDialog").close(); toast("Источник сохранён"); await Promise.all([loadSources(), loadTags()]); loadStatus();
  } catch (error) {
    setSourceFormError(error.message);
  } finally {
    submit.disabled = false;
    submit.textContent = "Сохранить источник";
  }
}

async function sourceAction(event) {
  const button = event.target.closest("button[data-action]"); if (!button) return;
  const source = sourcesState.find((item) => item.id === button.dataset.id); if (!source) return;
  if (button.dataset.action === "edit") return openSourceDialog(source);
  if (button.dataset.action === "mts-sso") return openMtsSso(source);
  if (button.dataset.action === "delete") {
    if (!confirm(`Удалить источник «${source.label}» вместе со всеми его переписками, задачами и вложениями? Ручные задачи сохранятся.`)) return;
    try { await request(`/sources/${source.id}`, { method: "DELETE" }); toast("Источник и связанные данные удалены"); await loadSources(); loadStatus(); } catch (error) { toast(error.message, true); }
    return;
  }
  button.disabled = true; button.textContent = "Проверяем…";
  try { await request(`/sources/${source.id}/test`, { method: "POST" }); toast("Подключение работает"); } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "Проверить"; await loadSources(); }
}

async function loadStatus() {
  try {
    const status = await request("/status");
    $("healthDot").classList.add("ok"); $("healthText").textContent = "Сервер работает";
    $("metricSources").textContent = status.sources; $("metricTasks").textContent = status.tasks; $("metricDevices").textContent = status.devices; $("metricOllama").textContent = status.ollama;
  } catch (_) { $("healthText").textContent = "Сервер недоступен"; }
}

function registerModelContextTools() {
  const context = document.modelContext;
  if (!context?.registerTool) return;
  const lifecycle = new AbortController();
  window.addEventListener("pagehide", () => lifecycle.abort(), { once: true });
  const register = (tool) => {
    try {
      Promise.resolve(context.registerTool(tool, { signal: lifecycle.signal })).catch(() => {});
    } catch (_) {
      // WebMCP is optional; the visible interface and REST API remain available.
    }
  };

  register({
    name: "read_communication_sources",
    title: "Прочитать источники Improver",
    description: "Возвращает настроенные IMAP, Exchange и МТС Линк адаптеры без секретов.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    annotations: { readOnlyHint: true, untrustedContentHint: true },
    async execute() {
      const sources = await request("/sources");
      sourcesState = sources;
      renderSources();
      return { sources };
    },
  });

  register({
    name: "save_communication_source",
    title: "Сохранить источник Improver",
    description: "Создаёт или обновляет один адаптер переписки и обновляет видимый список.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string", pattern: "^[a-zA-Z0-9_-]+$", maxLength: 128 },
        label: { type: "string", minLength: 1, maxLength: 255 },
        source_type: { type: "string", enum: ["imap", "exchange", "mts_link", "external_tasks"] },
        enabled: { type: "boolean" },
        settings: { type: "object" },
        credential: { type: ["string", "null"] },
        tag_ids: { type: "array", items: { type: "string", format: "uuid" } },
      },
      required: ["id", "label", "source_type", "enabled", "settings"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: false, untrustedContentHint: true },
    async execute(input) {
      if (!input || typeof input !== "object" || Array.isArray(input) ||
          !input.id || !input.label || !input.source_type ||
          !input.settings || typeof input.settings !== "object" || Array.isArray(input.settings)) {
        throw new Error("Неверные параметры источника");
      }
      const current = await request("/sources");
      const exists = current.some((source) => source.id === input.id);
      const source = await request(`/sources/${exists ? input.id : ""}`.replace(/\/$/, ""), {
        method: exists ? "PUT" : "POST",
        body: JSON.stringify({ ...input, credential: input.credential || null }),
      });
      await loadSources();
      await loadStatus();
      return { id: source.id, saved: true, credential_configured: source.credential_configured };
    },
  });
}

// The server PNG uses six pixels per QR module. Preserve integer module widths
// when fitting it to the page; arbitrary CSS scaling makes dense codes unreadable.
function fitConnectionQR(image) {
  if (!image.getAttribute("src") || !image.naturalWidth || image.naturalWidth % 6 !== 0) return;
  const available = Math.min(620, image.parentElement.clientWidth);
  if (!available) return;
  const modules = image.naturalWidth / 6;
  image.style.width = `${modules * Math.max(1, Math.floor(available / modules))}px`;
}
for (const id of ["mobileIdentityQR", "gatewayQR"]) $(id).addEventListener("load", () => fitConnectionQR($(id)));
window.addEventListener("resize", () => {
  for (const id of ["mobileIdentityQR", "gatewayQR"]) fitConnectionQR($(id));
});

let mobileQRSerial = 0;
let mobileQRTimer = null;
function hideMobileIdentity() {
  mobileQRSerial++;
  clearTimeout(mobileQRTimer);
  $("mobileIdentityQR").removeAttribute("src");
  $("mobileIdentityInfo").textContent = "";
  $("mobileIdentityResult").hidden = true;
  $("gatewayQR").removeAttribute("src");
  $("gatewayQRResult").hidden = true;
}
async function createMobileIdentity(event) {
  event.preventDefault();
  hideMobileIdentity();
  const serial = mobileQRSerial;
  const button = $("createMobileIdentity");
  button.disabled = true;
  $("mobileIdentityError").textContent = "";
  try {
    if (settingsState?.server?.public_url && $("publicUrl").value.trim().replace(/\/$/, "") !== settingsState.server.public_url.replace(/\/$/, "")) throw new Error("Сначала сохраните адрес прямого подключения");
    const result = await request("/mobile-identity", {
      method: "POST", cache: "no-store",
      body: JSON.stringify({}),
    });
    if (serial !== mobileQRSerial || document.hidden) return;
    $("mobileIdentityQR").src = result.qr_image;
    $("mobileIdentityInfo").textContent = `${result.server_url} · Сертификат действует до ${new Date(result.expires_at).toLocaleDateString("ru-RU")}`;
    $("mobileIdentityResult").hidden = false;
    mobileQRTimer = setTimeout(hideMobileIdentity, 120_000);
  } catch (error) {
    if (serial === mobileQRSerial) $("mobileIdentityError").textContent = error.message;
  } finally { button.disabled = false; }
}
$("mobileIdentityForm").addEventListener("submit", createMobileIdentity);
$("hideMobileIdentity").addEventListener("click", hideMobileIdentity);
window.addEventListener("pagehide", hideMobileIdentity);
document.addEventListener("visibilitychange", () => { if (document.hidden) hideMobileIdentity(); });

document.querySelectorAll(".tab[data-panel]").forEach((tab) => tab.addEventListener("click", () => {
  hideMobileIdentity();
  document.querySelectorAll(".tab[data-panel], .panel").forEach((node) => node.classList.remove("active"));
  tab.classList.add("active"); $(tab.dataset.panel).classList.add("active");
  if (tab.dataset.panel === "mobile") loadGateway();
}));
document.querySelectorAll(".save-settings").forEach((button) => button.addEventListener("click", saveSettings));
$("addSource").addEventListener("click", () => openSourceDialog());
$("closeDialog").addEventListener("click", () => $("sourceDialog").close());
$("cancelDialog").addEventListener("click", () => $("sourceDialog").close());
$("sourceType").addEventListener("change", (event) => {
  sourceLinkCheckSerial++;
  sourceLinkDrafts[sourceLinkType] = $("sourceLinkPatterns").value;
  sourceLinkType = event.target.value;
  $("sourceLinkPatterns").value = sourceLinkDrafts[sourceLinkType] ?? defaultSourceLinkPatterns(sourceLinkType).join("\n");
  $("sourceLinkRules").open = Boolean($("sourceLinkPatterns").value);
  $("sourceLinkTestResult").textContent = "";
  setSourceFormError();
  $("sourceFields").innerHTML = sourceFields(sourceLinkType);
  checkMtsExtension();
});
$("sourceForm").addEventListener("input", () => setSourceFormError());
$("sourceForm").addEventListener("submit", saveSource);
$("sourceLinkTest").addEventListener("click", testSourceLink);
$("sourceLinkPatterns").addEventListener("input", () => { sourceLinkCheckSerial++; $("sourceLinkTestResult").textContent = ""; });
$("sourceLinkTestUrl").addEventListener("input", () => { sourceLinkCheckSerial++; $("sourceLinkTestResult").textContent = ""; });
$("sourceLinkExample").addEventListener("click", () => {
  sourceLinkCheckSerial++;
  $("sourceLinkPatterns").value = MTS_LINK_DEFAULT_PATTERN;
  $("sourceLinkTestResult").textContent = "Пример добавлен. Проверьте ссылку и сохраните источник.";
});
$("sourceList").addEventListener("click", sourceAction);
$("tagCreateForm").addEventListener("submit", createTag);
$("tagList").addEventListener("click", tagAction);
registerModelContextTools();

request("/settings").then(populateSettings).catch((error) => toast(error.message, true));
loadSources();
loadTags();
loadStatus();


async function saveDirectSettings(event) {
  event.preventDefault();
  try {
    if (!settingsState) throw new Error("Настройки ещё не загружены");
    const url = new URL($("publicUrl").value.trim());
    if (url.protocol !== "https:" || url.username || url.password || (url.pathname !== "/" && url.pathname !== "") || url.search || url.hash) throw new Error("Укажите HTTPS-адрес без пути и параметров");
    const result = await request("/settings", { method: "PUT", body: JSON.stringify({
      settings: { server: { public_url: url.origin } },
      firebase_credentials_json: $("firebaseJson").value.trim() || null,
    }) });
    $("firebaseJson").value = "";
    settingsState.server.public_url = result.settings.server.public_url;
    $("publicUrl").value = result.settings.server.public_url;
    $("firebaseState").textContent = result.firebase_configured ? "Ключ настроен" : "Ключ не настроен";
    $("firebaseState").classList.toggle("ok", result.firebase_configured);
    hideMobileIdentity();
    toast("Настройки прямого подключения сохранены");
  } catch (error) { toast(error.message, true); }
}
$("directSettingsForm").addEventListener("submit", saveDirectSettings);
let gatewayState = null;
let gatewayBusy = false;
function renderGateway(state) {
  const previous = gatewayState;
  if (previous && previous.installation_id !== state.installation_id) hideMobileIdentity();
  gatewayState = state;
  const labels = { not_configured: "Шлюз не настроен", enrollment_incomplete: "Регистрация не завершена", paused: "Доступ приостановлен", reconnecting: "Подключение к шлюзу…", connected: "Сервер подключён к шлюзу" };
  $("gatewayStatus").textContent = labels[state.state] || "Состояние неизвестно";
  $("gatewayUID").value = state.installation_id || "";
  // Polling must not overwrite an address the user is editing.
  if (state.gateway && (!previous || previous.gateway !== state.gateway)) $("gatewayAddress").value = state.gateway;
  const ready = Boolean(state.qr_available);
  const incomplete = state.state === "enrollment_incomplete";
  $("gatewayToggle").hidden = !ready;
  $("gatewayToggle").textContent = state.enabled ? "Приостановить доступ" : "Возобновить доступ";
  $("gatewayShowQR").disabled = gatewayBusy || !ready;
  $("gatewayReregister").hidden = !ready && !incomplete;
  $("gatewayRecovery").hidden = !ready && !incomplete;
  $("gatewayEnroll").hidden = ready || incomplete;
  $("gatewayEnroll").disabled = gatewayBusy || !state.installation_id;
  $("gatewayAddress").disabled = gatewayBusy;
  for (const id of ["gatewayToggle", "gatewayReregister"]) $(id).disabled = gatewayBusy;
}
let gatewayLoadSerial = 0;
async function loadGateway() {
  if (gatewayBusy) return;
  const serial = ++gatewayLoadSerial;
  try {
    const state = await request("/gateway", { cache: "no-store" });
    if (serial === gatewayLoadSerial && !gatewayBusy) renderGateway(state);
  } catch (error) { if (serial === gatewayLoadSerial) $("gatewayError").textContent = error.message; }
}
async function gatewayAction(path, body, method = "POST") {
  if (gatewayBusy) return;
  gatewayBusy = true;
  ++gatewayLoadSerial;
  hideMobileIdentity();
  $("gatewayError").textContent = "";
  document.querySelectorAll("#gatewayConnection button").forEach(b => { b.disabled = true; });
  $("gatewayAddress").disabled = true;
  try {
    renderGateway(await request(path, { method, body: body ? JSON.stringify(body) : undefined }));
    if (path === "/gateway/reregister") toast("Новый UID и сертификаты получены. Подключите устройства по новому QR-коду");
  } catch (error) { $("gatewayError").textContent = error.message; }
  finally {
    gatewayBusy = false;
    document.querySelectorAll("#gatewayConnection button").forEach(b => { b.disabled = false; });
    $("gatewayAddress").disabled = false;
    if (gatewayState) renderGateway(gatewayState);
    await loadGateway();
  }
}
$("gatewayReregister").addEventListener("click", () => {
  if ($("gatewayForm").reportValidity()) gatewayAction("/gateway/reregister", { gateway: $("gatewayAddress").value.trim() });
});
$("gatewayToggle").addEventListener("click", () => gatewayAction("/gateway", { enabled: !gatewayState.enabled }, "PUT"));
$("gatewayForm").addEventListener("submit", event => {
  event.preventDefault();
  if (gatewayState?.qr_available || gatewayState?.state === "enrollment_incomplete") return;
  gatewayAction("/gateway/enroll", { gateway: $("gatewayAddress").value.trim() });
});
$("gatewayShowQR").addEventListener("click", async () => {
  hideMobileIdentity();
  const serial = mobileQRSerial;
  try {
    const result = await request("/gateway/qr", { method: "POST", cache: "no-store" });
    if (serial !== mobileQRSerial) return;
    $("gatewayQR").src = result.qr_image;
    $("gatewayQRResult").hidden = false;
    mobileQRTimer = setTimeout(hideMobileIdentity, 120000);
  } catch (error) { $("gatewayError").textContent = error.message; }
});
$("gatewayHideQR").addEventListener("click", hideMobileIdentity);
const connectionTabs = [...document.querySelectorAll("[data-connection]")];
connectionTabs.forEach((tab, index) => {
  tab.addEventListener("click", () => {
    hideMobileIdentity();
    connectionTabs.forEach(item => {
      const selected = item === tab;
      item.setAttribute("aria-selected", String(selected)); item.tabIndex = selected ? 0 : -1;
      $(item.getAttribute("aria-controls")).hidden = !selected;
    });
    if (tab.dataset.connection === "gateway") loadGateway();
  });
  tab.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? connectionTabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + connectionTabs.length) % connectionTabs.length;
    connectionTabs[next].focus(); connectionTabs[next].click();
  });
});
setInterval(() => { if (!document.hidden && $("mobile").classList.contains("active") && !$("gatewayConnection").hidden) loadGateway(); }, 10000);
