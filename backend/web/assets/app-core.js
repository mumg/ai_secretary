var tr = globalThis.SecretaryI18n?.t || ((s, ...a) => s.replace(/\{(\d+)\}/g, (m, i) => i < a.length ? String(a[i]) : m));
/* Pure formatting helpers shared by the live UI and its contract tests. */
(() => {
  "use strict";
  const escape = (value) =>
    String(value ?? "").replace(
      /[&<>"']/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          '"': "&quot;",
          "'": "&#39;",
        })[c],
    );
  function safeURL(value) {
    if (!value || /[\u0000-\u0020\u007f]/.test(value)) return "";
    try {
      const u = new URL(value);
      return ["https:", "http:", "mailto:"].includes(u.protocol) ? u.href : "";
    } catch {
      return "";
    }
  }
  const md = window.markdownit({ html: false, breaks: true, linkify: false });
  md.validateLink = (value) => !!safeURL(value);
  md.renderer.rules.image = (tokens, idx) =>
    escape(tokens[idx].content || tr("Изображение"));
  const linkOpen =
    md.renderer.rules.link_open ||
    ((tokens, idx, options, env, self) =>
      self.renderToken(tokens, idx, options));
  md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
    tokens[idx].attrSet("target", "_blank");
    tokens[idx].attrSet("rel", "noopener noreferrer");
    return linkOpen(tokens, idx, options, env, self);
  };
  function markdown(value) {
    return md.render(String(value || ""));
  }
  function plainText(value) {
    const node = document.createElement("div");
    node.innerHTML = markdown(String(value || "").slice(0, 2000));
    return node.textContent.replace(/\s+/g, " ").trim();
  }
  function healthClass(value, overall = false) {
    if (value === "ERROR") return "error";
    if (["DEGRADED", "STALE", "UNKNOWN"].includes(value)) return "warning";
    return value === "BUSY" && !overall ? "busy" : "ok";
  }
  function zonedISO(value, timeZone) {
    if (!value) return null;
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))
      throw new Error(tr("Укажите дату и время."));
    const [y, mo, d, h, mi] = value.split(/\D/).map(Number);
    const target = Date.UTC(y, mo - 1, d, h, mi);
    const parts = (date) =>
      Object.fromEntries(
        new Intl.DateTimeFormat("en-CA", {
          timeZone,
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hourCycle: "h23",
        })
          .formatToParts(date)
          .map((p) => [p.type, p.value]),
      );
    let guess = target;
    for (let i = 0; i < 4; i++) {
      const p = parts(new Date(guess));
      const delta =
        target - Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute);
      if (!delta) return new Date(guess).toISOString();
      guess += delta;
    }
    throw new Error(
      tr("Это время отсутствует в выбранном часовом поясе. Укажите другое время."),
    );
  }
  window.SecretaryCore = {
    escape,
    safeURL,
    markdown,
    plainText,
    healthClass,
    zonedISO,
  };
})();
