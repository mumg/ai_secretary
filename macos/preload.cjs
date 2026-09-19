// Sandboxed preload: only the existing MTS SSO message contract, no general IPC,
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
