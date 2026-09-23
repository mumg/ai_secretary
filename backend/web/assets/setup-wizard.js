(() => {
  const get = id => document.getElementById(id);
  const wizard = get("setupWizard");
  let steps = [];
  let stepIndex = 0;
  let widgetIndex = 0;
  let active = null;
  let serial = 0;
  let busy = false;
  let ssoWidgetID = null;
  const verified = new Set();

  function widgets(step) {
    if (step.widgets?.length) return step.widgets.map(item => typeof item === "string" ? item : item.id);
    return [step.type === "llm" ? "llm" : `source:${step.source_id}`];
  }
  function currentID() { return widgets(steps[stepIndex])[widgetIndex]; }
  function setStatus(message) { get("setupWizardStatus").textContent = message; }

  function showInstructionsText(element, value) {
    const content = value || "";
    element.replaceChildren();
    const urls = /https?:\/\/[^\s<>"']+/gi;
    let end = 0;
    for (const match of content.matchAll(urls)) {
      const label = match[0].replace(/[.,!?;:)\]]+$/, "");
      let url;
      try { url = new URL(label); } catch { continue; }
      if (!["http:", "https:"].includes(url.protocol) || !url.hostname) continue;
      element.append(document.createTextNode(content.slice(end, match.index)));
      const link = document.createElement("a");
      link.href = url.href;
      link.textContent = label;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      element.append(link);
      end = match.index + label.length;
    }
    element.append(document.createTextNode(content.slice(end)));
  }

  function showInstructions() {
    const step = steps[stepIndex];
    get("sourceWizardProgress").textContent = get("setupWizardProgress").textContent;
    get("sourceWizardTitle").textContent = step.title || currentID();
    showInstructionsText(get("sourceWizardInstructions"), step.instructions);
    const help = get("sourceWizardHelp");
    help.hidden = !step.help_url;
    if (step.help_url) help.href = step.help_url;
    else help.removeAttribute("href");
    get("sourceWizardContext").hidden = false;
  }

  function render() {
    const step = steps[stepIndex];
    const id = currentID();
    const model = id === "llm";
    const identity = id === "identity";
    document.body.classList.toggle("setup-model", model);
    document.body.classList.toggle("setup-identity", identity);
    get("setupWizardModel").hidden = !model && !identity;
    get("identityNames").required = identity;
    get("setupWizardProgress").textContent = tr("Шаг {0} из {1}", stepIndex + 1, steps.length);
    get("setupWizardTitle").textContent = step.title || id;
    showInstructionsText(get("setupWizardInstructions"), step.instructions);
    const help = get("setupWizardHelp");
    help.hidden = !step.help_url;
    if (step.help_url) help.href = step.help_url;
    else help.removeAttribute("href");
    get("setupWizardSSO").hidden = id !== ssoWidgetID;
    setStatus(verified.has(id) ? tr("Проверка прошла успешно") : "");
  }

  async function openCurrent() {
    const token = ++serial;
    active?.dispose();
    const id = currentID();
    ssoWidgetID = null;
    active = window.ConfigurationWidgets?.get(id);
    if (!active) { setStatus(`Неизвестный виджет настройки: ${id}`); return; }
    render();
    if (id.startsWith("source:")) {
      try {
        const source = (await request("/sources")).find(item => item.id === id.slice(7));
        if (token === serial && source?.source_type === "mts_link") {
          ssoWidgetID = id;
          get("setupWizardSSO").hidden = false;
        }
      } catch (error) { if (token === serial) setStatus(error.message); }
    }
    if (token !== serial || wizard.hidden) return;
    const entry = steps[stepIndex].widgets?.[widgetIndex];
    const autoVerifyExisting = id !== "identity" && Boolean(entry?.configured && !entry?.verified);
    if (autoVerifyExisting) setStatus(tr("Проверяем подключение…"));
    let checkError = null;
    await active.configure({
      onSuccess: () => {
        if (token !== serial) return;
        checkError = null;
        verified.add(id);
        if (id.startsWith("source:") && get("sourceDialog").open) get("sourceDialog").close();
        render();
        setStatus(id === "llm" ? tr("Модель отвечает") : id === "identity" ? tr("Сведения сохранены") : tr("Подключение работает"));
        void advance();
      },
      onError: error => {
        if (token !== serial) return;
        checkError = error;
        verified.delete(id);
        render();
        setStatus(error.message);
        if (id.startsWith("source:") && get("sourceDialog").open) setSourceFormError(error.message);
      },
    }, { wizard: true, host: get("setupWizardModel"), onShow: showInstructions,
      autoVerifyExisting });
    if (checkError && token === serial && id.startsWith("source:") && get("sourceDialog").open)
      setSourceFormError(checkError.message);
  }

  async function advance() {
    if (busy || !verified.has(currentID())) return;
    const step = steps[stepIndex];
    if (widgetIndex + 1 < widgets(step).length) {
      widgetIndex++;
      await openCurrent();
      return;
    }
    if (stepIndex + 1 < steps.length) {
      stepIndex++;
      widgetIndex = 0;
      await openCurrent();
      return;
    }
    busy = true;
    render();
    let failure;
    try {
      await request("/setup-wizard/finish", { method: "POST" });
      active?.dispose();
      wizard.hidden = true;
      document.body.classList.remove("setup-mode", "setup-model", "setup-identity");
      get("identityNames").required = false;
      location.assign("/app/");
    } catch (error) { failure = error; }
    finally {
      busy = false;
      if (failure) {
        if (!active) await openCurrent();
        else render();
        setStatus(failure.message);
      }
    }
  }

  get("sourceDialog").addEventListener("close", () => { get("sourceWizardContext").hidden = true; });
  get("setupWizardSSO").addEventListener("click", () => {
    if (active?.beginSSO) void active.beginSSO(true).catch(error => setStatus(error.message));
  });

  request("/setup-wizard").then(state => {
    if (!state.required || !state.steps?.length) return;
    steps = state.steps;
    for (const step of steps) {
      for (const widget of step.widgets || []) {
        if (widget.configured && widget.verified) verified.add(widget.id);
      }
    }
    let found = false;
    for (let i = 0; i < steps.length && !found; i++) {
      const ids = widgets(steps[i]);
      for (let j = 0; j < ids.length; j++) {
        if (!verified.has(ids[j])) { stepIndex = i; widgetIndex = j; found = true; break; }
      }
    }
    if (!found) { stepIndex = steps.length - 1; widgetIndex = widgets(steps[stepIndex]).length - 1; }
    document.body.classList.add("setup-mode");
    wizard.hidden = false;
    render();
    wizard.scrollIntoView({ block: "start" });
    if (found) void openCurrent();
    else void advance();
  }).catch(error => toast(error.message, true));
})();
