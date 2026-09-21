// Sandboxed preload: narrowly scoped native message contracts, no general IPC,
// filesystem, command execution or Node objects are exposed to the web page.
const { ipcRenderer } = require('electron');
const channel = 'secretary:mts-sso';
const webChannel = 'improver-mts-sso-v1';
if (window === window.top && ['/admin', '/admin/'].includes(location.pathname) &&
    location.protocol === 'http:' && location.hostname === '127.0.0.1') {
  window.addEventListener('message', event => {
    if (event.source !== window || event.origin !== location.origin ||
        event.data?.channel !== webChannel || event.data?.from !== 'admin') return;
    const { action, flowId, authorizationUrl } = event.data;
    if (['ping', 'start', 'cancel'].includes(action)) ipcRenderer.send(channel, { action, flowId, authorizationUrl });
  });
  ipcRenderer.on(channel, (_event, message) => {
    window.postMessage({ ...message, channel: webChannel, from: 'extension', transport: 'desktop' }, location.origin);
  });
}

// Only a validated language enum crosses this channel; the main process also
// checks the exact renderer and main frame before reading/writing preferences.
if (window === window.top && ['/app/', '/app', '/admin', '/admin/'].includes(location.pathname) &&
    location.protocol === 'http:' && location.hostname === '127.0.0.1') {
  const choice = ipcRenderer.sendSync('secretary:language');
  if (['system', 'ru', 'en', 'zh'].includes(choice)) localStorage.setItem('secretary.language', choice);
  window.addEventListener('secretary-language', event => {
    if (['system', 'ru', 'en', 'zh'].includes(event.detail)) ipcRenderer.sendSync('secretary:language', event.detail);
  });
}

// Fixed operations only; no command, URL, filesystem path or privilege option
// can be supplied by a renderer. Main independently checks the exact frame.
if (window === window.top && ['/admin', '/admin/'].includes(location.pathname) &&
    location.protocol === 'http:' && location.hostname === '127.0.0.1') {
  const ollamaChannel = 'improver-local-ollama-v1';
  window.addEventListener('message', event => {
    if (event.source !== window || event.origin !== location.origin || event.data?.channel !== ollamaChannel || event.data?.from !== 'admin') return;
    const { action, model, contextLength } = event.data;
    if (['status', 'runtime', 'inspect', 'setup', 'install', 'start', 'pull', 'cancel'].includes(action))
      ipcRenderer.send('secretary:ollama', { action, model, contextLength });
  });
  ipcRenderer.on('secretary:ollama', (_event, state) => {
    window.postMessage({ channel: ollamaChannel, from: 'desktop', state }, location.origin);
  });
}
