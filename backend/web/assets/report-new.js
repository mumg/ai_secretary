(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const tr = globalThis.SecretaryI18n?.t || ((s) => s);
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[char]);
  const query = new URLSearchParams(location.search);
  const originKind = query.get("kind") || "";
  const originID = query.get("id") || "";
  const requestedReturn = query.get("return") || "/app/";
  const returnPath = requestedReturn.startsWith("/app/") && !requestedReturn.startsWith("//") && !requestedReturn.startsWith("/app/report/new") ? requestedReturn : "/app/";
  let draftID = query.get("draft") || "";
  let draft = null;
  let busy = false;
  let dirty = false;
  function actionStatus(message) { $("report-action-status").textContent = message; $("report-action-status").hidden = !message; }

  async function api(path, options = {}) {
    const response = await fetch(`/api/v1${path}`, {
      credentials: "same-origin", cache: "no-store", ...options,
      headers: {Accept: "application/json", ...(options.body ? {"Content-Type": "application/json"} : {})},
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || tr("Сервер вернул ошибку {0}", response.status));
    }
    return response.status === 204 ? null : response.json();
  }
  function error(message) { $("report-error").textContent = message; $("report-error").hidden = !message; }
  function setOriginal(source) {
    $("report-message-title").textContent = /mail|email/i.test(String(source?.kind || source?.event_type || "")) ? tr("Исходное письмо") : tr("Исходное сообщение");
    if (!source) { $("report-source-status").textContent = tr("Исходное письмо недоступно"); return; }
    const recipients = source.recipients || (source.participants || []).filter((person) => ["to", "cc", "bcc"].includes(String(person.role || "").toLowerCase())).map((person) => `${String(person.role).toUpperCase()}: ${person.name ? `${person.name} <${person.address}>` : person.address}`).join("; ");
    $("report-source").textContent = [source.author && `${tr("Отправитель")}: ${source.author}`, recipients && `${tr("Получатели")}: ${recipients}`, source.subject && `${tr("Тема")}: ${source.subject}`, source.body || tr("Текст письма отсутствует")].filter(Boolean).join("\n\n");
    $("report-source").hidden = false;
    $("report-source-status").hidden = true;
  }
  async function loadOrigin() {
    if (!originKind || !originID) { error(tr("Не выбран источник обращения")); return; }
    try {
      const path = {task: "tasks", delegation: "delegations", conversation_event: "events", meeting_result: "meeting-results"}[originKind];
      if (!path) throw new Error(tr("Неизвестный тип исходного элемента"));
      const detail = await api(`/${path}/${encodeURIComponent(originID)}`);
      $("report-origin-title").textContent = detail.task?.title || detail.title || detail.subject || "";
      const source = originKind === "task" ? detail.source : originKind === "conversation_event" ? detail
        : detail.source_event_id ? await api(`/events/${encodeURIComponent(detail.source_event_id)}`) : null;
      setOriginal(source);
      $("report-type").value = originKind === "delegation" ? "false_delegation" : originKind === "task" ? "false_task" : "missing_task";
    } catch (cause) {
      $("report-source-status").textContent = tr("Не удалось загрузить исходное письмо") + ": " + cause.message;
    }
  }
  function fieldEditor(field, label) {
    return `<label>${escape(label)}<textarea data-path="${escape(field.path)}" ${field.path.endsWith("/body") ? "data-body" : ""}>${escape(field.value)}</textarea></label>`;
  }
  function updateButtons() {
    const ready = draft?.state === "ready" || draft?.state === "needs_review";
    $("report-save").disabled = !ready || busy || !dirty;
    $("report-send").disabled = !ready || busy || !$("report-consent").checked;
  }
  function paintDraft(value) {
    draft = value;
    draftID = value.draft_id;
    $("report-create-button").hidden = true;
    $("report-type").disabled = true;
    const ready = value.state === "ready" || value.state === "needs_review";
    $("report-comment").readOnly = !ready;
    $("report-expected").readOnly = !ready;
    $("report-progress").hidden = ready;
    $("report-review").hidden = !ready;
    $("report-anonymized").hidden = !ready;
    if (!ready) {
      $("report-progress-text").textContent = value.state === "sending" ? tr("Проверяем и отправляем обращение в фоне. Можно закрыть страницу.") : value.state === "failed" ? value.last_error || tr("Не удалось подготовить обезличивание") : tr("Обезличивание выполняется в фоне. Можно вернуться позже.");
      $("report-retry").hidden = value.state !== "failed";
      return;
    }
    setOriginal(value.original);
    error(value.last_error || "");
    const fields = value.fields || [];
    const byPath = Object.fromEntries(fields.map((field) => [field.path, field]));
    $("report-comment").value = byPath["/issue/user_comment"]?.value || "";
    $("report-expected").value = byPath["/issue/expected"]?.value || "";
    $("report-message-fields").innerHTML = [
      ["/context/events/0/author", tr("Отправитель")],
      ["/context/events/0/recipients", tr("Получатели")],
      ["/context/events/0/subject", tr("Тема")],
      ["/context/events/0/body", tr("Текст письма")],
    ].filter(([path]) => byPath[path]).map(([path, label]) => fieldEditor(byPath[path], label)).join("");
    const extra = fields.filter((field) => field.path.startsWith("/issue/observed/"));
    $("report-extra").hidden = extra.length === 0;
    $("report-extra-fields").innerHTML = extra.map((field) => fieldEditor(field, field.label)).join("");
    $("report-processed").innerHTML = (value.processed || []).length
      ? value.processed.map((item) => `<article><strong>${escape(item.kind === "delegation" ? tr("Поручение") : tr("Задача"))}: ${escape(item.title)}</strong>${item.evidence ? `<p>${escape(item.evidence)}</p>` : ""}</article>`).join("")
      : `<p>${tr("Задачи и поручения не созданы")}</p>`;
    dirty = false;
    $("report-consent").checked = false;
    actionStatus("");
    updateButtons();
  }
  async function loadDraft() {
    if (!draftID || busy) return;
    try {
      const value = await api(`/diagnostic-reports/drafts/${encodeURIComponent(draftID)}/review`);
      if (value.state === "processing") { location.replace("/app/reports"); return; }
      paintDraft(value); if (value.state !== "needs_review") error("");
    }
    catch (cause) { error(cause.message); }
  }
  async function create() {
    if (busy || !originKind || !originID) return;
    busy = true; error(""); $("report-create-button").disabled = true;
    $("report-progress").hidden = false;
    $("report-progress-text").textContent = tr("Создаём черновик обращения…");
    try {
      const value = await api("/diagnostic-reports/drafts", {method: "POST", body: JSON.stringify({origin_kind: originKind, origin_id: originID, issue_type: $("report-type").value, user_comment: $("report-comment").value, expected: $("report-expected").value})});
      if (value.state === "processing") {
        location.replace(returnPath);
        return;
      }
      history.replaceState(null, "", `/app/report/new?draft=${encodeURIComponent(value.draft_id)}`);
      paintDraft(value);
      if (value.state === "ready") { busy = false; await loadDraft(); }
    } catch (cause) { error(cause.message); $("report-progress").hidden = true; }
    finally { busy = false; $("report-create-button").disabled = false; }
  }
  async function save(replaceAll = false) {
    if (busy || !["ready", "needs_review"].includes(draft?.state)) return;
    busy = true; updateButtons(); error(""); actionStatus(tr("Проверяем правки…"));
    try {
      const edits = [
        {path: "/issue/user_comment", value: $("report-comment").value},
        {path: "/issue/expected", value: $("report-expected").value},
        ...[...document.querySelectorAll("textarea[data-path]")].map((field) => ({path: field.dataset.path, value: field.value})),
      ];
      const body = {edits};
      if (replaceAll) body.replace_all = {find: $("report-find").value, replacement: $("report-replacement").value};
      await api(`/diagnostic-reports/drafts/${encodeURIComponent(draftID)}`, {method: "PATCH", body: JSON.stringify(body)});
      $("report-find").value = ""; $("report-replacement").value = "";
      busy = false;
      await loadDraft();
      actionStatus(tr("Правки проверены. Проверьте обезличенную версию и снова подтвердите отправку."));
      return true;
    } catch (cause) { error(cause.message); actionStatus(""); return false; }
    finally { busy = false; updateButtons(); }
  }
  async function send() {
    if (busy || $("report-send").disabled) return;
    const edits = [
      {path: "/issue/user_comment", value: $("report-comment").value},
      {path: "/issue/expected", value: $("report-expected").value},
      ...[...document.querySelectorAll("textarea[data-path]")].map((field) => ({path: field.dataset.path, value: field.value})),
    ];
    busy = true; updateButtons(); error(""); actionStatus(tr("Ставим обращение в очередь…"));
    try {
      const queued = await api(`/diagnostic-reports/drafts/${encodeURIComponent(draftID)}/send`, {method: "POST", body: JSON.stringify({payload_sha256: draft.payload_sha256, edits})});
      location.replace(`/app/reports?draft=${encodeURIComponent(draftID)}&pending_report=${encodeURIComponent(queued.report_id)}`);
    } catch (cause) { error(cause.message); actionStatus(""); }
    finally { busy = false; updateButtons(); }
  }
  $("report-create-button").addEventListener("click", create);
  $("report-retry").addEventListener("click", async () => {
    try {
      const value = await api(`/diagnostic-reports/drafts/${encodeURIComponent(draftID)}/retry`, {method: "POST", body: "{}"});
      if (value.state === "processing") { location.replace("/app/reports"); return; }
      paintDraft(value); error("");
    }
    catch (cause) { error(cause.message); }
  });
  $("report-save").addEventListener("click", () => save());
  $("report-replace-button").addEventListener("click", () => { if ($("report-find").value.length >= 2) save(true); });
  $("report-send").addEventListener("click", send);
  $("report-consent").addEventListener("change", updateButtons);
  for (const id of ["report-comment", "report-expected"]) $(id).addEventListener("input", () => { if (draft?.state === "ready") { dirty = true; actionStatus(""); updateButtons(); } });
  $("report-message-fields").addEventListener("input", () => { dirty = true; actionStatus(""); updateButtons(); });
  $("report-extra-fields").addEventListener("input", () => { dirty = true; actionStatus(""); updateButtons(); });
  if (draftID) { $("report-create-button").hidden = true; $("report-type").disabled = true; loadDraft(); }
  else loadOrigin();
  setInterval(() => { if (!document.hidden && draftID && draft?.state === "processing" && !busy) loadDraft(); }, 5000);
})();
