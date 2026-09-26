(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const tr = globalThis.SecretaryI18n?.t || ((s) => s);
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"})[char]);
  const status = {queued: tr("Ожидает отправки"), received: tr("Получено"), in_review: tr("На рассмотрении"), resolved: tr("Решено"), rejected: tr("Отклонено")};
  const query = new URLSearchParams(location.search);
  let pendingReportID = query.get("pending_report") || "";
  let selectedKind = query.has("draft") ? "draft" : query.has("report") ? "report" : query.get("new") === "support" ? "support" : "";
  let selectedID = query.get(selectedKind) || "";
  let selected = null;
  let selectedReport = null;
  let dirty = false;
  let busy = false;
  function actionStatus(message) { $("report-action-status").textContent = message; $("report-action-status").hidden = !message; }
  async function api(path, options = {}) {
    const response = await fetch(`/api/v1/diagnostic-reports${path}`, {credentials: "same-origin", cache: "no-store", ...options, headers: {Accept: "application/json", ...(options.body ? {"Content-Type": "application/json"} : {})}});
    if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.detail || `${response.status}`); }
    return response.status === 204 ? null : response.json();
  }
  function fieldLabel(field, issueType = "") {
    const parts = field.path.split("/");
    if (issueType === "technical_support" && field.path === "/issue/user_comment") return tr("Проблема");
    if (issueType === "technical_support" && field.path === "/issue/expected") return tr("Как должно было быть");
    if (field.path === "/issue/user_comment") return tr("Полученное некорректное поведение");
    if (field.path === "/issue/expected") return tr("Ожидаемое поведение");
    if (parts[1] === "context" && parts[2] === "events") {
      return {author: tr("Отправитель"), recipients: tr("Получатели"), subject: tr("Тема"), body: tr("Текст письма")}[parts[4]] || field.label;
    }
    if (parts[1] === "issue" && parts[2] === "observed") {
      const name = {title: tr("Название"), evidence: tr("Основание"), assignee_name: tr("Исполнитель"), assignee_email: tr("Email исполнителя")}[parts[4]] || field.label;
      return `${tr("Результат обработки")} ${Number(parts[3]) + 1}: ${name}`;
    }
    return field.label;
  }
  function editor(field) {
    return `<label>${escape(fieldLabel(field))}<textarea data-path="${escape(field.path)}" ${field.path.endsWith("/body") ? "data-body" : ""}>${escape(field.value)}</textarea></label>`;
  }
  function updateSendButton() {
    $("report-send").disabled = !selected || !["ready", "needs_review"].includes(selected.state) || busy || !$("report-consent").checked;
  }
  function paintDraft(draft) {
    selected = draft;
    selectedReport = null;
    $("support-form").hidden = true;
    $("report-detail-title").textContent = tr("Черновик обращения");
    $("report-sent-detail").hidden = true;
    const ready = draft.state === "ready" || draft.state === "needs_review";
    $("report-review").hidden = !ready;
    $("report-message").hidden = ready;
    $("report-message").textContent = draft.state === "sending" ? `${tr("Проверяем и отправляем обращение в фоне. Можно закрыть страницу.")} ${draft.report_id ? `${tr("ID обращения")}: ${draft.report_id}` : ""}` : draft.state === "processing" ? tr("Обезличивание выполняется в фоне. Можно вернуться позже.") : draft.state === "failed" ? tr("Не удалось подготовить обезличивание") : "";
    $("report-warning").hidden = !(draft.warnings || []).length && !draft.last_error;
    $("report-warning").textContent = (draft.warnings || []).join(" · ") || draft.last_error || "";
    if (!ready) return;
    const original = draft.original || {};
    $("report-original-title").textContent = /mail|email/i.test(String(original.kind || "")) ? tr("Исходное письмо") : tr("Исходный материал");
    const source = document.createElement("pre");
    source.className = "report-source";
    source.textContent = [original.author && `${tr("Отправитель")}: ${original.author}`, original.recipients && `${tr("Получатели")}: ${original.recipients}`, original.subject && `${tr("Тема")}: ${original.subject}`, original.body].filter(Boolean).join("\n\n");
    $("report-original").replaceChildren(source);
    if (original.truncated) {
      const note = document.createElement("p"); note.className = "muted"; note.textContent = tr("Длинное письмо сокращено для обращения."); $("report-original").append(note);
    }
    const processed = draft.processed || [];
    $("report-processed").innerHTML = processed.length
      ? processed.map((item) => `<article><strong>${escape(item.kind === "delegation" ? tr("Поручение") : tr("Задача"))}: ${escape(item.title)}</strong>${item.evidence ? `<p>${escape(item.evidence)}</p>` : ""}${item.assignee_name ? `<p>${tr("Исполнитель")}: ${escape(item.assignee_name)}</p>` : ""}</article>`).join("")
      : `<p>${tr("Задачи и поручения не созданы")}</p>`;
    const fields = draft.fields || [];
    const byPath = Object.fromEntries(fields.map((field) => [field.path, field]));
    $("report-anonymized").innerHTML = ["/context/events/0/author", "/context/events/0/recipients", "/context/events/0/subject", "/context/events/0/body"].map((path) => byPath[path]).filter(Boolean).map(editor).join("");
    $("report-behavior").innerHTML = ["/issue/user_comment", "/issue/expected"].map((path) => byPath[path]).filter(Boolean).map(editor).join("");
    const extra = fields.filter((field) => field.path.startsWith("/issue/observed/"));
    $("report-extra").hidden = extra.length === 0;
    $("report-extra-fields").innerHTML = extra.map(editor).join("");
    dirty = false;
    $("report-consent").checked = false;
    $("report-save").disabled = true;
    actionStatus("");
    updateSendButton();
  }
  async function loadDraft(id) {
    selectedKind = "draft";
    selectedID = id;
    history.replaceState(null, "", id ? `/app/reports?draft=${encodeURIComponent(id)}${pendingReportID ? `&pending_report=${encodeURIComponent(pendingReportID)}` : ""}` : "/app/reports");
    try { paintDraft(await api(`/drafts/${encodeURIComponent(id)}/review`)); }
    catch (err) { selected = null; $("report-review").hidden = true; $("report-sent-detail").hidden = true; $("report-message").hidden = false; $("report-message").textContent = err.message; }
  }
  function paintReport(report) {
    selected = null;
    selectedReport = report;
    $("report-detail-title").textContent = report.issue_type === "technical_support" ? tr("Техническая поддержка") : tr("Детали обращения");
    $("support-form").hidden = true;
    $("report-review").hidden = true;
    $("report-message").hidden = true;
    $("report-warning").hidden = true;
    $("report-sent-detail").hidden = false;
    $("report-sent-id").textContent = report.report_id;
    $("report-sent-state").textContent = status[report.state] || report.state;
    const formatDate = (value) => value ? new Date(value).toLocaleString(globalThis.SecretaryI18n?.locale || "ru-RU") : "—";
    $("report-sent-created").textContent = formatDate(report.created_at);
    $("report-sent-updated").textContent = formatDate(report.updated_at);
    $("report-sent-error").hidden = !report.last_error;
    $("report-sent-error").textContent = report.last_error || "";
    $("report-sent-retry").hidden = report.state !== "queued";
    const answered = report.state === "resolved" && !!report.response?.trim();
    $("report-answer").hidden = !answered;
    $("report-answer-text").textContent = answered ? report.response : "";
    $("report-submitted-fields").innerHTML = (report.fields || []).map((field) => `<article><h4>${escape(fieldLabel(field, report.issue_type))}</h4><div class="report-submitted-value">${escape(field.value)}</div></article>`).join("");
  }
  function showSupportForm() {
    selectedKind = "support";
    selectedID = "";
    selected = null;
    selectedReport = null;
    history.replaceState(null, "", "/app/reports?new=support");
    $("report-detail-title").textContent = tr("Техническая поддержка");
    $("report-review").hidden = true;
    $("report-sent-detail").hidden = true;
    $("report-message").hidden = true;
    $("report-warning").hidden = true;
    $("support-form").hidden = false;
    document.querySelectorAll(".report-card").forEach((item) => item.classList.remove("selected"));
  }
  async function loadReport(id) {
    selectedKind = "report";
    selectedID = id;
    history.replaceState(null, "", `/app/reports?report=${encodeURIComponent(id)}`);
    try { paintReport(await api(`/details/${encodeURIComponent(id)}`)); }
    catch (err) { selectedReport = null; $("report-sent-detail").hidden = true; $("report-review").hidden = true; $("report-message").hidden = false; $("report-message").textContent = err.message; }
  }
  async function load() {
    try {
      const page = await api("");
      $("reports-error").hidden = true;
      const drafts = page.drafts || [];
      const reports = page.items || [];
      if (selectedKind === "draft" && !drafts.some((item) => item.draft_id === selectedID)) {
        if (pendingReportID && reports.some((item) => item.report_id === pendingReportID)) {
          selectedKind = "report"; selectedID = pendingReportID; selected = null; pendingReportID = "";
        } else { selectedKind = ""; selectedID = ""; selected = null; pendingReportID = ""; }
      }
      if (selectedKind === "report" && !reports.some((item) => item.report_id === selectedID)) { selectedKind = ""; selectedID = ""; selectedReport = null; }
      if (!selectedKind && (drafts.length || reports.length)) {
        selectedKind = drafts.length ? "draft" : "report";
        selectedID = drafts.length ? drafts[0].draft_id : reports[0].report_id;
      }
      const draftCards = drafts.map((draft) => `<article class="report-card ${["ready", "needs_review"].includes(draft.state) ? "draft-ready" : ""} ${selectedKind === "draft" && selectedID === draft.draft_id ? "selected" : ""}" data-draft-id="${escape(draft.draft_id)}" tabindex="0" role="button" aria-label="${tr("Черновик обращения")}"><strong>${tr("Черновик обращения")}</strong><p>${escape(draft.state === "needs_review" ? tr("Требуется уточнение перед отправкой") : draft.state === "ready" ? tr("Обезличивание готово к проверке") : draft.state === "sending" ? tr("Проверяем и отправляем обращение в фоне") : draft.state === "processing" ? tr("Обезличивание выполняется в фоне") : tr("Не удалось подготовить обезличивание"))}</p>${draft.state === "sending" && draft.report_id ? `<p class="report-id">${tr("ID обращения")}: ${escape(draft.report_id)}</p>` : ""}${draft.last_error ? `<p class="inline-error">${escape(draft.last_error)}</p>` : ""}${draft.state === "sending" ? "" : `<div class="report-card-actions">${draft.state === "failed" ? `<button data-retry-draft="${escape(draft.draft_id)}">${tr("Повторить попытку")}</button>` : ""}<button data-cancel-draft="${escape(draft.draft_id)}">${tr("Отменить черновик")}</button></div>`}</article>`).join("");
      const reportCards = reports.map((report) => `<article class="report-card ${report.state === "resolved" && report.response?.trim() ? "answered" : ""} ${selectedKind === "report" && selectedID === report.report_id ? "selected" : ""}" data-report-id="${escape(report.report_id)}" tabindex="0" role="button" aria-label="${tr("Обращение")} ${escape(report.report_id)}"><strong>${tr("Обращение")}</strong><p class="report-id">${escape(report.report_id)}</p><p>${escape(status[report.state] || report.state)}</p>${report.last_error ? `<p class="inline-error">${escape(report.last_error)}</p>` : ""}</article>`).join("");
      $("reports-list").innerHTML = draftCards + reportCards || `<p>${tr("Обращений пока нет")}</p>`;
      if (selectedKind === "draft" && (!selected || ["processing", "sending", "failed"].includes(selected.state))) await loadDraft(selectedID);
      else if (selectedKind === "report") await loadReport(selectedID);
      else if (selectedKind === "support") showSupportForm();
      else if (!selectedKind) { $("report-review").hidden = true; $("report-sent-detail").hidden = true; $("report-message").hidden = false; $("report-message").textContent = tr("Обращений пока нет"); history.replaceState(null, "", "/app/reports"); }
    } catch (err) { $("reports-error").textContent = err.message; $("reports-error").hidden = false; }
  }
  $("reports-refresh").addEventListener("click", load);
  $("report-sent-delete").addEventListener("click", async () => {
    if (busy || !selectedReport || !confirm(tr("Удалить обращение из приложения и поддержки? Это действие нельзя отменить."))) return;
    busy = true;
    $("report-sent-delete").disabled = true;
    try {
      await api(`/${encodeURIComponent(selectedReport.report_id)}`, {method: "DELETE"});
      selectedKind = ""; selectedID = ""; selectedReport = null;
      await load();
    } catch (err) {
      $("reports-error").textContent = err.message;
      $("reports-error").hidden = false;
    } finally { busy = false; $("report-sent-delete").disabled = false; }
  });
  $("reports-new-support").addEventListener("click", () => {
    if (busy) return;
    $("support-form").reset();
    $("support-error").hidden = true;
    showSupportForm();
    $("support-problem").focus();
  });
  $("support-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || !$("support-form").reportValidity()) return;
    busy = true;
    $("support-send").disabled = true;
    $("support-error").hidden = true;
    try {
      const report = await api("/support", {method: "POST", body: JSON.stringify({problem: $("support-problem").value, expected: $("support-expected").value, consent_without_redaction: $("support-consent").checked})});
      await loadReport(report.report_id);
      await load();
    } catch (err) {
      $("support-error").textContent = err.message;
      $("support-error").hidden = false;
    } finally { busy = false; $("support-send").disabled = false; }
  });
  $("reports-list").addEventListener("click", async (event) => {
    const card = event.target.closest(".report-card");
    if (!card || busy) return;
    const button = event.target.closest("button");
    busy = true;
    try {
      if (button?.dataset.cancelDraft) {
        await api(`/drafts/${encodeURIComponent(button.dataset.cancelDraft)}`, {method: "DELETE"});
        if (selectedKind === "draft" && selectedID === button.dataset.cancelDraft) { selectedKind = ""; selectedID = ""; selected = null; }
        await load();
      } else if (button?.dataset.retryDraft) {
        await api(`/drafts/${encodeURIComponent(button.dataset.retryDraft)}/retry`, {method: "POST", body: "{}"});
        await loadDraft(button.dataset.retryDraft);
        await load();
      } else if (card.dataset.draftId) {
        if (selectedID !== card.dataset.draftId) pendingReportID = "";
        document.querySelectorAll(".report-card").forEach((item) => item.classList.toggle("selected", item === card));
        await loadDraft(card.dataset.draftId);
      } else if (card.dataset.reportId) {
        pendingReportID = "";
        document.querySelectorAll(".report-card").forEach((item) => item.classList.toggle("selected", item === card));
        await loadReport(card.dataset.reportId);
      }
    } catch (err) { $("reports-error").textContent = err.message; $("reports-error").hidden = false; }
    finally { busy = false; }
  });
  $("reports-list").addEventListener("keydown", (event) => {
    if (event.target.matches(".report-card") && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      event.target.click();
    }
  });
  $("report-review").addEventListener("input", (event) => { if (event.target.matches("textarea[data-path]")) { dirty = true; $("report-save").disabled = false; actionStatus(""); updateSendButton(); } });
  $("report-consent").addEventListener("change", updateSendButton);
  async function save(replaceAll = false) {
    if (!selected || busy) return;
    busy = true; $("report-save").disabled = true; updateSendButton();
    actionStatus(tr("Проверяем правки…"));
    try {
      const edits = [...document.querySelectorAll("#report-review textarea[data-path]")].map((field) => ({path: field.dataset.path, value: field.value}));
      const request = {edits};
      if (replaceAll) request.replace_all = {find: $("report-find").value, replacement: $("report-replacement").value};
      await api(`/drafts/${encodeURIComponent(selected.draft_id)}`, {method: "PATCH", body: JSON.stringify(request)});
      await loadDraft(selected.draft_id);
      $("report-find").value = ""; $("report-replacement").value = "";
      actionStatus(tr("Правки проверены. Проверьте обезличенную версию и снова подтвердите отправку."));
      return true;
    } catch (err) { $("report-warning").textContent = err.message; $("report-warning").hidden = false; $("report-save").disabled = false; return false; }
    finally { busy = false; updateSendButton(); }
  }
  $("report-save").addEventListener("click", () => save());
  $("report-replace-all").addEventListener("click", () => { if ($("report-find").value.length >= 2) save(true); });
  $("report-sent-retry").addEventListener("click", async () => {
    if (!selectedReport || busy) return;
    busy = true;
    try { await api(`/${encodeURIComponent(selectedReport.report_id)}/retry`, {method: "POST", body: "{}"}); await load(); }
    catch (err) { $("reports-error").textContent = err.message; $("reports-error").hidden = false; }
    finally { busy = false; }
  });
  $("report-send").addEventListener("click", async () => {
    if (!selected || !$("report-consent").checked || busy) return;
    const draftID = selected.draft_id;
    const edits = [...document.querySelectorAll("#report-review textarea[data-path]")].map((field) => ({path: field.dataset.path, value: field.value}));
    busy = true; updateSendButton(); actionStatus(tr("Ставим обращение в очередь…"));
    try {
      const queued = await api(`/drafts/${encodeURIComponent(draftID)}/send`, {method: "POST", body: JSON.stringify({payload_sha256: selected.payload_sha256, edits})});
      pendingReportID = queued.report_id;
      paintDraft({...selected, state: "sending", report_id: queued.report_id});
      selectedKind = "draft"; selectedID = draftID;
      history.replaceState(null, "", `/app/reports?draft=${encodeURIComponent(draftID)}&pending_report=${encodeURIComponent(queued.report_id)}`);
      void load();
    } catch (err) { $("report-warning").textContent = err.message; $("report-warning").hidden = false; actionStatus(""); }
    finally { busy = false; updateSendButton(); }
  });
  load();
  setInterval(() => { if (!document.hidden && !busy) load(); }, 10000);
})();
