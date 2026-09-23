var tr = globalThis.SecretaryI18n?.t || ((s, ...a) => s.replace(/\{(\d+)\}/g, (m, i) => i < a.length ? String(a[i]) : m));
const mtsSsoChannel = "improver-mts-sso-v1";
let mtsExtensionReady = false;
let mtsNativeSSO = false;
let mtsLogin = null;
let mtsExtensionCheckTimer;
const mtsSavedEmails = new Map();
function mtsEmailKey(sourceId) {
  return `improver-mts-sso-email:${sourceId}`;
}
function savedMtsEmail(sourceId) {
  if (mtsSavedEmails.has(sourceId)) return mtsSavedEmails.get(sourceId);
  try { return localStorage.getItem(mtsEmailKey(sourceId))?.slice(0, 320) ?? null; }
  catch { return null; }
}
function rememberMtsEmail() {
  if (!mtsLogin) return;
  const email = $("mtsSsoEmail").value.trim();
  if (!email && !mtsLogin.emailEdited) return;
  mtsSavedEmails.set(mtsLogin.sourceId, email);
  try {
    localStorage.setItem(mtsEmailKey(mtsLogin.sourceId), email);
  } catch { /* Keep the email in memory when browser storage is unavailable. */ }
}
async function restoreMtsEmail(login) {
  try {
    const data = await request(`/sources/${login.sourceId}/mts-link/login-email`);
    if (mtsLogin !== login || login.emailEdited || $("mtsSsoEmail").value.trim() || !data.email) return;
    $("mtsSsoEmail").value = data.email;
    rememberMtsEmail();
  } catch { /* Manual entry remains available if the saved login has expired. */ }
}
function renderMtsExtension(checking = false) {
  const ready = mtsExtensionReady;
  const status = $("sourceMtsExtensionStatus");
  if (status) {
    status.textContent = checking ? tr("Проверяем доступность входа…") : ready
      ? mtsNativeSSO ? tr("Вход через МТС Линк доступен в приложении. Расширение не требуется.")
        : tr("Расширение «AI Секретарь» подключено. Доступен вход через SSO.")
      : tr("Расширение «AI Секретарь» не обнаружено на этой странице.");
    $("sourceMtsSso").disabled = !ready;
    $("sourceMtsSsoHint").hidden = !ready;
    $("sourceMtsFallback").hidden = checking || ready;
  }
  $("mtsSsoFallback").hidden = checking || ready;
  $("mtsSsoEmail").disabled = !ready;
  $("mtsSsoFind").disabled = !ready || Boolean(mtsLogin?.busy);
}
function checkMtsExtension() {
  clearTimeout(mtsExtensionCheckTimer);
  mtsExtensionReady = false;
  $("credentialLabel").textContent = $("sourceType").value === "mts_link" ? "Access token" : tr("Пароль или токен");
  renderMtsExtension(true);
  mtsExtensionCheckTimer = setTimeout(() => renderMtsExtension(), 800);
  mtsBridge("ping");
}
function mtsMessage(message, error = false) {
  $("mtsSsoStatus").textContent = message;
  $("mtsSsoStatus").classList.toggle("source-error", error);
}
function mtsBridge(action, extra = {}) {
  window.postMessage({ channel: mtsSsoChannel, from: "admin", action, ...extra }, location.origin);
}
function cancelMtsLogin() {
  rememberMtsEmail();
  if (mtsLogin?.flowId) mtsBridge("cancel", { flowId: mtsLogin.flowId });
  mtsLogin = null;
}
function openMtsSso(source, enableSource = source.enabled) {
  cancelMtsLogin();
  mtsLogin = { sourceId: source.id, source, enableSource };
  $("mtsSsoTitle").textContent = tr("Вход МТС Линк — {0}", source.label);
  const email = savedMtsEmail(source.id);
  $("mtsSsoEmail").value = email ?? "";
  if (email === null && source.credential_configured) void restoreMtsEmail(mtsLogin);
  $("mtsSsoChoices").replaceChildren();
  $("mtsSsoFind").disabled = !mtsExtensionReady;
  $("mtsSsoCancel").disabled = false;
  mtsMessage(mtsExtensionReady ? tr("Введите рабочий email для корпоративного SSO.") : tr("Нажмите значок расширения в панели браузера на этой странице."));
  $("mtsSsoDialog").showModal();
  checkMtsExtension();
}
async function mtsSsoFromSourceForm() {
  if (!mtsExtensionReady) return;
  if (!$("sourceForm").reportValidity()) return;
  const button = $("sourceMtsSso");
  button.disabled = true;
  setSourceFormError();
  try {
    const settings = readSourceSettings();
    if (settings.base_url !== "https://gw.mts-link.ru") throw new Error(tr("SSO поддерживается для шлюза https://gw.mts-link.ru"));
    const existing = sourcesState.find(s => s.id === editingSourceId);
    const desiredEnabled = $("sourceEnabled").checked;
    const credential = $("sourceCredential").value || null;
    const payload = {
      id: $("sourceId").value.trim(), label: $("sourceLabel").value.trim(), source_type: "mts_link",
      enabled: existing?.credential_configured || credential ? desiredEnabled : false,
      settings, credential,
      tag_ids: [...document.querySelectorAll('#sourceTagPicker input:checked')].map(input => input.value)
    };
    const source = await request(editingSourceId ? `/sources/${editingSourceId}` : "/sources", {
      method: editingSourceId ? "PUT" : "POST", body: JSON.stringify(payload)
    });
    $("sourceDialog").close();
    await loadSources();
    openMtsSso(source, desiredEnabled);
  } catch (error) { setSourceFormError(error.message); }
  finally { button.disabled = false; }
}
async function findMtsOrganizations(event) {
  event.preventDefault();
  const login = mtsLogin;
  if (!login || !mtsExtensionReady) return;
  const email = $("mtsSsoEmail").value.trim();
  rememberMtsEmail();
  $("mtsSsoFind").disabled = true;
  $("mtsSsoChoices").replaceChildren();
  mtsMessage(tr("Ищем способы корпоративного входа…"));
  try {
    const data = await request(`/sources/${login.sourceId}/mts-link/organizations`, { method: "POST", body: JSON.stringify({ email }) });
    if (mtsLogin !== login) return;
    if (!data.choices.length) { mtsMessage(tr("Для этого email не найдены способы SAML/OAuth. Проверьте адрес."), true); return; }
    mtsMessage(tr("Выберите организацию. Вход откроется в отдельном окне."));
    for (const choice of data.choices) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${choice.name} (${choice.kind})`;
      button.addEventListener("click", () => startMtsLogin(login, email, choice));
      $("mtsSsoChoices").append(button);
    }
  } catch (error) { if (mtsLogin === login) mtsMessage(error.message, true); }
  finally { if (mtsLogin === login) $("mtsSsoFind").disabled = !mtsExtensionReady; }
}
async function startMtsLogin(login, email, choice) {
  if (mtsLogin !== login || login.busy) return;
  if (login.flowId) mtsBridge("cancel", { flowId: login.flowId });
  login.flowId = crypto.randomUUID();
  login.busy = true;
  $("mtsSsoFind").disabled = true;
  $("mtsSsoChoices").querySelectorAll("button").forEach(b => { b.disabled = true; });
  mtsMessage(tr("Открываем корпоративный вход…"));
  try {
    const data = await request(`/sources/${login.sourceId}/mts-link/start`, {
      method: "POST", body: JSON.stringify({ email, organization_id: choice.organization_id,
        method_index: choice.method_index, enable_source: login.enableSource })
    });
    if (mtsLogin !== login) return;
    login.ticket = data.ticket;
    mtsBridge("start", { flowId: login.flowId, authorizationUrl: data.authorization_url });
  } catch (error) {
    if (mtsLogin === login) { mtsMessage(error.message, true); resetMtsAttempt(); }
  }
}
function resetMtsAttempt() {
  if (!mtsLogin) return;
  mtsLogin.busy = false;
  delete mtsLogin.ticket;
  delete mtsLogin.flowId;
  $("mtsSsoFind").disabled = !mtsExtensionReady;
  $("mtsSsoCancel").disabled = false;
  $("mtsSsoChoices").querySelectorAll("button").forEach(b => { b.disabled = false; });
}
window.addEventListener("message", async event => {
  if (event.source !== window || event.origin !== location.origin ||
      event.data?.channel !== mtsSsoChannel || event.data?.from !== "extension") return;
  const message = event.data;
  if (message.action === "ready") {
    clearTimeout(mtsExtensionCheckTimer);
    mtsExtensionReady = true;
    mtsNativeSSO = message.transport === "desktop";
    renderMtsExtension();
    if (mtsLogin && !mtsLogin.busy) {
      $("mtsSsoFind").disabled = false;
      mtsMessage(mtsNativeSSO ? tr("Введите рабочий email для входа в МТС Линк.") : tr("Расширение подключено. Введите рабочий email."));
    }
    return;
  }
  if (message.action === "unavailable") {
    mtsExtensionReady = false;
    renderMtsExtension();
    if (mtsLogin) mtsMessage(tr("Расширение недоступно. Нажмите его значок на этой странице или введите access token вручную."), true);
    return;
  }
  const login = mtsLogin;
  if (!login?.flowId || message.flowId !== login.flowId) return;
  if (message.action === "opened") return mtsMessage(tr("Выполните корпоративный вход в открывшемся окне."));
  if (message.action === "error" || message.action === "cancelled") {
    mtsMessage(message.message || tr("Окно входа закрыто. Можно попробовать снова."), true);
    resetMtsAttempt();
    return;
  }
  if (message.action !== "complete" || !login.ticket || login.completing) return;
  login.completing = true;
  $("mtsSsoCancel").disabled = true;
  mtsMessage(tr("Сохраняем подключение на сервере…"));
  try {
    await request(`/sources/${login.sourceId}/mts-link/finish`, {
      method: "POST", body: JSON.stringify({ ticket: login.ticket, auth_code: message.authCode })
    });
    mtsLogin = null;
    $("mtsSsoDialog").close();
    toast(tr("МТС Линк подключён. Обновление access token настроено."));
    document.dispatchEvent(new CustomEvent("secretary:source-saved", { detail: { sourceId: login.sourceId } }));
    await loadSources();
    loadStatus();
  } catch (error) {
    if (mtsLogin === login) {
      login.completing = false;
      mtsMessage(error.message, true);
      resetMtsAttempt();
    }
  }
});
$("mtsSsoForm").addEventListener("submit", findMtsOrganizations);
$("mtsSsoEmail").addEventListener("input", () => {
  if (mtsLogin) mtsLogin.emailEdited = true;
  rememberMtsEmail();
});
$("mtsSsoCancel").addEventListener("click", () => $("mtsSsoDialog").close());
$("mtsSsoDialog").addEventListener("cancel", event => {
  if (mtsLogin?.completing) event.preventDefault();
});
$("mtsSsoDialog").addEventListener("close", cancelMtsLogin);
$("sourceFields").addEventListener("click", event => {
  if (event.target.closest("#sourceMtsRecheck")) checkMtsExtension();
  if (event.target.closest("#sourceMtsSso")) void mtsSsoFromSourceForm();
});
$("mtsSsoManual").addEventListener("click", () => {
  const source = mtsLogin?.source;
  if (!source) return;
  $("mtsSsoDialog").close();
  cancelMtsLogin();
  openSourceDialog(source);
  $("sourceCredential").focus();
});
window.addEventListener("focus", () => {
  if (!mtsLogin?.busy && ($("sourceDialog").open || $("mtsSsoDialog").open)) checkMtsExtension();
});
window.addEventListener("pagehide", cancelMtsLogin);
checkMtsExtension();
