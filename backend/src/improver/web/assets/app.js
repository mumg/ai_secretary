(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const C = window.SecretaryCore,
    e = C.escape;
  const tabs = ["tasks", "meetings", "results", "threads", "status"];
  const names = {
    tasks: "План на сегодня",
    meetings: "Предстоящие встречи",
    results: "Результаты встреч",
    threads: "Резюме переписок",
    status: "Состояние компонентов",
  };
  const searches = {
    tasks: "задачам",
    meetings: "встречам",
    results: "итогам",
    threads: "перепискам",
  };
  const priorities = {
    LOW: "Низкий",
    NORMAL: "Обычный",
    HIGH: "Высокий",
    CRITICAL: "Критический",
  };
  const statuses = {
    NEEDS_CONFIRMATION: "Нужно подтвердить",
    NEW: "Новая",
    IN_PROGRESS: "В работе",
    POSSIBLY_COMPLETED: "Возможно выполнена",
    COMPLETED: "Завершена",
    CANCELLED: "Отменена",
    PENDING: "В очереди",
    PROCESSING: "Обработка",
    READY: "Контекст готов",
    EMPTY: "Материалы не найдены",
    NOT_REQUESTED: "Контекст не запрошен",
    FAILED: "Ошибка",
    ENDED: "Встреча завершена",
    OK: "Норма",
    BUSY: "Занят",
    DEGRADED: "Есть проблемы",
    ERROR: "Ошибка",
    STALE: "Данные устарели",
    UNKNOWN: "Нет данных",
    DISABLED: "Отключён",
  };
  const state = {
    tab: "tasks",
    q: "",
    order: "rank",
    selected: null,
    chat: false,
    zone: "UTC",
    configured: false,
    views: {},
    detail: null,
    detailKey: "",
    chatRows: [],
    system: null,
  };
  let listController,
    detailController,
    listSerial = 0,
    detailSerial = 0,
    searchTimer,
    toastTimer,
    pollBusy = false,
    chatBusy = false,
    systemBusy = false,
    listLoading = false,
    actionBusy = false,
    chatRevision = 0;
  const view = () =>
    (state.views[state.tab] ||= {
      rows: [],
      hasMore: false,
      loaded: false,
      signature: "",
      scroll: 0,
    });
  const selectionKey = (s) => (s ? `${s.kind}:${s.id}` : "");
  const badge = (text, cls = "") =>
    `<span class="tag ${e(cls)}">${e(text)}</span>`;
  const label = (s) => statuses[s] || s || "Не указано";
  const closed = (t) => ["COMPLETED", "CANCELLED"].includes(t.status);
  const date = (value, time = true) =>
    value && Number.isFinite(Date.parse(value))
      ? new Intl.DateTimeFormat("ru-RU", {
          timeZone: state.zone,
          day: "numeric",
          month: "short",
          year: "numeric",
          ...(time ? { hour: "2-digit", minute: "2-digit" } : {}),
        }).format(new Date(value))
      : "Не указано";
  const clock = (value) =>
    value
      ? new Intl.DateTimeFormat("ru-RU", {
          timeZone: state.zone,
          hour: "2-digit",
          minute: "2-digit",
        }).format(new Date(value))
      : "";
  const interval = (x) =>
    x.all_day
      ? `${date(x.starts_at, false)} · весь день`
      : `${date(x.starts_at)} – ${date(x.starts_at, false) === date(x.ends_at, false) ? clock(x.ends_at) : date(x.ends_at)}`;
  const resultTime = (x) =>
    x.time_basis === "transcript"
      ? `По временным отметкам расшифровки: ${interval(x)}`
      : x.time_known === false
      ? `Время встречи неизвестно · письмо получено ${date(x.received_at)}`
      : interval(x);
  const link = (url, title) =>
    C.safeURL(url)
      ? `<a href="${e(C.safeURL(url))}" target="_blank" rel="noopener noreferrer">${e(title)}</a>`
      : "";
  const openButton = (kind, id, title, cls = "link-button") =>
    id
      ? `<button class="${cls}" data-open-kind="${e(kind)}" data-open-id="${e(id)}">${e(title)}</button>`
      : "";
  const markdown = (text) => `<div class="markdown">${C.markdown(text)}</div>`;
  const fact = (title, html) =>
    `<div class="fact"><span>${e(title)}</span>${html || "Не указано"}</div>`;
  const head = (text) =>
    `<div class="detail-head"><span class="eyebrow">${e(text)}</span></div>`;
  function people(values) {
    return `<div class="participants">${(values || []).map((p) => (typeof p === "string" ? `<span class="person">${e(p)}</span>` : `<span class="person">${e(p.name || p.display_name || p.address || p.email || "Участник")}${p.address || p.email ? `<small>${e(p.address || p.email)}</small>` : ""}</span>`)).join("")}</div>`;
  }
  function toast(message, error = false) {
    $("toast").textContent = message;
    $("toast").classList.toggle("error", error);
    $("toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => ($("toast").hidden = true), 5500);
  }
  function errorBox(id, message, retry = "") {
    $(id).hidden = !message;
    $(id).innerHTML = message
      ? `${e(message)}${retry ? ` <button data-retry="${retry}">Повторить</button>` : ""}`
      : "";
  }
  async function api(path, options = {}) {
    const response = await fetch(`/api/v1${path}`, {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const detail = Array.isArray(body.detail)
        ? body.detail.map((x) => x.msg).join("; ")
        : body.detail;
      throw new Error(
        response.status === 404
          ? "Запись не найдена. Возможно, она была удалена."
          : response.status === 401 || response.status === 403
            ? "Нет доступа. Проверьте клиентский сертификат."
            : detail || `Сервер вернул ошибку ${response.status}`,
      );
    }
    return response.status === 204 ? null : response.json();
  }
  function failText(err) {
    return err instanceof TypeError
      ? "Сервер недоступен. Проверьте соединение."
      : err.message;
  }
  function saveLocation(replace = false) {
    const params = new URLSearchParams({ tab: state.tab });
    if (state.q) params.set("q", state.q);
    if (state.order !== "rank") params.set("order", state.order);
    if (state.selected) {
      params.set("kind", state.selected.kind);
      params.set("id", state.selected.id);
    }
    if (state.chat) params.set("chat", "1");
    const next = `#${params}`;
    if (location.hash !== next)
      history[replace ? "replaceState" : "pushState"](
        {
          listScroll: $("list-scroll").scrollTop,
          detailScroll: $("detail").scrollTop,
        },
        "",
        next,
      );
  }
  function storeScroll() {
    view().scroll = $("list-scroll").scrollTop;
    history.replaceState(
      {
        ...history.state,
        listScroll: $("list-scroll").scrollTop,
        detailScroll: $("detail").scrollTop,
      },
      "",
      location.href,
    );
  }
  function fromLocation() {
    const p = new URLSearchParams(location.hash.slice(1));
    state.tab = tabs.includes(p.get("tab")) ? p.get("tab") : "tasks";
    state.q = (p.get("q") || "").slice(0, 200);
    state.order = p.get("order") === "due" ? "due" : "rank";
    const kind = p.get("kind"),
      id = p.get("id");
    state.selected =
      ["task", "meeting", "result", "thread", "event", "component"].includes(
        kind,
      ) && id
        ? { kind, id }
        : null;
    state.chat = p.get("chat") === "1";
  }
  function chrome() {
    $("page-title").textContent = names[state.tab];
    $("page-sub").textContent =
      state.tab === "tasks"
        ? "Встречи сегодня и активные задачи"
        : state.tab === "status"
          ? "Загрузчики, обработка и сервисы"
          : "";
    $("search").value = state.q;
    $("search").placeholder = `Поиск по ${searches[state.tab] || "записям"}`;
    $("search").setAttribute("aria-label", $("search").placeholder);
    $("search-wrap").hidden = state.tab === "status";
    $("clear-search").hidden = !state.q;
    $("sort").hidden = state.tab !== "tasks";
    $("sort").value = state.order;
    $("create-task").hidden = state.tab !== "tasks";
    document
      .querySelectorAll("[data-tab]")
      .forEach((b) =>
        b.setAttribute(
          "aria-current",
          b.dataset.tab === state.tab ? "page" : "false",
        ),
      );
    $("chat-toggle").setAttribute("aria-pressed", String(state.chat));
    $("chat-pane").hidden = !state.chat;
    $("detail").hidden = state.chat;
    $("detail-error").hidden = state.chat || !$("detail-error").textContent;
  }
  function taskCard(x) {
    const overdue = x.due_at && Date.parse(x.due_at) < Date.now() && !closed(x);
    return {
      title: x.title,
      sub: x.due_at ? `Срок: ${date(x.due_at)}` : "Без срока",
      preview: x.description || x.evidence,
      tags:
        badge(priorities[x.priority], x.priority.toLowerCase()) +
        badge(
          label(x.status),
          x.status === "NEEDS_CONFIRMATION" ? "warning" : "",
        ) +
        (overdue ? badge("Просрочена", "error") : ""),
    };
  }
  function card(row) {
    const x = row.value;
    let c;
    if (row.kind === "task") c = taskCard(x);
    else if (row.kind === "meeting")
      c = {
        title: x.title,
        sub: interval(x),
        preview: x.location || x.source_label,
        tags: badge(x.all_day ? "Весь день" : "Встреча", "blue"),
      };
    else if (row.kind === "result")
      c = {
        title: x.title,
        sub: resultTime(x),
        preview: x.brief_summary || x.summary,
        tags:
          badge(
            label(x.analysis_state),
            x.analysis_state === "FAILED" ? "error" : "green",
          ) +
          (x.supplement_count
            ? badge(`Дополнений: ${x.supplement_count}`)
            : ""),
      };
    else if (row.kind === "thread")
      c = {
        title: x.title || "Без темы",
        sub: `${x.source_label} · ${x.event_count} сообщений · ${date(x.last_event_at)}`,
        preview: x.summary,
        tags: "",
      };
    else
      c = {
        title: x.label,
        sub: date(x.observed_at),
        preview: x.message,
        tags: badge(label(x.status), C.healthClass(x.status)),
      };
    return `<button class="item${selectionKey(state.selected) === selectionKey({ kind: row.kind, id: x.id }) ? " selected" : ""}" data-open-kind="${row.kind}" data-open-id="${e(x.id)}" aria-pressed="${selectionKey(state.selected) === `${row.kind}:${x.id}`}"><span class="item-title">${e(c.title)}</span><span class="item-sub">${e(c.sub)}</span>${c.tags ? `<span class="tags">${c.tags}</span>` : ""}${c.preview ? `<span class="item-preview">${e(C.plainText(c.preview))}</span>` : ""}</button>`;
  }
  let renderedList = "";
  function renderList() {
    const v = view(),
      scroll = $("list-scroll").scrollTop;
    let group = "";
    const html =
      v.rows
        .map((row) => {
          const next = row.group || "";
          const h = next !== group ? `<div class="group">${e(next)}</div>` : "";
          group = next;
          return h + card(row);
        })
        .join("") ||
      `<div class="empty">${!v.loaded ? "Загрузка…" : state.q ? "По запросу ничего не найдено." : "Здесь пока нет записей."}</div>`;
    if (html !== renderedList) {
      const focused = document.activeElement?.closest("#list [data-open-id]")
        ?.dataset.openId;
      $("list").innerHTML = html;
      renderedList = html;
      if (focused) {
        [...$("list").querySelectorAll("[data-open-id]")]
          .find((node) => node.dataset.openId === focused)
          ?.focus({ preventScroll: true });
      }
    }
    $("load-more").hidden = !v.hasMore;
    $("count").textContent = v.loaded
      ? `${v.rows.length} записей${v.hasMore ? " · есть ещё" : ""}`
      : "Загрузка…";
    $("list-scroll").scrollTop = scroll;
  }
  async function loadList({ more = false, quiet = false } = {}) {
    if (!state.configured) return;
    const tab = state.tab,
      v = view(),
      signature = `${state.q}|${state.order}`,
      serial = ++listSerial;
    listController?.abort();
    listController = new AbortController();
    const signal = listController.signal;
    listLoading = true;
    $("load-more").disabled = true;
    if (!quiet) {
      $("count").textContent = "Загрузка…";
      errorBox("list-error", "");
    }
    try {
      let rows = [],
        hasMore = false;
      if (tab === "status") {
        const system = await api("/system/status", { signal });
        state.system = system;
        paintHealth();
        rows = system.components.map((value) => ({
          kind: "component",
          value,
          group: "Компоненты",
        }));
      } else if (tab === "tasks") {
        const [tasks, plan] = await Promise.all([
          api(
            `/tasks?${new URLSearchParams({ q: state.q, order: state.order })}`,
            { signal },
          ),
          state.q
            ? Promise.resolve({ meetings: [] })
            : api("/plans/today", { signal }),
        ]);
        rows = [
          ...plan.meetings.map((value) => ({
            kind: "meeting",
            value,
            group: "Встречи сегодня",
          })),
          ...tasks.map((value) => ({ kind: "task", value, group: "Задачи" })),
        ];
      } else {
        const path = {
          meetings: "meetings",
          results: "meeting-results",
          threads: "threads",
        }[tab];
        let offset = more ? v.nextOffset || v.rows.length : 0;
        const target = more ? 30 : Math.max(30, v.rows.length);
        const items = [];
        let page;
        do {
          const limit = Math.min(100, target - items.length);
          page = await api(
            `/${path}?${new URLSearchParams({ q: state.q, offset, limit })}`,
            { signal },
          );
          items.push(...page.items);
          offset += page.items.length;
        } while (page.has_more && items.length < target && page.items.length);
        if (serial === listSerial) v.nextOffset = offset;
        rows = items.map((value) => ({
          kind: { meetings: "meeting", results: "result", threads: "thread" }[
            tab
          ],
          value,
          group:
            tab === "meetings"
              ? date(value.starts_at, false)
              : tab === "results"
                ? date(value.starts_at, false)
                : date(value.last_event_at, false),
        }));
        hasMore = page.has_more;
      }
      if (serial !== listSerial) return;
      v.rows = more
        ? [
            ...v.rows,
            ...rows.filter(
              (r) => !v.rows.some((old) => old.value.id === r.value.id),
            ),
          ]
        : rows;
      v.hasMore = hasMore;
      v.loaded = true;
      v.signature = signature;
      renderList();
      errorBox("list-error", "");
      $("updated").textContent = `Обновлено ${clock(new Date().toISOString())}`;
      if (!state.selected && v.rows.length && tab !== "status") {
        state.selected = { kind: v.rows[0].kind, id: v.rows[0].value.id };
        saveLocation(true);
        renderList();
      }
      if (!state.chat) {
        if (tab === "status" && !state.selected) renderSystem();
        else if (state.selected)
          await loadDetail({
            quiet: quiet && selectionKey(state.selected) === state.detailKey,
          });
      }
    } catch (err) {
      if (err.name !== "AbortError" && serial === listSerial) {
        errorBox(
          "list-error",
          failText(err) + " Ранее загруженные данные сохранены.",
          "list",
        );
        $("count").textContent = "Не удалось обновить";
      }
    } finally {
      if (serial === listSerial) {
        listLoading = false;
        $("load-more").disabled = false;
      }
    }
  }
  function references(refs) {
    return refs?.length
      ? `<h3>Источники</h3><div class="references">${refs.map((r) => `<button class="source" data-open-kind="${r.meeting_result_id ? "result" : e(r.kind)}" data-open-id="${e(r.meeting_result_id || r.id)}"><strong>[${e(r.key)}] ${e(r.title)}</strong><p class="muted">${e([r.source_label, r.occurred_at ? date(r.occurred_at) : null].filter(Boolean).join(" · "))}</p><div class="item-preview">${e(r.snippet)}</div></button>`).join("")}</div>`
      : "";
  }
  function enhance(container, refs = []) {
    container.querySelectorAll(".markdown pre").forEach((pre) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "copy-code";
      b.dataset.copy = "code";
      b.textContent = "Копировать";
      pre.prepend(b);
    });
    if (!refs.length) return;
    const byKey = new Map(refs.map((r) => [r.key, r]));
    const walk = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walk.nextNode()) {
      const n = walk.currentNode;
      if (
        n.parentElement?.closest(".markdown") &&
        !n.parentElement.closest("pre,code,a,button") &&
        /\[[A-Z]\d+\]/.test(n.textContent)
      )
        nodes.push(n);
    }
    for (const node of nodes) {
      const fragment = document.createDocumentFragment();
      let at = 0;
      for (const match of node.textContent.matchAll(/\[([A-Z]\d+)\]/g)) {
        fragment.append(node.textContent.slice(at, match.index));
        const ref = byKey.get(match[1]);
        if (ref) {
          const b = document.createElement("button");
          b.className = "reference-link";
          b.dataset.openKind = ref.meeting_result_id ? "result" : ref.kind;
          b.dataset.openId = ref.meeting_result_id || ref.id;
          b.textContent = match[0];
          b.title = ref.title;
          fragment.append(b);
        } else fragment.append(match[0]);
        at = match.index + match[0].length;
      }
      fragment.append(node.textContent.slice(at));
      node.replaceWith(fragment);
    }
  }
  function sourceHTML(s) {
    if (!s)
      return '<p class="muted">Создана вручную. Исходное сообщение отсутствует.</p>';
    return `<article class="source"><div class="eyebrow">${e(s.source_label || s.source_id || "Первоисточник")}</div><h3>${e(s.subject || "Без темы")}</h3><p class="muted">${e(s.author || "Автор не указан")} · ${e(date(s.occurred_at))} · ${e({ INCOMING: "Входящее", OUTGOING: "Исходящее", INTERNAL: "Внутреннее" }[s.direction] || s.direction || "")}</p>${people(s.participants)}<div class="text-body">${e(s.body || "Текст отсутствует")}</div>${link(s.source_url, "Открыть в источнике ↗")}</article>`;
  }
  function taskHTML(data) {
    const t = data.task;
    return (
      head("Задача") +
      `<h2>${e(t.title)}</h2><div class="tags">${badge(priorities[t.priority], t.priority.toLowerCase())}${badge(label(t.status))}</div><div class="actions">${!closed(t) ? `${t.status === "NEEDS_CONFIRMATION" ? '<button class="primary" data-action="confirm">Подтвердить</button><button data-action="reject">Отклонить</button>' : '<button class="primary" data-action="complete">✓ Завершить</button>'}<button data-action="remind">◷ Напомнить</button>` : ""}</div><div class="facts">${fact("Срок", e(t.due_at ? date(t.due_at) : "Без срока"))}${fact("Статус", e(label(t.status)))}${fact("Приоритет", e(priorities[t.priority]))}${fact("Рейтинг", e(t.ranking_score.toFixed(2)))}</div><h3>Что нужно сделать</h3>${t.description ? markdown(t.description) : '<p class="muted">Описание не указано.</p>'}${t.ranking_reasons.length ? `<h3>Почему задача в плане</h3><ul>${t.ranking_reasons.map((r) => `<li>${e(r)}</li>`).join("")}</ul>` : ""}${t.evidence ? `<h3>Основание назначения</h3><div class="callout text-body">${e(t.evidence)}</div>` : ""}<h3>Напоминания</h3>${t.reminders.length ? `<ul>${t.reminders.map((r) => `<li>${e(date(r.remind_at))} · ${r.sent_at ? "Отправлено" : r.enabled ? "Запланировано" : "Отключено"} ${!r.sent_at ? `<button class="link-button" data-remove-reminder="${e(r.id)}">Удалить</button>` : ""}</li>`).join("")}</ul>` : '<p class="muted">Напоминания не установлены.</p>'}<h3>Первоисточник</h3>${sourceHTML(data.source)}`
    );
  }
  function meetingHTML(data) {
    const m = data.meeting,
      pending = ["PENDING", "PROCESSING"].includes(data.status),
      ended =
        ["CANCELLED", "ENDED"].includes(data.status) ||
        Date.parse(m.ends_at) <= Date.now();
    return (
      head("Подготовка к встрече") +
      `<h2>${e(m.title)}</h2><div class="tags">${badge(label(data.status), pending ? "blue" : data.status === "FAILED" ? "error" : "green")}</div><div class="facts">${fact(`Время · ${state.zone}`, e(interval(m)))}${fact("Организатор", people(m.organizer ? [m.organizer] : []))}${fact("Место", e(m.location))}${fact("Подключение", link(m.mts_link_url, "Открыть встречу ↗"))}</div><h3>Участники</h3>${m.attendees.length ? people(m.attendees) : '<p class="muted">Участники не указаны.</p>'}<div class="actions">${!ended ? `<button class="primary" data-action="context" ${pending ? "disabled" : ""}>${pending ? "Контекст готовится…" : data.summary ? "Обновить контекст" : "Создать контекст"}</button>` : ""}${openButton("event", m.source_event_id, "Открыть приглашение")}</div>${data.generated_at ? `<p class="muted">Подготовлено ${e(date(data.generated_at))}${pending ? " · показана предыдущая версия" : ""}${data.stale ? " · встреча изменилась, контекст устарел" : ""}</p>` : ""}${data.error ? `<p class="inline-error">${e(data.error)}</p>` : ""}${data.summary ? markdown(data.summary) : `<div class="callout">${ended ? "Сохранённого контекста нет." : pending ? "Подготовка продолжается на сервере. Можно выбрать другую запись." : "Контекст пока не запрашивался. Нажмите «Создать контекст», чтобы подготовить материалы."}</div>`}${references(data.references)}`
    );
  }
  function resultHTML(x) {
    return (
      head("Итоги встречи") +
      `<h2>${e(x.title)}</h2><p class="muted">${e(resultTime(x))} · ${e(x.source_label)}</p><div class="tags">${badge(label(x.analysis_state), x.analysis_state === "FAILED" ? "error" : "green")}</div><h3>Участники</h3>${x.participants.length ? people(x.participants) : '<p class="muted">Участники не указаны.</p>'}<h3>Резюме</h3>${x.summary ? markdown(x.summary) : `<p class="muted">${x.analysis_state === "COMPLETED" ? "Резюме отсутствует." : x.analysis_state === "FAILED" ? "Анализ завершился ошибкой." : "Анализ ещё не завершён."}</p>`}<h3>Решения</h3>${x.decisions.length ? markdown(x.decisions.map((v) => `- ${v}`).join("\n")) : '<p class="muted">Решения не выделены.</p>'}<h3>Договорённости</h3>${x.agreements.length ? markdown(x.agreements.map((v) => `- ${v}`).join("\n")) : '<p class="muted">Договорённости не выделены.</p>'}<div class="actions">${openButton("event", x.source_event_id, "Открыть полный оригинал")}${openButton("meeting", x.calendar_meeting_id, "Календарная встреча")}</div>${x.participant_summaries.length ? "<h3>Дополнения участников</h3>" : ""}${x.participant_summaries.map((p) => `<article class="source"><strong>${e(p.author || "Автор не указан")}</strong><p class="muted">${e(p.source_label)} · ${e(date(p.occurred_at))}</p>${markdown(p.summary)}${p.decisions.length ? `<h4>Решения</h4>${markdown(p.decisions.map((v) => `- ${v}`).join("\n"))}` : ""}${p.agreements.length ? `<h4>Договорённости</h4>${markdown(p.agreements.map((v) => `- ${v}`).join("\n"))}` : ""}${openButton("event", p.source_event_id, "Открыть оригинал дополнения")}</article>`).join("")}`
    );
  }
  function threadHTML(x) {
    return (
      head("Переписка") +
      `<h2>${e(x.title || "Без темы")}</h2><p class="muted">${e(x.source_label)} · ${x.event_count} сообщений · ${e(date(x.first_event_at))} — ${e(date(x.last_event_at))}</p>${people(x.participants)}<h3>Резюме Qwen</h3>${x.summary ? markdown(x.summary) : '<p class="muted">Резюме ещё не сформировано.</p>'}<h3>Сообщения · сначала новые</h3>${x.events.map((ev) => `<article class="source"><strong>${e(ev.subject || "Без темы")}</strong><p class="muted">${e(ev.author || "Автор не указан")} · ${e(date(ev.occurred_at))}</p><div class="text-body">${e(ev.preview)}</div>${openButton("event", ev.id, "Читать полное сообщение")}</article>`).join("")}<p class="muted">Показано ${x.events.length} из ${x.event_count}</p>${x.has_more_events ? '<button data-action="more-events">Загрузить следующие сообщения</button>' : ""}`
    );
  }
  function metricsHTML(metrics) {
    return `<div class="metrics">${Object.entries(metrics || {})
      .map(
        ([key, value]) =>
          `<div class="metric"><strong class="numbers">${e(value)}</strong><span>${e(key)}</span></div>`,
      )
      .join("")}</div>`;
  }
  function systemHTML(selected) {
    const system = state.system;
    if (!system) return '<p class="empty">Состояние загружается…</p>';
    const comp = selected
      ? system.components.find((x) => x.id === selected)
      : null;
    if (selected && !comp) return '<p class="empty">Компонент не найден.</p>';
    if (comp)
      return (
        head("Состояние компонента") +
        `<h2>${e(comp.label)}</h2><p class="${C.healthClass(comp.status)}"><span class="dot"></span>${e(label(comp.status))}</p><p>${e(comp.message || "Сообщений нет.")}</p><div class="facts">${fact("Последнее наблюдение", e(date(comp.observed_at)))}${fact("Актуально до", e(date(comp.expires_at)))}${fact("Тип", e(comp.component_type))}${fact("Идентификатор", e(comp.id))}</div><h3>Показатели</h3>${metricsHTML(comp.metrics)}`
      );
    return (
      head("Мониторинг") +
      `<h2>${["OK", "BUSY"].includes(system.overall_status) ? "Система работает штатно" : "Есть компоненты, требующие внимания"}</h2><p class="muted">Обновлено ${e(date(system.generated_at))}. Занятость Qwen не означает сбой.</p><div class="table-wrap"><table><thead><tr><th>Компонент</th><th>Состояние</th><th>Обновлено</th><th>Показатели</th></tr></thead><tbody>${system.components
        .map(
          (c) =>
            `<tr><td>${openButton("component", c.id, c.label)}</td><td class="${C.healthClass(c.status)}"><span class="dot"></span>${e(label(c.status))}<p class="muted">${e(c.message || "")}</p></td><td>${e(date(c.observed_at))}</td><td class="numbers">${Object.entries(
              c.metrics,
            )
              .map(([k, v]) => `${e(k)}: ${e(v)}`)
              .join("<br>")}</td></tr>`,
        )
        .join("")}</tbody></table></div>`
    );
  }
  function setDetail(html, refs = [], preserve = false) {
    const scroll = $("detail").scrollTop;
    $("detail").innerHTML = html;
    enhance($("detail"), refs);
    $("detail").scrollTop = preserve ? scroll : 0;
  }
  function renderSystem() {
    state.detailKey = "system";
    setDetail(systemHTML(null), [], true);
  }
  async function loadDetail({ quiet = false } = {}) {
    const selected = state.selected;
    if (!selected || state.chat) return;
    const key = selectionKey(selected),
      serial = ++detailSerial;
    detailController?.abort();
    detailController = new AbortController();
    const signal = detailController.signal;
    if (!quiet && state.detailKey !== key) {
      state.detail = null;
      setDetail('<p class="empty">Загрузка деталей…</p>');
    }
    errorBox("detail-error", "");
    try {
      let data;
      if (selected.kind === "component") {
        if (!state.system) await loadHealth();
        data = state.system;
      } else {
        const path = {
          task: `/tasks/${selected.id}`,
          meeting: `/meetings/${selected.id}/context`,
          result: `/meeting-results/${selected.id}`,
          thread: `/threads/${selected.id}?events_limit=30`,
          event: `/events/${selected.id}`,
        }[selected.kind];
        data = await api(path, { signal });
        if (quiet && selected.kind === "thread") {
          const wanted = state.detail?.events?.length || 30;
          while (data.has_more_events && data.events.length < wanted) {
            const page = await api(
              `/threads/${selected.id}?events_offset=${data.events.length}&events_limit=${Math.min(100, wanted - data.events.length)}`,
              { signal },
            );
            data = {
              ...data,
              events: [...data.events, ...page.events],
              has_more_events: page.has_more_events,
            };
            if (!page.events.length) break;
          }
        }
      }
      if (
        serial !== detailSerial ||
        key !== selectionKey(state.selected) ||
        state.chat
      )
        return;
      const changed =
        key !== state.detailKey ||
        JSON.stringify(data) !== JSON.stringify(state.detail);
      state.detail = data;
      state.detailKey = key;
      if (changed)
        setDetail(
          selected.kind === "task"
            ? taskHTML(data)
            : selected.kind === "meeting"
              ? meetingHTML(data)
              : selected.kind === "result"
                ? resultHTML(data)
                : selected.kind === "thread"
                  ? threadHTML(data)
                  : selected.kind === "event"
                    ? head("Исходное сообщение") + sourceHTML(data)
                    : systemHTML(selected.id),
          data.references,
          quiet,
        );
    } catch (err) {
      if (err.name !== "AbortError" && serial === detailSerial)
        errorBox("detail-error", failText(err), "detail");
    }
  }
  async function selectRecord(kind, id) {
    storeScroll();
    state.selected = { kind, id };
    state.chat = false;
    saveLocation();
    chrome();
    renderList();
    await loadDetail();
  }
  async function switchTab(tab) {
    clearTimeout(searchTimer);
    storeScroll();
    const old = state.views[tab];
    state.tab = tab;
    state.q = "";
    state.order = "rank";
    state.selected = null;
    state.chat = false;
    saveLocation();
    chrome();
    renderList();
    $("list-scroll").scrollTop = old?.scroll || 0;
    state.detailKey = "";
    setDetail('<p class="empty">Выберите запись слева.</p>');
    await loadList();
  }
  function paintHealth(error) {
    const value = error ? "UNKNOWN" : state.system?.overall_status || "UNKNOWN";
    $("health").className = `health ${C.healthClass(value, true)}`;
    const text = error
      ? "Нет связи с сервером"
      : ["OK", "BUSY"].includes(value)
        ? "Система в норме"
        : label(value);
    $("health-label").textContent = text;
    $("health").title = `${text} — открыть состояние компонентов`;
    $("health").setAttribute("aria-label", $("health").title);
  }
  async function loadHealth() {
    if (systemBusy) return;
    systemBusy = true;
    try {
      state.system = await api("/system/status");
      paintHealth();
      $("connection").hidden = navigator.onLine;
    } catch (err) {
      paintHealth(true);
      $("connection").textContent =
        failText(err) + " Показаны последние полученные данные.";
      $("connection").hidden = false;
    } finally {
      systemBusy = false;
    }
  }
  function renderChat() {
    const scroller = $("chat-scroll"),
      atBottom =
        scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <
        100,
      scroll = scroller.scrollTop;
    $("chat-messages").innerHTML = state.chatRows.length
      ? state.chatRows
          .map(
            (r) =>
              `<article class="chat-turn" data-request-id="${e(r.id)}"><div class="question">${e(r.query)}</div><div class="chat-meta">${e(date(r.created_at))} · ${e(label(r.status))}</div><div class="answer">${r.answer ? markdown(r.answer) : r.status === "FAILED" ? `<p class="inline-error">${e(r.error || "Не удалось получить ответ.")}</p>` : '<p class="muted">Ответ готовится на сервере…</p>'}${references(r.references)}</div></article>`,
          )
          .join("")
      : '<div class="empty"><h3>Что найти в архиве?</h3><p>Спросите о решении, поручении или договорённости. Ответ будет сопровождаться источниками.</p></div>';
    $("chat-messages")
      .querySelectorAll(".chat-turn")
      .forEach((el, i) => enhance(el, state.chatRows[i].references));
    if (atBottom) scroller.scrollTop = scroller.scrollHeight;
    else {
      scroller.scrollTop = scroll;
      $("new-messages").hidden = false;
    }
  }
  async function loadChat() {
    if (chatBusy) return;
    chatBusy = true;
    const revision = chatRevision;
    try {
      const rows = await api("/chat/requests?limit=100");
      if (revision !== chatRevision) return;
      const initial = !state.chatRows.length;
      const byId = new Map(state.chatRows.map((r) => [r.id, r]));
      rows.forEach((r) => byId.set(r.id, r));
      const merged = [...byId.values()].sort(
        (a, b) =>
          a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
      );
      if (initial) $("older-chat").hidden = rows.length < 100;
      if (JSON.stringify(merged) !== JSON.stringify(state.chatRows)) {
        state.chatRows = merged;
        renderChat();
      } else if (!rows.length) renderChat();
      errorBox("chat-error", "");
    } catch (err) {
      errorBox("chat-error", failText(err), "chat");
    } finally {
      chatBusy = false;
    }
  }
  async function openChat(open) {
    storeScroll();
    state.chat = open;
    saveLocation();
    chrome();
    if (open) {
      await loadChat();
      $("message").focus();
    } else if (state.selected) await loadDetail();
    else if (state.tab === "status") renderSystem();
  }
  async function mutate(button, path, options = {}) {
    if (actionBusy) return;
    actionBusy = true;
    button.disabled = true;
    try {
      const result = await api(path, { method: "POST", ...options });
      toast("Изменения сохранены");
      await loadList({ quiet: true });
      return result;
    } catch (err) {
      toast(failText(err), true);
    } finally {
      actionBusy = false;
      button.disabled = false;
    }
  }
  let reminderTaskId = null,
    dialogFocus = null;
  function openDialog(id) {
    dialogFocus = document.activeElement;
    $(id).showModal();
  }
  function closeDialog(dialog) {
    if (dialog.querySelector("[type=submit]:disabled")) {
      toast("Дождитесь завершения сохранения.");
      return;
    }
    dialog.close();
    dialogFocus?.focus({ preventScroll: true });
  }
  async function action(event) {
    const b = event.target.closest("button");
    if (!b) return;
    if (b.dataset.openKind) {
      await selectRecord(b.dataset.openKind, b.dataset.openId);
      return;
    }
    if (b.dataset.copy) {
      try {
        await navigator.clipboard.writeText(
          b.parentElement.querySelector("code").textContent,
        );
        toast("Код скопирован");
      } catch {
        toast("Не удалось скопировать. Выделите текст вручную.", true);
      }
      return;
    }
    if (b.dataset.retry) {
      const r = b.dataset.retry;
      await (r === "list"
        ? refresh()
        : r === "detail"
          ? loadDetail()
          : loadChat());
      return;
    }
    if (b.dataset.close !== undefined) {
      closeDialog(b.closest("dialog"));
      return;
    }
    const selected = state.selected;
    if (b.dataset.removeReminder && selected?.kind === "task") {
      await mutate(
        b,
        `/tasks/${selected.id}/reminders/${b.dataset.removeReminder}`,
        { method: "DELETE" },
      );
      return;
    }
    if (!selected) return;
    if (
      ["complete", "confirm", "reject"].includes(b.dataset.action) &&
      selected.kind === "task"
    )
      await mutate(b, `/tasks/${selected.id}/${b.dataset.action}`);
    if (b.dataset.action === "remind" && selected.kind === "task") {
      if (reminderTaskId !== selected.id) $("reminder-time").value = "";
      reminderTaskId = selected.id;
      $("reminder-task").textContent = state.detail.task.title;
      openDialog("reminder-dialog");
    }
    if (b.dataset.action === "context" && selected.kind === "meeting")
      await mutate(b, `/meetings/${selected.id}/context/refresh`);
    if (b.dataset.action === "more-events" && selected.kind === "thread") {
      b.disabled = true;
      const key = selectionKey(selected),
        previous = state.detail;
      try {
        const next = await api(
          `/threads/${selected.id}?events_offset=${previous.events.length}&events_limit=30`,
        );
        if (key === selectionKey(state.selected) && !state.chat) {
          state.detail = {
            ...next,
            events: [
              ...previous.events,
              ...next.events.filter(
                (x) => !previous.events.some((ev) => ev.id === x.id),
              ),
            ],
          };
          setDetail(threadHTML(state.detail), [], true);
        }
      } catch (err) {
        toast(failText(err), true);
      } finally {
        b.disabled = false;
      }
    }
  }
  document.addEventListener("click", (event) => {
    action(event).catch((err) => toast(failText(err), true));
  });
  $("tabs").addEventListener("click", (ev) => {
    const b = ev.target.closest("[data-tab]");
    if (b) switchTab(b.dataset.tab);
  });
  $("search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    state.q = $("search").value;
    state.selected = null;
    listController?.abort();
    listSerial++;
    detailController?.abort();
    detailSerial++;
    state.detailKey = "";
    state.detail = null;
    $("clear-search").hidden = !state.q;
    saveLocation(true);
    setDetail('<p class="empty">Выберите запись в результатах поиска.</p>');
    searchTimer = setTimeout(() => {
      view().rows = [];
      view().loaded = false;
      view().hasMore = false;
      renderList();
      loadList();
    }, 300);
  });
  $("clear-search").onclick = () => {
    $("search").value = "";
    $("search").dispatchEvent(new Event("input"));
    $("search").focus();
  };
  $("sort").onchange = () => {
    state.order = $("sort").value;
    saveLocation(true);
    loadList();
  };
  $("load-more").onclick = () => {
    if (!listLoading) loadList({ more: true });
  };
  $("refresh").onclick = () => {
    document.querySelector(".view-menu").open = false;
    refresh();
  };
  $("health").onclick = () => switchTab("status");
  $("chat-toggle").onclick = () => openChat(!state.chat);
  $("chat-close").onclick = () => openChat(false);
  $("create-task").onclick = () => openDialog("task-dialog");
  document.querySelectorAll("dialog").forEach((d) =>
    d.addEventListener("cancel", (ev) => {
      ev.preventDefault();
      closeDialog(d);
    }),
  );
  $("task-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const button =
      ev.submitter || $("task-form").querySelector("[type=submit]");
    if (button.disabled) return;
    errorBox("task-form-error", "");
    button.disabled = true;
    try {
      const title = $("task-title").value.trim();
      if (!title) throw new Error("Введите название задачи.");
      const task = await api("/tasks", {
        method: "POST",
        body: JSON.stringify({
          title,
          description: $("task-description").value.trim() || null,
          priority: $("task-priority").value,
          due_at: C.zonedISO($("task-due").value, state.zone),
        }),
      });
      button.disabled = false;
      $("task-form").reset();
      closeDialog($("task-dialog"));
      toast("Задача создана");
      await switchTab("tasks");
      await selectRecord("task", task.id);
    } catch (err) {
      errorBox("task-form-error", failText(err));
    } finally {
      button.disabled = false;
    }
  };
  $("reminder-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const button =
      ev.submitter || $("reminder-form").querySelector("[type=submit]");
    if (button.disabled) return;
    button.disabled = true;
    errorBox("reminder-error", "");
    try {
      const remind_at = C.zonedISO($("reminder-time").value, state.zone);
      if (!remind_at || Date.parse(remind_at) <= Date.now())
        throw new Error("Укажите время в будущем.");
      await api(`/tasks/${reminderTaskId}/reminders`, {
        method: "POST",
        body: JSON.stringify({ remind_at }),
      });
      button.disabled = false;
      closeDialog($("reminder-dialog"));
      toast("Напоминание добавлено");
      await loadDetail({ quiet: true });
    } catch (err) {
      errorBox("reminder-error", failText(err));
    } finally {
      button.disabled = false;
    }
  };
  $("chat-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const query = $("message").value.trim();
    if (!query || $("send").disabled) return;
    $("send").disabled = true;
    errorBox("chat-error", "");
    try {
      const history = state.chatRows
        .flatMap((r) => [
          { role: "user", content: r.query },
          ...(r.answer ? [{ role: "assistant", content: r.answer }] : []),
        ])
        .slice(-20)
        .map((m) => ({ ...m, content: m.content.slice(0, 8000) }));
      const row = await api("/chat/requests", {
        method: "POST",
        body: JSON.stringify({ query, history }),
      });
      chatRevision++;
      state.chatRows.push(row);
      if ($("message").value.trim() === query) $("message").value = "";
      renderChat();
      $("chat-scroll").scrollTop = $("chat-scroll").scrollHeight;
    } catch (err) {
      errorBox(
        "chat-error",
        failText(err) + " Вопрос сохранён в поле ввода.",
        "chat",
      );
    } finally {
      $("send").disabled = false;
    }
  };
  $("message").onkeydown = (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
      ev.preventDefault();
      $("chat-form").requestSubmit();
    }
  };
  $("older-chat").onclick = async () => {
    const button = $("older-chat");
    if (button.disabled || !state.chatRows.length) return;
    button.disabled = true;
    try {
      const rows = await api(
        `/chat/requests?limit=100&before=${state.chatRows[0].id}`,
      );
      const scroller = $("chat-scroll"),
        height = scroller.scrollHeight,
        top = scroller.scrollTop;
      state.chatRows = [
        ...rows.filter((r) => !state.chatRows.some((old) => old.id === r.id)),
        ...state.chatRows,
      ];
      renderChat();
      button.hidden = rows.length < 100;
      scroller.scrollTop = top + scroller.scrollHeight - height;
    } catch (err) {
      errorBox("chat-error", failText(err), "chat");
    } finally {
      button.disabled = false;
    }
  };
  $("new-messages").onclick = () => {
    $("chat-scroll").scrollTop = $("chat-scroll").scrollHeight;
    $("new-messages").hidden = true;
  };
  window.addEventListener("popstate", async () => {
    clearTimeout(searchTimer);
    fromLocation();
    chrome();
    renderList();
    await loadList();
    if (state.chat) await loadChat();
    $("list-scroll").scrollTop = history.state?.listScroll || 0;
    $("detail").scrollTop = history.state?.detailScroll || 0;
  });
  async function configure() {
    const config = await api("/ui/config");
    new Intl.DateTimeFormat("ru", { timeZone: config.timezone });
    state.zone = config.timezone;
    state.configured = true;
    $("timezone").textContent = `Часовой пояс: ${state.zone}`;
    document
      .querySelectorAll(".form-zone")
      .forEach(
        (el) => (el.textContent = `Время в часовом поясе ${state.zone}`),
      );
  }
  async function refresh() {
    try {
      if (!state.configured) await configure();
      await Promise.all([
        loadHealth(),
        listLoading ? Promise.resolve() : loadList({ quiet: true }),
        state.chat ? loadChat() : Promise.resolve(),
      ]);
    } catch (err) {
      errorBox("list-error", failText(err), "list");
    }
  }
  const pendingTopics = new Set();
  let liveTimer = null,
    liveBusy = false;
  function queueLive(topics) {
    topics.forEach((topic) => pendingTopics.add(topic));
    if (!liveTimer) liveTimer = setTimeout(flushLive, 250);
  }
  async function flushLive() {
    liveTimer = null;
    if (document.hidden) return;
    if (
      liveBusy ||
      actionBusy ||
      listLoading ||
      chatBusy ||
      document.querySelector("dialog[open]")
    ) {
      liveTimer = setTimeout(flushLive, 250);
      return;
    }
    const topics = new Set(pendingTopics);
    pendingTopics.clear();
    const has = (...names) =>
      topics.has("all") || names.some((n) => topics.has(n));
    liveBusy = true;
    try {
      if (!state.configured) await configure();
      const listChanged = {
        tasks: has("tasks", "meetings"),
        meetings: has("meetings"),
        results: has("results", "events"),
        threads: has("threads", "events"),
        status: has("status", "tasks", "events", "chat", "contexts"),
      }[state.tab];
      const detailChanged = {
        task: has("tasks", "events"),
        meeting: has("contexts", "meetings", "events"),
        result: has("results", "events"),
        thread: has("threads", "events"),
        event: has("events"),
        component: has("status"),
      }[state.selected?.kind];
      // Only invalidate visible content. Existing renderers preserve selection,
      // scroll and drafts; defer while a mutation/form is in progress.
      if (listChanged) await loadList({ quiet: true });
      else if (detailChanged && !state.chat) await loadDetail({ quiet: true });
      if (state.chat && has("chat")) await loadChat();
      await loadHealth();
    } catch (err) {
      errorBox("list-error", failText(err), "list");
    } finally {
      liveBusy = false;
      if (pendingTopics.size) queueLive([]);
    }
  }
  const live = window.SecretaryRealtime?.(queueLive);
  window.addEventListener("online", refresh);
  window.addEventListener("offline", () => {
    paintHealth(true);
    $("connection").textContent =
      "Нет соединения. Показаны последние полученные данные.";
    $("connection").hidden = false;
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.altKey && ev.code === "KeyR") {
      ev.preventDefault();
      refresh();
    }
  });
  let touch = null;
  $("list-scroll").addEventListener(
    "touchstart",
    (ev) => {
      if (
        ev.touches.length !== 1 ||
        ev.target.closest("input,textarea,select,a") ||
        window.getSelection()?.toString()
      )
        return;
      touch = {
        x: ev.touches[0].clientX,
        y: ev.touches[0].clientY,
        top: $("list-scroll").scrollTop === 0,
      };
    },
    { passive: true },
  );
  $("list-scroll").addEventListener(
    "touchend",
    (ev) => {
      if (!touch || !ev.changedTouches.length) return;
      const dx = ev.changedTouches[0].clientX - touch.x,
        dy = ev.changedTouches[0].clientY - touch.y,
        top = touch.top;
      touch = null;
      if (window.getSelection()?.toString()) return;
      if (dy > 95 && Math.abs(dx) < 45 && top) refresh();
      else if (Math.abs(dx) > 90 && Math.abs(dy) < 45) {
        const index = tabs.indexOf(state.tab) + (dx < 0 ? 1 : -1);
        if (tabs[index]) switchTab(tabs[index]);
      }
    },
    { passive: true },
  );
  $("list-scroll").addEventListener("touchcancel", () => (touch = null), {
    passive: true,
  });
  setInterval(async () => {
    if (
      document.hidden ||
      live?.connected ||
      pollBusy ||
      actionBusy ||
      document.querySelector("dialog[open]")
    )
      return;
    pollBusy = true;
    try {
      if (state.chat) await loadChat();
      else if (
        state.selected?.kind === "meeting" &&
        ["PENDING", "PROCESSING"].includes(state.detail?.status)
      )
        await loadDetail({ quiet: true });
    } finally {
      pollBusy = false;
    }
  }, 5000);
  setInterval(() => {
    if (
      !document.hidden &&
      !live?.connected &&
      !actionBusy &&
      !document.querySelector("dialog[open]")
    )
      refresh();
  }, 30000);
  fromLocation();
  chrome();
  refresh();
})();
