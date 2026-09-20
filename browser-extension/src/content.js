(async () => {
  await SecretaryLanguageReady;
  const channel = "improver-mts-sso-v1";
  const notify = (message) => window.postMessage({ channel, from: "extension", ...message }, location.origin);
  const check = async () => {
    try {
      const response = await chrome.runtime.sendMessage({ action: "ping" });
      notify({ action: response?.ok ? "ready" : "unavailable" });
    } catch { notify({ action: "unavailable" }); }
  };
  if (globalThis.__improverMtsBridge) {
    void check();
    return;
  }
  globalThis.__improverMtsBridge = true;
  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== location.origin ||
        event.data?.channel !== channel || event.data?.from !== "admin") return;
    const { action, flowId, authorizationUrl } = event.data;
    if (action === "ping") return void check();
    if (!["start", "cancel"].includes(action)) return;
    chrome.runtime.sendMessage({ action, flowId, authorizationUrl }).catch(() => {
      notify({ action: "error", flowId, message: tr("Расширение отключено. Нажмите его значок в админке.") });
    });
  });
  chrome.runtime.onMessage.addListener((message) => {
    if (message.channel === channel) notify(message);
  });
  void check();
})();
