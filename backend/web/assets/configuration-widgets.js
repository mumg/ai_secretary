// The admin page and the setup wizard use these same controls and save actions.
// A widget owns its status check, editor entry point and completion callbacks.
(() => {
  const registry = new Map();
  const get = id => document.getElementById(id);

  class ConfigurationWidget {
    constructor(id, { show, check, test, restore = () => {} }) {
      if (typeof show !== "function" || typeof check !== "function" || typeof test !== "function" ||
          typeof restore !== "function") throw Error("Invalid configuration widget");
      this.id = id;
      this.show = show;
      this.check = check;
      this.test = test;
      this.restore = restore;
      this.callbacks = null;
      this.serial = 0;
    }
    async isConfigured() {
      const state = await this.check();
      return Boolean(state?.configured && state?.verified);
    }
    async configure(callbacks = {}, context = {}) {
      this.callbacks = callbacks;
      const serial = ++this.serial;
      if (context.autoVerifyExisting && await this.verify()) return () => this.dispose();
      if (serial !== this.serial) return () => this.dispose();
      try {
        await this.show(context);
      } catch (error) {
        if (serial === this.serial) {
          if (callbacks.onError) callbacks.onError(error);
          else toast(error.message, true);
        }
      }
      return () => this.dispose();
    }
    async verify() {
      const serial = this.serial;
      try {
        const state = await this.check();
        if (!state?.configured && this.callbacks?.onSuccess) throw Error(this.id === "llm"
          ? tr("Сначала сохраните настройки модели и API-ключ.")
          : tr("Сначала сохраните и включите источник с нужными учётными данными."));
        await this.test();
        if (serial === this.serial) {
          if (this.callbacks?.onSuccess) this.callbacks.onSuccess();
          else if (this.id === "llm" || this.id.startsWith("source:"))
            toast(this.id === "llm" ? tr("Модель отвечает") : tr("Подключение работает"));
        }
        return true;
      } catch (error) {
        if (serial === this.serial) {
          if (this.callbacks?.onError) this.callbacks.onError(error);
          else toast(error.message, true);
        }
        return false;
      }
    }
    dispose() { ++this.serial; this.callbacks = null; this.restore(); }
  }

  async function status(id) {
    const result = await request("/configuration-widgets");
    return result.widgets?.[id] || { configured: false, verified: false };
  }

  function source(id) {
    const key = `source:${id}`;
    if (registry.has(key)) return registry.get(key);
    const widget = new ConfigurationWidget(key, {
      check: () => status(key),
      test: async () => {
        try { await request(`/sources/${encodeURIComponent(id)}/test`, { method: "POST" }); }
        finally { await loadSources(); }
      },
      show: async context => {
        const item = (await request("/sources")).find(candidate => candidate.id === id);
        if (!item) throw Error(tr("Источник больше не доступен"));
        openSourceDialog(item);
        if (context.wizard && !item.enabled) get("sourceEnabled").checked = true;
        context.onShow?.();
      },
    });
    widget.beginSSO = async enableSource => {
      const item = (await request("/sources")).find(candidate => candidate.id === id);
      if (!item) throw Error(tr("Источник больше не доступен"));
      openMtsSso(item, enableSource ?? item.enabled);
    };
    registry.set(key, widget);
    return widget;
  }

  const analysis = get("analysis");
  const analysisHome = document.createComment("analysis widget home");
  analysis.before(analysisHome);
  const analysisConfig = get("analysisConfig");
  const analysisConfigHome = document.createComment("analysis configuration widget home");
  analysisConfig.before(analysisConfigHome);
  registry.set("llm", new ConfigurationWidget("llm", {
    check: () => status("llm"),
    test: () => request("/setup-wizard/llm/test", { method: "POST" }),
    show: async context => {
      if (context.host) context.host.append(analysisConfig);
      else analysisConfigHome.after(analysisConfig);
      get("llmProvider").scrollIntoView({ block: "center" });
      get("llmProvider").focus();
    },
    restore: () => analysisConfigHome.after(analysisConfig),
  }));

  registry.set("identity", new ConfigurationWidget("identity", {
    check: () => status("identity"),
    test: () => request("/setup-wizard/identity/confirm", { method: "POST" }),
    show: async context => {
      if (context.host) context.host.append(analysis);
      else analysisHome.after(analysis);
      get("identityNames").scrollIntoView({ block: "center" });
      get("identityNames").focus();
    },
    restore: () => analysisHome.after(analysis),
  }));

  // Other admin settings are widgets too. They use the existing forms in place;
  // they can be referenced by a future setup step without copying their markup.
  for (const [id, panel] of [["sources", "sources"], ["tags", "tags"],
    ["analysis", "analysis"], ["analysisConfig", "analysisConfig"], ["notifications", "notifications"],
    ["connection", "mobile"]]) {
    const node = get(panel);
    const home = document.createComment(`${id} widget home`);
    node.before(home);
    registry.set(id, new ConfigurationWidget(id, {
      show: async context => {
        if (context.host) context.host.append(node);
        else { home.after(node); document.querySelector(`[data-panel="${panel}"]`).click(); }
      },
      check: async () => {
        if (id === "sources") {
          const result = await request("/configuration-widgets");
          const entries = Object.entries(result.widgets || {}).filter(([key]) => key.startsWith("source:"));
          return { configured: entries.length > 0 && entries.every(([, value]) => value.configured),
            verified: entries.length > 0 && entries.every(([, value]) => value.verified) };
        }
        const configured = id === "tags" || Boolean(settingsState);
        return { configured, verified: configured };
      },
      test: async () => {},
      restore: () => home.after(node),
    }));
  }

  document.addEventListener("secretary:source-saved", event => {
    if (event.detail?.sourceId) void source(event.detail.sourceId).verify();
  });
  document.addEventListener("secretary:settings-saved", event => {
    if (event.detail?.llm) void registry.get("llm").verify();
    if (event.detail?.panel) void registry.get(event.detail.panel)?.verify();
  });
  document.addEventListener("secretary:widget-saved", event => {
    if (event.detail?.id) void registry.get(event.detail.id)?.verify();
  });
  window.ConfigurationWidgets = {
    ConfigurationWidget,
    register(id, options) {
      if (!id || registry.has(id)) throw Error("Duplicate configuration widget");
      const widget = new ConfigurationWidget(id, options);
      registry.set(id, widget);
      return widget;
    },
    get(id) { return id.startsWith("source:") ? source(id.slice(7)) : registry.get(id); },
    all: () => [...registry.values()],
  };
})();
