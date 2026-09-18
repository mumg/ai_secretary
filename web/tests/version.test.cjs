const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const root = path.resolve(__dirname, "../../backend/web");
const settle = () => new Promise(resolve => setTimeout(resolve, 15));

for (const shell of ["index.html", "app.html"]) {
  test(`${shell}: release notice updates, renders safe text and survives offline`, async t => {
    const dom = new JSDOM(fs.readFileSync(path.join(root, shell), "utf8"), {
      url: "http://localhost/admin", runScripts: "outside-only",
    });
    t.after(() => dom.window.close());
    const w = dom.window;
    let refresh;
    w.setInterval = callback => { refresh = callback; };
    Object.defineProperty(w.document, "hidden", { value: false });
    let data = { current_version: "0.1.0", latest_version: "0.1.0", update_available: false,
      checked_at: "2026-09-16T10:00:00Z", last_success_at: "2026-09-16T10:00:00Z" };
    let offline = false;
    const requests = [];
    w.fetch = async (url, options) => {
      requests.push([url, options.method]);
      if (offline) throw new Error("offline");
      return { ok: true, json: async () => structuredClone(data) };
    };
    w.eval(fs.readFileSync(path.join(root, "assets/version.js"), "utf8"));
    await settle();
    const el = id => w.document.getElementById(id);
    assert.equal(el("version-notice").hidden, true);
    data.latest_version = "0.2.0";
    data.update_available = true;
    await refresh();
    await settle();
    assert.equal(el("version-notice").hidden, false);
    assert.match(el("version-notice-text").textContent, /0\.2\.0.*0\.1\.0/);
    if (shell === "index.html") {
      el("version-check").click();
      await settle();
      assert.deepEqual(requests.at(-1), ["/api/v1/system/version/check", "POST"]);
      data.error = "Файл version ещё не опубликован";
      data.latest_version = '<img src="x" onerror="alert(1)">';
      await refresh();
      await settle();
      assert.match(el("version-details").textContent, /не опубликован/);
      assert.equal(el("version-notice-text").querySelector("img"), null);
    }
    offline = true;
    await refresh();
    await settle();
    assert.equal(el("version-notice").hidden, false);
    if (shell === "index.html") assert.equal(el("version-check").disabled, false);
    assert.ok(requests.every(([url]) => url.startsWith("/api/v1/system/version")));
  });
}
