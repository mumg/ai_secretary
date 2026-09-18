const { test } = require("node:test"),
  assert = require("node:assert/strict"),
  fs = require("node:fs"),
  path = require("node:path");
const { JSDOM } = require("jsdom"),
  { createAPI } = require("./fixtures.cjs");
const root = path.resolve(__dirname, "../../backend/web");
const wait = () => new Promise((resolve) => setTimeout(resolve, 15));
async function settle() {
  for (let i = 0; i < 4; i++) await wait();
}
function setup(t, api = createAPI(), hash = "") {
  const dom = new JSDOM(fs.readFileSync(path.join(root, "app.html"), "utf8"), {
    url: `http://localhost/app/${hash}`,
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const w = dom.window;
  w.fetch = api.handle;
  w.AbortController = AbortController;
  w.structuredClone = structuredClone;
  w.setInterval = () => 0;
  const sockets = [];
  w.WebSocket = class {
    constructor(url) {
      this.url = String(url);
      this.readyState = 0;
      sockets.push(this);
      queueMicrotask(() => { this.open(); this.message({ type: "status", data: api.data.system }); });
    }
    open() {
      this.readyState = 1;
      this.onopen?.();
    }
    message(data) {
      this.onmessage?.({ data: JSON.stringify(data) });
    }
    send(value) {
      this.sent = value;
    }
    close() {
      this.readyState = 3;
      this.onclose?.();
    }
  };
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  w.HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  for (const file of [
    "markdown-it.min.js",
    "app-core.js",
    "realtime.js",
    "app.js",
  ])
    w.eval(fs.readFileSync(path.join(root, "assets", file), "utf8"));
  t.after(() => dom.window.close());
  return { w, d: w.document, api, sockets };
}
function click(d, selector) {
  assert.ok(d.querySelector(selector), selector);
  d.querySelector(selector).click();
}
test("initial plan, task action, same reading pane and API-confirmed completion", async (t) => {
  const { d, api } = setup(t);
  await settle();
  click(d, '[data-open-id="task1"]');
  await settle();
  assert.match(d.querySelector("#detail").textContent, /Основание назначения/);
  assert.match(d.querySelector("#detail").textContent, /orlov@example.test/);
  assert.equal(d.querySelector(".list-pane").hidden, false);
  click(d, '[data-action="complete"]');
  await settle();
  assert.equal(api.data.tasks[0].status, "COMPLETED");
  assert.match(d.querySelector("#detail").textContent, /Завершена/);
  assert.ok(!d.querySelector('#list [data-open-id="task1"]'));
});
test("confirmed task can be rejected and disappears from active plan", async (t) => {
  const { d, api } = setup(t);
  await settle();
  click(d, '[data-open-id="task1"]');
  await settle();
  assert.equal(d.querySelector('[data-action="reject"]').getAttribute("aria-label"), "Отказаться от задачи");
  click(d, '[data-action="reject"]');
  await settle();
  assert.equal(api.data.tasks[0].status, "CANCELLED");
  assert.ok(api.calls.some((c) => c.method === "POST" && c.path === "/tasks/task1/reject"));
  assert.ok(!d.querySelector('#list [data-open-id="task1"]'));
  assert.ok(!d.querySelector('[data-action="reject"]'));
});
test("failed rejection leaves task active and shows an error", async (t) => {
  const api = createAPI(), original = api.handle;
  api.handle = async (url, options) => url.endsWith("/reject")
    ? { ok: false, status: 503, json: async () => ({ detail: "Попробуйте позже" }) }
    : original(url, options);
  const { d } = setup(t, api);
  await settle();
  click(d, '[data-open-id="task1"]');
  await settle();
  click(d, '[data-action="reject"]');
  await settle();
  assert.equal(api.data.tasks[0].status, "NEW");
  assert.ok(d.querySelector('#list [data-open-id="task1"]'));
  assert.match(d.querySelector("#toast").textContent, /Попробуйте позже/);
});
test("future meeting GET never requests generation; explicit button queues it", async (t) => {
  const { d, api } = setup(t);
  await settle();
  click(d, '[data-tab="meetings"]');
  await settle();
  click(d, '[data-open-id="future"]');
  await settle();
  assert.equal(api.calls.filter((x) => x.method === "POST").length, 0);
  assert.match(d.querySelector("#detail").textContent, /Создать контекст/);
  click(d, '[data-action="context"]');
  await settle();
  assert.equal(
    api.calls.filter((x) => x.path === "/meetings/future/context/refresh")
      .length,
    1,
  );
  assert.equal(d.querySelector(".list-pane").hidden, false);
});
test("stale detail response cannot replace the selected record", async (t) => {
  const api = createAPI(),
    original = api.handle;
  let resolve;
  api.handle = async (url, opts) => {
    if (url === "/api/v1/tasks/task1") return new Promise((r) => (resolve = r));
    return original(url, opts);
  };
  const { d } = setup(t, api);
  await settle();
  click(d, '[data-open-id="task1"]');
  await wait();
  click(d, '[data-open-id="task2"]');
  await settle();
  resolve(await original("/api/v1/tasks/task1"));
  await settle();
  assert.match(d.querySelector("#detail h2").textContent, /оценку сроков/);
});
test("search suppresses old response and searches server archive", async (t) => {
  const api = createAPI(),
    original = api.handle;
  let resolve;
  api.handle = async (url, opts) =>
    url.includes("q=old")
      ? new Promise((r) => (resolve = r))
      : original(url, opts);
  const { w, d } = setup(t, api);
  await settle();
  const input = d.querySelector("#search");
  input.value = "old";
  input.dispatchEvent(new w.Event("input"));
  await new Promise((r) => setTimeout(r, 330));
  input.value = "состав";
  input.dispatchEvent(new w.Event("input"));
  await new Promise((r) => setTimeout(r, 350));
  resolve({ ok: true, status: 200, json: async () => [] });
  await settle();
  assert.match(d.querySelector("#list").textContent, /Согласовать состав/);
  assert.match(w.location.hash, /q=/);
});
test("failed refresh retains current list and provides a retry", async (t) => {
  const api = createAPI(),
    original = api.handle;
  let fail = false;
  api.handle = async (url, opts) => {
    if (fail && url.startsWith("/api/v1/tasks?"))
      throw new TypeError("network");
    return original(url, opts);
  };
  const { d } = setup(t, api);
  await settle();
  fail = true;
  click(d, "#refresh");
  await settle();
  assert.match(d.querySelector("#list").textContent, /Согласовать состав/);
  assert.equal(d.querySelector("#list-error").hidden, false);
  fail = false;
  click(d, '[data-retry="list"]');
  await settle();
  assert.equal(d.querySelector("#list-error").hidden, true);
});
test("chat Markdown, references and queue survive switching sections", async (t) => {
  const { w, d, api } = setup(t);
  await settle();
  click(d, "#chat-toggle");
  await settle();
  assert.ok(d.querySelector("#chat-messages strong"));
  assert.ok(d.querySelector("#chat-messages table"));
  d.querySelector("#message").value = "Какие сроки?";
  d.querySelector("#chat-form").dispatchEvent(
    new w.Event("submit", { cancelable: true }),
  );
  await settle();
  assert.equal(api.data.chat.at(-1).query, "Какие сроки?");
  assert.match(d.querySelector("#chat-messages").textContent, /В очереди/);
  click(d, '[data-tab="threads"]');
  await settle();
  click(d, "#chat-toggle");
  await settle();
  assert.match(d.querySelector("#chat-messages").textContent, /Какие сроки/);
  click(d, '#chat-messages [data-open-id="event1"]');
  await settle();
  assert.equal(d.querySelector("#chat-pane").hidden, true);
  assert.match(d.querySelector("#detail").textContent, /maxim@example.test/);
});
test("manual creation submits priority and due time in the server zone", async (t) => {
  const { w, d, api } = setup(t);
  await settle();
  click(d, "#create-task");
  d.querySelector("#task-title").value = "Новая задача";
  d.querySelector("#task-priority").value = "HIGH";
  d.querySelector("#task-due").value = "2030-09-15T13:00";
  d.querySelector("#task-form").dispatchEvent(
    new w.Event("submit", { cancelable: true }),
  );
  await settle();
  const create = api.calls.find(
    (x) => x.path === "/tasks" && x.method === "POST",
  );
  assert.equal(create.body.due_at, "2030-09-15T10:00:00.000Z");
  assert.equal(create.body.priority, "HIGH");
  assert.equal(d.querySelector("#task-dialog").open, false);
  assert.match(d.querySelector("#detail h2").textContent, /Новая задача/);
});
test("busy global indicator stays green; missing connection is a problem", async (t) => {
  const { w, d } = setup(t);
  await settle();
  assert.match(d.querySelector("#health").className, /ok/);
  w.dispatchEvent(new w.Event("offline"));
  assert.match(d.querySelector("#health").className, /warning/);
  assert.equal(d.querySelector("#connection").hidden, false);
});
test("Markdown and source URLs cannot execute HTML or load tracking images", async (t) => {
  const { w } = setup(t);
  await settle();
  const C = w.SecretaryCore;
  const html = C.markdown(
    "<script>alert(1)</script>\n\n![tracker](https://evil.example/pixel)\n\n[x](javascript:alert(1))\n\n**bold**\n\n```js\nalert(1)\n```",
  );
  const div = w.document.createElement("div");
  div.innerHTML = html;
  assert.equal(div.querySelector("script,img"), null);
  assert.ok(div.querySelector("strong"));
  assert.ok(div.querySelector("pre code"));
  assert.equal(C.safeURL("javascript:alert(1)"), "");
  assert.equal(C.safeURL("data:text/html,x"), "");
  assert.equal(C.safeURL("file:///tmp/test"), "");
  assert.equal(C.healthClass("BUSY", true), "ok");
  assert.throws(() => C.zonedISO("2030-03-31T02:30", "Europe/Berlin"));
});
test("direct record URL restores list and selected task", async (t) => {
  const { d } = setup(t, createAPI(), "#tab=tasks&kind=task&id=task2");
  await settle();
  assert.match(d.querySelector("#detail h2").textContent, /оценку сроков/);
  assert.equal(
    d
      .querySelector('#list [data-open-id="task2"]')
      .getAttribute("aria-pressed"),
    "true",
  );
});

test("websocket invalidation updates list and reading pane, preserving draft and scroll", async (t) => {
  const { w, d, api, sockets } = setup(t);
  await settle();
  assert.match(sockets[0].url, /^ws:\/\/localhost\/api\/v1\/realtime\?status=1$/);
  const socket = sockets[0];
  socket.open();
  click(d, '[data-open-id="task1"]');
  await settle();
  d.querySelector("#list-scroll").scrollTop = 140;
  api.data.tasks[0].title = "Название обновлено другим клиентом";
  socket.message({ type: "changed", topics: ["tasks"] });
  socket.message({ type: "ping" });
  assert.equal(socket.sent, "pong");
  await new Promise((r) => setTimeout(r, 350));
  await settle();
  assert.match(
    d.querySelector("#detail").textContent,
    /Название обновлено другим клиентом/,
  );
  assert.match(
    d.querySelector("#list").textContent,
    /Название обновлено другим клиентом/,
  );
  assert.equal(d.querySelector("#list-scroll").scrollTop, 140);
  click(d, "#chat-toggle");
  await settle();
  const input = d.querySelector("#message");
  input.value = "Неотправленный вопрос";
  api.data.chat[0].answer = "**Новый ответ**";
  socket.message({ type: "changed", topics: ["chat"] });
  await new Promise((r) => setTimeout(r, 350));
  await settle();
  assert.equal(input.value, "Неотправленный вопрос");
  assert.match(d.querySelector("#chat-pane").textContent, /Новый ответ/);
});

test("websocket closes in hidden tab; return reconnects and resyncs missed data", async (t) => {
  const { w, d, api, sockets } = setup(t);
  await settle();
  sockets[0].open();
  Object.defineProperty(d, "hidden", { value: true, configurable: true });
  d.dispatchEvent(new w.Event("visibilitychange"));
  assert.equal(sockets[0].readyState, 3);
  api.data.tasks[0].title = "Изменено пока вкладка скрыта";
  Object.defineProperty(d, "hidden", { value: false, configurable: true });
  d.dispatchEvent(new w.Event("visibilitychange"));
  assert.equal(sockets.length, 2);
  sockets[1].open();
  sockets[1].message({ type: "changed", topics: ["all"] });
  await new Promise((r) => setTimeout(r, 350));
  await settle();
  assert.match(
    d.querySelector("#list").textContent,
    /Изменено пока вкладка скрыта/,
  );
  // A late message from the obsolete connection has no effect.
  api.data.tasks[0].title = "Не должно появиться";
  sockets[0].message({ type: "changed", topics: ["all"] });
  await new Promise((r) => setTimeout(r, 350));
  assert.doesNotMatch(
    d.querySelector("#list").textContent,
    /Не должно появиться/,
  );
});

 test("queue snapshots render rate and progress over WebSocket without status HTTP requests", async (t) => {
  const {d, api, sockets} = setup(t);
  await settle();
  click(d, '[data-tab="status"]');
  await settle();
  const socket=sockets[0];
  const snapshot=structuredClone(api.data.system);
  const processing=snapshot.components.find(c=>c.id==="processing");
  processing.metrics={events_total:100,events_pending:78,events_processing:1,events_completed:20,events_excluded:1,events_failed:0,events_retry_waiting:2,events_completed_last_15m:15,events_rate_per_minute:1};
  socket.message({type:"status",data:snapshot});
  assert.equal(d.querySelector(".queue-waiting strong").textContent,"78");
  assert.equal(d.querySelector(".queue-rate strong").textContent,"1");
  assert.equal(d.querySelector(".queue-progress").value,21);
  assert.match(d.querySelector(".queue-panel").textContent,/В реальном времени/);
  d.querySelector("#detail").scrollTop=90;
  processing.metrics.events_pending=77;
  processing.metrics.events_completed=21;
  socket.message({type:"status",data:snapshot});
  assert.equal(d.querySelector(".queue-waiting strong").textContent,"77");
  assert.equal(d.querySelector("#detail").scrollTop,90);
  socket.close();
  assert.match(d.querySelector(".queue-panel").textContent,/данные устарели/);
  assert.equal(d.querySelector(".queue-waiting strong").textContent,"77");
  assert.ok(!api.calls.some(c=>c.path==="/system/status"));
 });
 test("empty queue and zero rate never show invalid progress", async(t)=>{
  const {d, api, sockets}=setup(t);
  await settle(); click(d,'[data-tab="status"]'); await settle();
  const snapshot=structuredClone(api.data.system);
  snapshot.components.find(c=>c.id==="processing").metrics={events_total:0,events_rate_per_minute:0};
  sockets[0].message({type:"status",data:snapshot});
  assert.match(d.querySelector(".queue-panel").textContent,/Очередь пуста/);
  assert.match(d.querySelector(".queue-panel").textContent,/завершений не было/);
  assert.equal(d.querySelector(".queue-progress").value,0);
  assert.doesNotMatch(d.querySelector(".queue-panel").textContent,/NaN|Infinity/);
 });

test("failed event displays analysis failure reason as text", async (t) => {
  const api = createAPI();
  api.data.source.analysis_state = "FAILED";
  api.data.source.analysis_error = "Анализ не будет выполнен: лимит токенов <script>unsafe()</script>";
  const { d } = setup(t, api, "#tab=threads&kind=event&id=event1");
  await settle();
  const error = d.querySelector("#detail .inline-error");
  assert.ok(error);
  assert.equal(error.textContent, api.data.source.analysis_error);
  assert.equal(error.querySelector("script"), null);
});
