// Synthetic archive only. Never load production data into screenshots/tests.
const now = new Date();
const stamp = (days = 0, hour = 11, minute = 30) =>
  new Date(
    Date.UTC(
      now.getUTCFullYear(),
      now.getUTCMonth(),
      now.getUTCDate() + days,
      hour,
      minute,
    ),
  ).toISOString();
const person = (name, address) => ({ name, address });
const people = [
  person("Анна Белова", "anna@example.test"),
  person("Максим", "maxim@example.test"),
  person("Максим Орлов", "orlov@example.test"),
];
function fixtures() {
  const source = {
    id: "event1",
    source_id: "work-mail",
    source_label: "Рабочая почта",
    source_type: "exchange",
    subject: "Согласование сервисов для пилота",
    author: "Анна Белова <anna@example.test>",
    participants: people,
    occurred_at: stamp(0, 7, 12),
    event_type: "email",
    direction: "INCOMING",
    body: "Максим, прошу до 18:00 подтвердить состав сервисов для пилота.\n\nОбращаюсь к maxim@example.test, владельцу направления интеграции. Максим Орлов участвует в обсуждении инфраструктуры.\n\nБазовый сценарий: каталог и остатки. Статусы заказов включим на втором этапе.",
    source_url: "https://mail.example.test/messages/pilot",
  };
  const tasks = [
    {
      id: "task1",
      title: "Согласовать состав сервисов для пилота",
      description:
        "Подтвердить **каталог и остатки** для первого этапа.\n\n- Согласовать ограничения обмена.\n- Указать владельцев сервисов.\n- Передать решение команде запуска.",
      status: "NEW",
      priority: "HIGH",
      due_at: stamp(0, 15, 0),
      source_event_id: "event1",
      evidence:
        "Анна уточнила адрес получателя: maxim@example.test. Задача адресована владельцу направления интеграции; другой Максим отвечает за инфраструктуру.",
      confidence: 0.98,
      ranking_score: 86.4,
      ranking_reasons: [
        "Срок наступает сегодня",
        "Высокий приоритет исходного письма",
        "Результат нужен для запуска пилота",
      ],
      manually_created: false,
      completed_at: null,
      reminders: [],
      created_at: stamp(-1),
      updated_at: stamp(),
    },
    {
      id: "task2",
      title: "Подготовить оценку сроков интеграции",
      description: "Уточнить зависимости и предварительную оценку.",
      status: "NEEDS_CONFIRMATION",
      priority: "NORMAL",
      due_at: stamp(1, 13),
      source_event_id: "event1",
      evidence:
        "В переписке несколько участников с именем Максим. Получатель поручения требует подтверждения.",
      confidence: 0.6,
      ranking_score: 58,
      ranking_reasons: ["Ожидает подтверждения"],
      manually_created: false,
      completed_at: null,
      reminders: [],
      created_at: stamp(-1),
      updated_at: stamp(),
    },
    {
      id: "task3",
      title: "Обновить план запуска пилота",
      description: "Добавить согласованные этапы и ответственных.",
      status: "IN_PROGRESS",
      priority: "NORMAL",
      due_at: stamp(3, 14),
      source_event_id: null,
      evidence: null,
      confidence: null,
      ranking_score: 32,
      ranking_reasons: [],
      manually_created: true,
      completed_at: null,
      reminders: [],
      created_at: stamp(-1),
      updated_at: stamp(),
    },
  ];
  const meetings = [
    {
      id: "meeting1",
      source_id: "work-mail",
      source_label: "Рабочая почта",
      source_event_id: "event1",
      title: "Синхронизация по проекту «Орион»",
      starts_at: stamp(0, 15, 30),
      ends_at: stamp(0, 16),
      all_day: false,
      location: "МТС Линк · проектная команда",
      organizer: people[0],
      attendees: people,
      status: "CONFIRMED",
      method: "REQUEST",
      mts_link_url: "https://meet.example.test/orion",
    },
    {
      id: "meeting2",
      source_id: "work-mail",
      source_label: "Рабочая почта",
      source_event_id: "event1",
      title: "Архитектурный разбор интеграции",
      starts_at: stamp(0, 16),
      ends_at: stamp(0, 17),
      all_day: false,
      location: "Переговорная «Север»",
      organizer: people[0],
      attendees: people,
      status: "CONFIRMED",
      method: "REQUEST",
    },
    {
      id: "future",
      source_id: "work-mail",
      source_label: "Рабочая почта",
      source_event_id: "event1",
      title: "Планирование следующего этапа",
      starts_at: stamp(2, 10),
      ends_at: stamp(2, 11),
      all_day: false,
      location: "Онлайн",
      organizer: people[0],
      attendees: people,
      status: "CONFIRMED",
      method: "REQUEST",
    },
  ];
  const refs = [
    {
      key: "E1",
      kind: "event",
      id: "event1",
      title: source.subject,
      source_label: "Рабочая почта",
      occurred_at: source.occurred_at,
      snippet:
        "Подтвердить каталог и остатки для первого этапа. Статусы заказов — второй этап.",
      source_url: source.source_url,
    },
    {
      key: "E2",
      kind: "event",
      id: "event2",
      meeting_result_id: "result1",
      title: "Синхронизация по проекту «Орион»",
      source_label: "МТС Линк",
      occurred_at: stamp(-1),
      snippet: "Согласованы границы пилота и владельцы открытых вопросов.",
      source_url: null,
    },
  ];
  const summary =
    "## Главное к обсуждению\nНа предыдущей встрече согласовали **границы пилота**. Сегодня нужно подтвердить состав сервисов и ограничения обмена. [E2]\n\n### Действующие договорённости\n- На первом этапе включаем каталог и остатки. [E1]\n- Оценка сроков будет уточнена после согласования перечня. [E2]\n\n### Открытые вопросы\n1. Кто подтвердит готовность смежной системы?\n2. Какие ограничения могут повлиять на запуск?\n\n> Предложение к повестке: зафиксировать владельца и срок по каждому открытому вопросу.";
  const contexts = Object.fromEntries(
    meetings.map((m) => [
      m.id,
      {
        meeting: m,
        status: m.id === "future" ? "NOT_REQUESTED" : "READY",
        summary: m.id === "future" ? null : summary,
        references: m.id === "future" ? [] : refs,
        generated_at: m.id === "future" ? null : stamp(0, 7, 30),
        error: null,
        stale: false,
      },
    ]),
  );
  const results = [
    {
      id: "result1",
      source_id: "mts",
      source_label: "МТС Линк",
      source_event_id: "event2",
      calendar_meeting_id: "meeting1",
      title: "Синхронизация по проекту «Орион»",
      starts_at: stamp(-1, 11),
      ends_at: stamp(-1, 12),
      owner_name: "Анна Белова",
      meeting_url: null,
      transcript_status: "ready",
      summary:
        "Согласовали **базовый сценарий обмена** и порядок запуска.\n\nОценка сроков зависит от подтверждения состава сервисов.",
      decisions: [
        "Включить в пилот каталог и остатки.",
        "Статусы заказов перенести на второй этап.",
      ],
      agreements: [
        "Максим подтверждает состав сервисов.",
        "Анна собирает ограничения команды.",
      ],
      analysis_state: "COMPLETED",
      analyzed_at: stamp(-1, 13),
      origin_type: "mts_transcript",
      supplement_count: 1,
      brief_summary:
        "Границы пилота согласованы. Открыт вопрос по ограничениям интеграции.",
      time_known: true,
      received_at: stamp(-1, 12),
      participants: people,
      participant_summaries: [
        {
          source_event_id: "event1",
          source_label: "Рабочая почта",
          author: "Анна Белова",
          occurred_at: stamp(-1, 13),
          summary:
            "Уточнили владельцев сервисов. Просим подтвердить перечень до следующей встречи.",
          decisions: [],
          agreements: [],
        },
      ],
    },
  ];
  const threads = [
    {
      id: "thread1",
      source_id: "work-mail",
      source_label: "Рабочая почта",
      source_type: "exchange",
      title: source.subject,
      participants: people,
      summary:
        "Анна прислала уточнённый список. Требуется подтвердить **два сервиса** и ограничения первого этапа.",
      event_count: 2,
      first_event_at: stamp(-2),
      last_event_at: stamp(0, 7, 12),
      summarized_at: stamp(0, 7, 15),
      events: [
        {
          id: "event1",
          event_type: "email",
          direction: "INCOMING",
          subject: source.subject,
          author: source.author,
          occurred_at: source.occurred_at,
          preview: source.body,
          source_url: source.source_url,
        },
        {
          id: "event2",
          event_type: "email",
          direction: "INCOMING",
          subject: "Предварительные ограничения",
          author: "Максим Орлов",
          occurred_at: stamp(-1),
          preview:
            "Передали ограничения инфраструктуры. Просим учесть их при оценке.",
          source_url: null,
        },
      ],
      has_more_events: false,
      events_offset: 0,
      events_limit: 30,
    },
  ];
  const system = {
    overall_status: "BUSY",
    generated_at: stamp(),
    components: [
      {
        id: "source-mail",
        label: "Рабочая почта",
        component_type: "loader",
        status: "OK",
        message: "Почта синхронизирована",
        metrics: { loaded: 3, poll_interval_seconds: 60 },
        observed_at: stamp(),
        expires_at: stamp(1),
      },
      {
        id: "source-mts",
        label: "МТС Линк",
        component_type: "loader",
        status: "OK",
        message: "Новых записей нет",
        metrics: { poll_interval_seconds: 900, loaded: 0 },
        observed_at: stamp(),
        expires_at: stamp(1),
      },
      {
        id: "ollama",
        label: "Ollama / Qwen",
        component_type: "llm",
        status: "BUSY",
        message: "Готовится ответ по архиву",
        metrics: { active_requests: 1 },
        observed_at: stamp(),
        expires_at: stamp(1),
      },
      {
        id: "processing",
        label: "Обработка событий",
        component_type: "worker",
        status: "OK",
        message: null,
        metrics: { pending: 4, processing: 1, failed: 0 },
        observed_at: stamp(),
        expires_at: stamp(1),
      },
      {
        id: "ollama-semaphore",
        label: "Семафор Ollama",
        component_type: "semaphore",
        status: "BUSY",
        message: "Ожидание очереди",
        metrics: { active: 1, capacity: 1, waiting: 2 },
        observed_at: stamp(),
        expires_at: stamp(1),
      },
    ],
  };
  const chat = [
    {
      id: "request1",
      query: "Что осталось согласовать перед запуском пилота «Орион»?",
      status: "COMPLETED",
      answer:
        "## Два открытых вопроса\n1. **Состав сервисов.** Максим должен подтвердить перечень сегодня до 18:00. [E1]\n2. **Ограничения интеграции.** Команда ожидает уточнений перед оценкой сроков. [E2]\n\nПоследнее письмо пока не содержит подтверждения согласования.\n\n| Вопрос | Следующий шаг |\n| --- | --- |\n| Каталог и остатки | Подтвердить перечень |\n| Ограничения | Собрать комментарии команды |",
      references: refs,
      error: null,
      attempts: 1,
      created_at: stamp(0, 8),
      updated_at: stamp(0, 8, 1),
      completed_at: stamp(0, 8, 1),
    },
  ];
  return {
    source,
    tasks,
    meetings,
    contexts,
    results,
    threads,
    system,
    chat,
    summary,
    refs,
  };
}
function createAPI(data = fixtures()) {
  const calls = [];
  async function handle(url, options = {}) {
    const u = new URL(url, "http://localhost"),
      p = u.pathname.replace("/api/v1", ""),
      method = options.method || "GET",
      body = options.body ? JSON.parse(options.body) : {};
    calls.push({ path: p, method, body, query: u.search });
    const ok = (value) => ({
      ok: true,
      status: 200,
      json: async () => structuredClone(value),
    });
    if (p === "/ui/config") return ok({ timezone: "Europe/Moscow" });
    if (p === "/system/status") return ok(data.system);
    if (p === "/plans/today")
      return ok({ meetings: data.meetings.slice(0, 2), items: [] });
    if (p === "/tasks" && method === "POST") {
      const t = {
        ...data.tasks[2],
        ...body,
        id: `task${data.tasks.length + 1}`,
      };
      data.tasks.push(t);
      return ok(t);
    }
    if (p === "/tasks")
      return ok(
        data.tasks.filter(
          (t) =>
            !["COMPLETED", "CANCELLED"].includes(t.status) &&
            t.title
              .toLowerCase()
              .includes((u.searchParams.get("q") || "").toLowerCase()),
        ),
      );
    for (const [path, key] of [
      ["/meetings", "meetings"],
      ["/meeting-results", "results"],
      ["/threads", "threads"],
    ])
      if (p === path) {
        const rows = data[key].filter((t) =>
            t.title
              .toLowerCase()
              .includes((u.searchParams.get("q") || "").toLowerCase()),
          ),
          offset = +(u.searchParams.get("offset") || 0),
          limit = +(u.searchParams.get("limit") || 30);
        return ok({
          items: rows.slice(offset, offset + limit),
          offset,
          limit,
          has_more: offset + limit < rows.length,
        });
      }
    const seg = p.split("/").filter(Boolean),
      id = seg[1];
    if (seg[0] === "tasks" && id) {
      const t = data.tasks.find((t) => t.id === id);
      if (!t) return { ok: false, status: 404, json: async () => ({}) };
      if (seg[2]) {
        if (seg[2] === "reminders")
          t.reminders.push({
            id: "reminder1",
            remind_at: body.remind_at,
            sent_at: null,
            enabled: true,
          });
        else
          t.status = {
            complete: "COMPLETED",
            confirm: "NEW",
            reject: "CANCELLED",
          }[seg[2]];
        return ok(t);
      }
      return ok({ task: t, source: t.source_event_id ? data.source : null });
    }
    if (seg[0] === "meetings" && seg[2] === "context") {
      const c = data.contexts[id];
      if (method === "POST") {
        c.status = "PENDING";
        setTimeout(() => {
          c.status = "READY";
          c.summary = data.summary;
          c.references = data.refs;
          c.generated_at = stamp();
        }, 50);
      }
      return ok(c);
    }
    if (seg[0] === "meeting-results" && id)
      return ok(data.results.find((r) => r.id === id));
    if (seg[0] === "threads" && id)
      return ok(data.threads.find((t) => t.id === id));
    if (seg[0] === "events" && id) return ok({ ...data.source, id });
    if (p === "/chat/requests") {
      if (method === "POST") {
        const r = {
          ...data.chat[0],
          id: `request${data.chat.length + 1}`,
          query: body.query,
          status: "PENDING",
          answer: null,
          references: [],
          created_at: new Date().toISOString(),
        };
        data.chat.push(r);
        return ok(r);
      }
      return ok(data.chat);
    }
    return {
      ok: false,
      status: 404,
      json: async () => ({ detail: "Not found" }),
    };
  }
  return { data, calls, handle };
}
module.exports = { fixtures, createAPI };
