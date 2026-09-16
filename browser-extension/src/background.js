const CHANNEL = "improver-mts-sso-v1";
const PREFIX = "mts-flow-";
const finishing = new Set();
const pending = new Set();

function adminOrigin(raw) {
  try {
    const url = new URL(raw);
    if (!["/admin", "/admin/"].includes(url.pathname) || url.username || url.password) return null;
    if (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1"].includes(url.hostname))) return null;
    return url.origin;
  } catch { return null; }
}
function loginUrl(raw) {
  const url = new URL(raw);
  if (url.origin !== "https://gw.mts-link.ru" || url.username || url.password ||
      !["/sso/saml/login", "/sso/oauth/login"].includes(url.pathname) ||
      url.searchParams.get("returnUrl") !== "mtslink://mobile/login") throw new Error("Invalid URL");
  return url.href;
}
function callbackCode(raw) {
  try {
    const url = new URL(raw);
    if (url.protocol !== "mtslink:" || url.hostname !== "mobile" || url.pathname !== "/login" ||
        url.username || url.password || url.searchParams.getAll("authCode").length !== 1) return null;
    const code = url.searchParams.get("authCode");
    return code && code.length <= 32768 && /^[\x21-\x7e]+$/.test(code) ? code : null;
  } catch { return null; }
}
async function send(flow, message) {
  try {
    const tab = await chrome.tabs.get(flow.adminTab);
    if (adminOrigin(tab.url) !== flow.origin) return;
    await chrome.tabs.sendMessage(flow.adminTab, { channel: CHANNEL, flowId: flow.id, ...message }, { frameId: 0 });
  } catch { /* The originating admin tab may have closed. */ }
}
async function end(tabId, message) {
  if (finishing.has(tabId)) return;
  finishing.add(tabId);
  try {
    const key = PREFIX + tabId;
    const flow = (await chrome.storage.session.get(key))[key];
    if (!flow) return;
    await chrome.storage.session.remove(key);
    await chrome.alarms.clear(key);
    await chrome.debugger.detach({ tabId }).catch(() => {});
    await chrome.tabs.remove(tabId).catch(() => {});
    await send(flow, message);
  } finally { finishing.delete(tabId); }
}
chrome.action.onClicked.addListener(async (tab) => {
  const origin = adminOrigin(tab.url);
  if (!origin || !tab.id) {
    await chrome.action.setBadgeText({ tabId: tab.id, text: "!" });
    return;
  }
  await chrome.storage.session.set({ ["admin-" + tab.id]: origin });
  await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
  await chrome.action.setBadgeText({ tabId: tab.id, text: "SSO" });
});
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message.action === "ping") {
    (async () => {
      const origin = adminOrigin(sender.url);
      return Boolean(sender.tab && sender.frameId === 0 && origin &&
        (await chrome.storage.session.get("admin-" + sender.tab.id))["admin-" + sender.tab.id] === origin);
    })().then(ok => respond({ ok }), () => respond({ ok: false }));
    return true;
  }
  (async () => {
    const origin = adminOrigin(sender.url);
    if (!sender.tab || sender.frameId !== 0 || !origin ||
        (await chrome.storage.session.get("admin-" + sender.tab.id))["admin-" + sender.tab.id] !== origin ||
        !/^[a-f0-9-]{36}$/.test(message.flowId || "")) return;
    const states = await chrome.storage.session.get(null);
    if (message.action === "cancel") {
      for (const [key, flow] of Object.entries(states)) {
        if (key.startsWith(PREFIX) && flow.adminTab === sender.tab.id && flow.id === message.flowId)
          await end(Number(key.slice(PREFIX.length)), { action: "cancelled" });
      }
      return;
    }
    if (message.action !== "start") return;
    const flow = { id: message.flowId, adminTab: sender.tab.id, origin };
    if (pending.has(sender.tab.id)) return;
    pending.add(sender.tab.id);
    let tabId;
    try {
      const url = loginUrl(message.authorizationUrl);
      for (const [key, old] of Object.entries(states)) {
        if (key.startsWith(PREFIX) && old.adminTab === sender.tab.id)
          await end(Number(key.slice(PREFIX.length)), { action: "cancelled" });
      }
      const popup = await chrome.windows.create({ url: "about:blank", type: "popup", width: 620, height: 820 });
      tabId = popup.tabs[0].id;
      await chrome.storage.session.set({ [PREFIX + tabId]: flow });
      await chrome.alarms.create(PREFIX + tabId, { delayInMinutes: 15 });
      await chrome.debugger.attach({ tabId }, "1.3");
      await chrome.debugger.sendCommand({ tabId }, "Fetch.enable", {
        patterns: [{ urlPattern: "https://gw.mts-link.ru/sso/*", requestStage: "Response" }]
      });
      await chrome.tabs.update(tabId, { url });
      await send(flow, { action: "opened" });
    } catch {
      const error = { action: "error", message: "Не удалось открыть окно SSO. Проверьте, что расширению разрешена отладка вкладки." };
      if (tabId) await end(tabId, error); else await send(flow, error);
    } finally { pending.delete(sender.tab.id); }
  })().then(() => respond({ ok: true }), () => respond({ ok: false }));
  return true;
});
chrome.debugger.onEvent.addListener((target, method, params) => {
  if (method !== "Fetch.requestPaused" || !target.tabId) return;
  (async () => {
    const key = PREFIX + target.tabId;
    if (!(await chrome.storage.session.get(key))[key]) return;
    const location = params.responseHeaders?.find(h => h.name.toLowerCase() === "location")?.value;
    const code = callbackCode(location);
    if (code) {
      // Abort before Chrome can hand the URI to an installed application.
      await chrome.debugger.sendCommand(target, "Fetch.failRequest", { requestId: params.requestId, errorReason: "Aborted" });
      await end(target.tabId, { action: "complete", authCode: code });
    } else {
      await chrome.debugger.sendCommand(target, "Fetch.continueRequest", { requestId: params.requestId });
    }
  })().catch(() => end(target.tabId, { action: "error", message: "Не удалось получить результат SSO. Начните вход заново." }));
});
chrome.debugger.onDetach.addListener(target => {
  if (target.tabId) void end(target.tabId, { action: "error", message: "Захват SSO отключён. Начните вход заново." });
});
chrome.tabs.onRemoved.addListener(async tabId => {
  await end(tabId, { action: "cancelled" });
  await chrome.storage.session.remove("admin-" + tabId);
  const states = await chrome.storage.session.get(null);
  for (const [key, flow] of Object.entries(states)) {
    if (key.startsWith(PREFIX) && flow.adminTab === tabId) await end(Number(key.slice(PREFIX.length)), { action: "cancelled" });
  }
});
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name.startsWith(PREFIX)) void end(Number(alarm.name.slice(PREFIX.length)), {
    action: "error", message: "Время входа истекло. Начните SSO-вход заново."
  });
});
