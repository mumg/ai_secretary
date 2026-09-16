(() => {
  "use strict";
  const banner = document.getElementById("version-notice");
  const details = document.getElementById("version-details");
  const button = document.getElementById("version-check");
  let busy = false;
  let loaded = false;

  function render(data) {
    banner.hidden = !data.update_available;
    document.getElementById("version-notice-text").textContent = data.update_available
      ? `Доступна новая версия ${data.latest_version}. Установлена ${data.current_version}.`
      : "";
    if (!details) return;
    const messages = [`Версия исходников: ${data.current_version}.`];
    if (data.error) messages.push(data.error);
    else if (!data.checked_at) messages.push("Проверяем обновления…");
    else if (!data.update_available) messages.push("Новых версий нет.");
    if (data.last_success_at) {
      messages.push(`Последняя успешная проверка: ${new Date(data.last_success_at).toLocaleString("ru-RU")}.`);
    }
    details.textContent = messages.join(" ");
  }

  async function refresh(manual = false) {
    if (busy) return;
    busy = true;
    if (button) button.disabled = true;
    try {
      const response = await fetch(`/api/v1/system/version${manual ? "/check" : ""}`, {
        method: manual ? "POST" : "GET", cache: "no-store",
      });
      if (!response.ok) throw new Error("Version request failed");
      const data = await response.json();
      render(data);
      // Startup runs in the background; fetch its result shortly after first load.
      if (!loaded && !data.checked_at) setTimeout(() => refresh(), 15000);
      loaded = true;
    } catch {
      if (details) details.textContent = "Не удалось получить сведения о версии с сервера. Повторите проверку позже.";
    } finally {
      busy = false;
      if (button) button.disabled = false;
    }
  }

  button?.addEventListener("click", () => refresh(true));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  setInterval(() => { if (!document.hidden) refresh(); }, 5 * 60 * 1000);
  refresh();
})();
