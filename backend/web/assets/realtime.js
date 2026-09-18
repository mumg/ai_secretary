/* Entity invalidations and complete status snapshots over the same socket. */
(() => {
  "use strict";
  window.SecretaryRealtime = function (onChanged, onStatus, onConnection = () => {}) {
    let socket = null,
      retry = null,
      attempt = 0,
      lastMessage = 0,
      lastStatus = 0,
      stopped = false;
    function disconnect() {
      clearTimeout(retry);
      retry = null;
      const old = socket;
      socket = null;
      old?.close();
      onConnection(false);
    }
    function reconnect() {
      if (stopped || document.hidden || retry || socket) return;
      retry = setTimeout(
        () => {
          retry = null;
          connect();
        },
        Math.min(30000, 1000 * 2 ** Math.min(attempt++, 5)) *
          (0.8 + Math.random() * 0.4),
      );
    }
    function connect() {
      if (stopped || document.hidden || socket || !window.WebSocket) return;
      const url = new URL("/api/v1/realtime", location.href);
      if (onStatus) url.searchParams.set("status", "1");
      url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      let current;
      try {
        current = new WebSocket(url);
      } catch {
        reconnect();
        return;
      }
      socket = current;
      lastMessage = Date.now();
      lastStatus = Date.now();
      current.onmessage = (event) => {
        if (socket !== current) return;
        lastMessage = Date.now();
        try {
          const message = JSON.parse(event.data);
          if (message.type === "status" && onStatus && Array.isArray(message.data?.components)) {
            attempt = 0;
            lastStatus = Date.now();
            onStatus(message.data);
            onConnection(true);
          }
          if (message.type === "ping") current.send("pong");
          if (message.type === "changed" && Array.isArray(message.topics)) {
            attempt = 0;
            onChanged(message.topics);
          }
        } catch {
          /* Invalid frames never execute page code. */
        }
      };
      current.onclose = () => {
        if (socket !== current) return;
        socket = null;
        onConnection(false);
        reconnect();
      };
      current.onerror = () => current.close();
    }
    function visibility() {
      if (document.hidden) disconnect();
      else {
        connect();
        onChanged(["all"]);
      }
    }
    function online() {
      disconnect();
      attempt = 0;
      connect();
    }
    const watchdog = setInterval(() => {
      if (socket && (Date.now() - lastMessage > 60000 || (onStatus && Date.now() - lastStatus > 20000))) {
        disconnect();
        reconnect();
      }
    }, 10000);
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("online", online);
    window.addEventListener("offline", disconnect);
    window.addEventListener("pagehide", disconnect);
    window.addEventListener("pageshow", visibility);
    connect();
    return {
      get connected() {
        return socket?.readyState === 1;
      },
      stop() {
        stopped = true;
        disconnect();
        clearInterval(watchdog);
        document.removeEventListener("visibilitychange", visibility);
        window.removeEventListener("online", online);
        window.removeEventListener("offline", disconnect);
        window.removeEventListener("pagehide", disconnect);
        window.removeEventListener("pageshow", visibility);
      },
    };
  };
})();
