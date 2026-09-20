'use strict';

const electron = require('electron');
const { app, BrowserWindow, Menu, dialog, shell, session } = electron;
const path = require('node:path');
const fs = require('node:fs/promises');
const os = require('node:os');
const { Services } = require('./services.cjs');
const { WindowsServices } = require('./windows-services.cjs');
const { MtsAuth } = require('./mts-auth.cjs');
const isWindows = process.platform === 'win32';

let window, services, origin, busy = false;
const splash = path.join(__dirname, 'splash.html');
// CI uses a separate userData directory, data root and launchd labels. This is
// only enabled by an explicit command-line switch, never by renderer content.
const smoke = process.argv.includes('--secretary-smoke-test');
const smokeRoot = process.env.AI_SECRETARY_SMOKE_ROOT;
if (smoke) {
  const validRoot = smokeRoot && (isWindows
    ? path.dirname(smokeRoot) === os.tmpdir() && path.basename(smokeRoot).startsWith('AI Secretary smoke ')
    : path.dirname(smokeRoot) === path.join(os.homedir(), 'Library/Application Support') &&
      path.basename(smokeRoot).startsWith('AI Secretary smoke ') &&
      /^net\.muratov\.secretary\.smoke\.\d+$/.test(process.env.AI_SECRETARY_SMOKE_LABEL || ''));
  if (!validRoot)
    throw Error('Invalid isolated smoke-test configuration');
  app.setPath('userData', path.join(smokeRoot, 'electron'));
}
function internal(url) {
  try { return origin && new URL(url).origin === origin; } catch { return false; }
}
function external(url) {
  try { if (['https:', 'http:', 'mailto:'].includes(new URL(url).protocol)) void shell.openExternal(url); } catch { /* Invalid link. */ }
}
async function status(text) {
  if (!window || window.isDestroyed() || !window.webContents.getURL().startsWith('file:')) return;
  await window.webContents.executeJavaScript(`document.getElementById('status').textContent = ${JSON.stringify(text)}`).catch(() => {});
}
function createWindow() {
  window = new BrowserWindow({ width: 1320, height: 900, minWidth: 800, minHeight: 600,
    title: 'AI Секретарь', show: false, backgroundColor: '#f5f7fa',
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false,
      contextIsolation: true, sandbox: true, webviewTag: false, webSecurity: true }
  });
  window.once('ready-to-show', () => window.show());
  window.on('closed', () => { window = null; });
  window.webContents.setWindowOpenHandler(({ url }) => {
    if (internal(url)) void window.loadURL(url); else external(url);
    return { action: 'deny' };
  });
  window.webContents.on('will-navigate', (event, url) => {
    if (!internal(url)) { event.preventDefault(); external(url); }
  });
  window.webContents.on('will-redirect', (event, url) => {
    if (!internal(url)) event.preventDefault();
  });
  window.webContents.on('will-attach-webview', event => event.preventDefault());
  new MtsAuth(electron, window, () => origin);
  return window.loadFile(splash);
}
async function start() {
  if (busy) return;
  busy = true;
  try {
    if (!window) await createWindow(); else await window.loadFile(splash);
    origin = await services.ensure();
    if (window && !window.isDestroyed()) {
      await window.loadURL(`${origin}${process.argv.includes('--settings') && !smoke ? '/admin' : '/app/'}`);
      if (smoke) {
        // Wait for a real API-backed render, not just the initial HTML skeleton.
        await window.webContents.executeJavaScript(`new Promise((resolve, reject) => {
          let attempts = 0;
          const check = () => {
            if (document.getElementById('updated')?.textContent.startsWith('Обновлено') &&
                document.getElementById('list-error')?.hidden) return resolve(true);
            if (++attempts > 60) return reject(new Error('Application data did not load'));
            setTimeout(check, 500);
          };
          check();
        })`);
        const state = await window.webContents.executeJavaScript(`({ title: document.title,
          tabs: document.querySelectorAll('#tabs button').length, node: typeof process, require: typeof require })`);
        if (state.tabs !== 6 || state.node !== 'undefined' || state.require !== 'undefined') throw Error('Renderer smoke check failed');
        await fs.writeFile(path.join(smokeRoot, 'electron-app.png'), (await window.webContents.capturePage()).toPNG());
        await window.loadURL(`${origin}/admin`);
        state.settings = await window.webContents.executeJavaScript('document.querySelectorAll("input").length');
        if (!state.settings) throw Error('Settings page did not load');
        state.nativeSSO = await window.webContents.executeJavaScript(`new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('Native SSO bridge unavailable')), 5000);
          const listener = event => {
            if (event.source === window && event.origin === location.origin &&
                event.data?.channel === 'improver-mts-sso-v1' && event.data?.action === 'ready' &&
                event.data?.transport === 'desktop') {
              clearTimeout(timer); window.removeEventListener('message', listener); resolve(true);
            }
          };
          window.addEventListener('message', listener);
          window.postMessage({ channel: 'improver-mts-sso-v1', from: 'admin', action: 'ping' }, location.origin);
        })`);
        await fs.writeFile(path.join(smokeRoot, 'electron-smoke.json'), JSON.stringify(state));
        app.exit(0); return;
      }
    }
  } catch (error) {
    if (smoke) { console.error(error); app.exit(1); return; }
    await status('Не удалось запустить сервер. Откройте журналы или перезапустите приложение.');
    const result = await dialog.showMessageBox({ type: 'error', title: 'AI Секретарь', message: 'Не удалось запустить службы',
      detail: error.message, buttons: ['Закрыть', 'Открыть журналы'], defaultId: 0 });
    if (result.response === 1) await shell.openPath(path.join(services.data, 'logs'));
  } finally { busy = false; }
}
async function stopAndQuit(remove = false) {
  if (busy) return;
  const result = await dialog.showMessageBox({ type: 'question', message: remove ? 'Удалить фоновые службы?' : 'Остановить сервер и закрыть приложение?',
    detail: remove ? 'Данные и резервные копии сохранятся. Следующий запуск приложения восстановит службы.' :
      'Обработка данных и удалённое подключение остановятся. Службы запустятся при следующем входе в macOS или запуске приложения.',
    buttons: ['Отмена', remove ? 'Удалить службы' : 'Остановить'], cancelId: 0, defaultId: 0 });
  if (result.response !== 1) return;
  busy = true;
  try {
    if (remove) await services.uninstall(); else await services.lock(() => services.stopAll());
    app.quit();
  } catch (error) { dialog.showErrorBox('Не удалось остановить службы', error.message); }
  finally { busy = false; }
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', (_event, argv) => {
    if (!isWindows && (argv.includes('--stop-services') || argv.includes('--remove-services'))) {
      void stopAndQuit(argv.includes('--remove-services')); return;
    }
    if (window) { window.restore(); window.focus(); if (origin && argv.includes('--settings')) void window.loadURL(`${origin}/admin`); }
    else void start();
  });
  app.on('window-all-closed', () => app.quit());
  app.whenReady().then(async () => {
    if (!isWindows && app.isPackaged && process.execPath.startsWith('/Volumes/')) {
      dialog.showErrorBox('Установка AI Секретаря', 'Перетащите AI Secretary в папку «Программы», затем запустите его оттуда.');
      app.quit(); return;
    }
    const canWriteClipboard = (contents, permission, requestingURL) =>
      permission === 'clipboard-sanitized-write' && contents === window?.webContents &&
      internal(contents.getURL()) && internal(requestingURL);
    session.defaultSession.setPermissionRequestHandler((contents, permission, callback, details) =>
      callback(canWriteClipboard(contents, permission, details.requestingUrl)));
    session.defaultSession.setPermissionCheckHandler((contents, permission, requestingOrigin) =>
      canWriteClipboard(contents, permission, requestingOrigin));
    services = isWindows ? new WindowsServices({ progress: text => void status(text) }) :
      new Services({ payload: app.isPackaged ? path.join(process.resourcesPath, 'server') : path.join(__dirname, '../dist/macos/payload'),
        ...(smoke ? { data: smokeRoot, label: process.env.AI_SECRETARY_SMOKE_LABEL } : {}), progress: text => void status(text) });
    Menu.setApplicationMenu(null);
    if (process.platform === 'darwin') {
      // macOS normally dispatches these shortcuts through the application menu.
      app.on('web-contents-created', (_event, contents) => contents.on('before-input-event', (event, input) => {
        if (input.type !== 'keyDown' || !input.meta || input.control || input.alt) return;
        const edit = { KeyA: 'selectAll', KeyC: 'copy', KeyX: 'cut',
          KeyV: input.shift ? 'pasteAndMatchStyle' : 'paste', KeyZ: input.shift ? 'redo' : 'undo' }[input.code];
        if (edit) { event.preventDefault(); contents[edit](); }
        else if (input.code === 'KeyQ') { event.preventDefault(); app.quit(); }
        else if (input.code === 'KeyW') { event.preventDefault(); BrowserWindow.fromWebContents(contents)?.close(); }
      }));
    }
    if (!isWindows && (process.argv.includes('--stop-services') || process.argv.includes('--remove-services'))) {
      await stopAndQuit(process.argv.includes('--remove-services'));
      app.quit(); return;
    }
    await start();
  }).catch(error => { dialog.showErrorBox('AI Секретарь', error.message); app.quit(); });
}
