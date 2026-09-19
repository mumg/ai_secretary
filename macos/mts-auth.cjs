'use strict';

const crypto = require('node:crypto');
const CHANNEL = 'secretary:mts-sso';
const GATEWAY = 'https://gw.mts-link.ru';

function loginURL(raw) {
  if (typeof raw !== 'string' || raw.length > 65536) throw Error('Invalid login URL');
  const url = new URL(raw);
  if (url.origin !== GATEWAY || url.username || url.password || url.hash ||
      !['/sso/saml/login', '/sso/oauth/login'].includes(url.pathname) ||
      url.searchParams.getAll('returnUrl').length !== 1 ||
      url.searchParams.get('returnUrl') !== 'mtslink://mobile/login') throw Error('Invalid login URL');
  return url.href;
}
function callbackCode(raw) {
  try {
    const url = new URL(raw);
    if (url.protocol !== 'mtslink:' || url.host !== 'mobile' || url.pathname !== '/login' ||
        url.username || url.password || url.hash || url.searchParams.getAll('authCode').length !== 1) return null;
    const code = url.searchParams.get('authCode');
    return code && code.length <= 32768 && /^[\x21-\x7e]+$/.test(code) ? code : null;
  } catch { return null; }
}
function isGateway(raw) { try { return new URL(raw).origin === GATEWAY; } catch { return false; } }
function isAdmin(raw, origin) {
  try { const url = new URL(raw); return url.origin === origin && ['/admin', '/admin/'].includes(url.pathname); }
  catch { return false; }
}

// The Electron dependency is injected so the security/lifecycle contract can be
// tested without network access, an IdP account or real authentication codes.
class MtsAuth {
  constructor({ BrowserWindow, session, ipcMain }, owner, getOrigin) {
    this.BrowserWindow = BrowserWindow; this.session = session;
    this.owner = owner; this.getOrigin = getOrigin; this.flow = null;
    this.receive = (event, message) => this.handle(event, message);
    ipcMain.on(CHANNEL, this.receive);
    const cancelOnNavigate = (_event, url, _inPlace, mainFrame) => {
      if (mainFrame !== false && !_inPlace) void this.finish({ action: 'cancelled' });
    };
    owner.webContents.on('did-start-navigation', cancelOnNavigate);
    owner.on('closed', () => {
      ipcMain.removeListener(CHANNEL, this.receive);
      void this.finish({ action: 'cancelled' });
    });
  }
  allowed(event) {
    return !this.owner.isDestroyed() && event.sender === this.owner.webContents &&
      event.senderFrame === this.owner.webContents.mainFrame &&
      isAdmin(event.senderFrame.url, this.getOrigin());
  }
  send(message) {
    if (!this.owner.isDestroyed() && isAdmin(this.owner.webContents.getURL(), this.getOrigin()))
      this.owner.webContents.send(CHANNEL, { ...message, transport: 'desktop' });
  }
  handle(event, message) {
    if (!this.allowed(event) || !message || typeof message !== 'object') return;
    if (message.action === 'ping') { this.send({ action: 'ready' }); return; }
    if (!/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(message.flowId || '')) return;
    if (message.action === 'cancel') {
      if (this.flow?.id === message.flowId) void this.finish({ action: 'cancelled' });
      return;
    }
    if (message.action !== 'start') return;
    let url;
    try { url = loginURL(message.authorizationUrl); }
    catch { this.send({ action: 'error', flowId: message.flowId, message: 'Недопустимый адрес входа МТС Линк.' }); return; }
    // One active flow per admin window. Finishing immediately clears the active
    // slot; cleanup of the old isolated session may continue asynchronously.
    void this.finish({ action: 'cancelled' });
    const partition = this.session.fromPartition(`mts-sso-${crypto.randomUUID()}`, { cache: false });
    partition.setPermissionRequestHandler((_web, _permission, callback) => callback(false));
    partition.setPermissionCheckHandler(() => false);
    const popup = new this.BrowserWindow({ width: 650, height: 830, parent: this.owner, title: 'Вход в МТС Линк',
      autoHideMenuBar: true, webPreferences: { session: partition, sandbox: true, contextIsolation: true,
        nodeIntegration: false, webviewTag: false, webSecurity: true, safeDialogs: true } });
    popup.removeMenu();
    const flow = { id: message.flowId, popup, partition, timer: null };
    this.flow = flow;
    const end = result => { if (this.flow === flow) void this.finish(result); };
    flow.timer = setTimeout(() => end({ action: 'error', message: 'Время входа истекло. Начните вход заново.' }), 15 * 60000);
    // Capture the HTTPS Location header before Chromium can launch the native
    // MTS application. Do not register/replace the system-wide mtslink handler.
    partition.webRequest.onHeadersReceived({ urls: [`${GATEWAY}/sso/*`] }, (details, callback) => {
      const locations = Object.entries(details.responseHeaders || {}).find(([name]) => name.toLowerCase() === 'location')?.[1];
      const code = Array.isArray(locations) && locations.length === 1 ? callbackCode(locations[0]) : null;
      if (isGateway(details.url) && details.statusCode >= 300 && details.statusCode < 400 && code && this.flow === flow) {
        callback({ cancel: true });
        end({ action: 'complete', authCode: code });
      } else callback({ cancel: false });
    });
    const navigate = (event, target) => {
      let url;
      try { url = new URL(target); } catch { event.preventDefault(); return; }
      if (url.protocol === 'https:' && !url.username && !url.password) return;
      event.preventDefault();
      const code = callbackCode(target);
      if (code && isGateway(popup.webContents.getURL())) end({ action: 'complete', authCode: code });
    };
    popup.webContents.on('will-navigate', navigate);
    popup.webContents.on('will-redirect', navigate);
    popup.webContents.on('will-attach-webview', event => event.preventDefault());
    popup.webContents.setWindowOpenHandler(({ url }) => {
      // Corporate IdPs sometimes open the next step in a new tab. Keep it in
      // the same sandbox/session; never pass these URLs to shell.openExternal.
      try { const u = new URL(url); if (u.protocol === 'https:' && !u.username && !u.password) void popup.loadURL(u.href).catch(() => {}); } catch { /* reject */ }
      return { action: 'deny' };
    });
    popup.webContents.on('did-navigate', (_event, target) => {
      try { popup.setTitle(`Вход в МТС Линк — ${new URL(target).hostname}`); } catch { /* no URLs/tokens in logs */ }
    });
    popup.on('closed', () => end({ action: 'cancelled' }));
    this.send({ action: 'opened', flowId: flow.id });
    void popup.loadURL(url).catch(() => {
      // Redirect cancellation also rejects loadURL; a completed flow is already gone.
      end({ action: 'error', message: 'Не удалось открыть страницу входа. Проверьте подключение и повторите попытку.' });
    });
  }
  async finish(message) {
    const flow = this.flow;
    if (!flow) return;
    this.flow = null;
    clearTimeout(flow.timer);
    flow.partition.webRequest.onHeadersReceived(null);
    if (!flow.popup.isDestroyed()) flow.popup.destroy();
    this.send({ ...message, flowId: flow.id });
    await flow.partition.closeAllConnections().catch(() => {});
    await flow.partition.clearAuthCache().catch(() => {});
    await flow.partition.clearStorageData().catch(() => {});
    await flow.partition.clearCache().catch(() => {});
  }
}

module.exports = { MtsAuth, CHANNEL, loginURL, callbackCode, isAdmin };
