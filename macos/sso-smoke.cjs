// Real Electron IPC/preload + HTTPS redirect check, with a local synthetic IdP.
// Run with Electron, not Node. No credentials or requests to the real gateway.
'use strict';
const electron = require('electron');
const { app, BrowserWindow, session } = electron;
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const https = require('node:https');
const http = require('node:http');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const { MtsAuth } = require('./mts-auth.cjs');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'secretary-sso-test-'));
app.setPath('userData', path.join(directory, 'electron'));
app.commandLine.appendSwitch('host-resolver-rules', 'MAP gw.mts-link.ru:443 127.0.0.1:48199');
let web, idp, owner, auth;
const deadline = setTimeout(() => { console.error('SSO smoke timed out'); app.exit(1); }, 30000);
const listen = server => new Promise((resolve, reject) => {
  server.once('error', reject);
  server.listen(server === idp ? 48199 : 0, '127.0.0.1', resolve);
});
(async () => {
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
    '-subj', '/CN=gw.mts-link.ru', '-keyout', path.join(directory, 'key'),
    '-out', path.join(directory, 'cert')], { stdio: 'ignore' });
  const cert = fs.readFileSync(path.join(directory, 'cert'));
  const fingerprint = new crypto.X509Certificate(cert).fingerprint256;
  let requests = 0;
  idp = https.createServer({ key: fs.readFileSync(path.join(directory, 'key')), cert }, (req, res) => {
    assert.equal(req.headers.host, 'gw.mts-link.ru');
    requests++;
    res.writeHead(302, { Location: 'mtslink://mobile/login?authCode=synthetic-test-code' });
    res.end();
  });
  web = http.createServer((_req, res) => res.end('<!doctype html><title>Local admin test</title>'));
  await Promise.all([listen(idp), listen(web), app.whenReady()]);
  const origin = `http://127.0.0.1:${web.address().port}`;
  owner = new BrowserWindow({ show: false, webPreferences: {
    preload: path.join(__dirname, 'preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false,
  } });
  const partitions = [];
  auth = new MtsAuth({ ...electron, session: { fromPartition(name, options) {
    const partition = session.fromPartition(name, options);
    partition.setCertificateVerifyProc(({ hostname, certificate }, callback) =>
      callback(hostname === 'gw.mts-link.ru' && new crypto.X509Certificate(certificate.data).fingerprint256 === fingerprint ? 0 : -3));
    partitions.push(partition);
    return partition;
  } }, BrowserWindow: class extends BrowserWindow {
    constructor(options) { super({ ...options, show: false }); }
    async loadURL(url) { await this.webContents.session.setProxy({ mode: 'direct' }); return super.loadURL(url); }
  } }, owner, () => origin);
  await owner.loadURL(`${origin}/admin`);
  const result = await owner.webContents.executeJavaScript(`new Promise(resolve => {
    window.addEventListener('message', event => {
      if (event.data?.from === 'extension' && ['complete', 'error'].includes(event.data.action)) resolve(event.data);
    });
    window.postMessage({ channel: 'improver-mts-sso-v1', from: 'admin', action: 'start',
      flowId: '11111111-2222-3333-4444-555555555555',
      authorizationUrl: 'https://gw.mts-link.ru/sso/saml/login?returnUrl=mtslink%3A%2F%2Fmobile%2Flogin'
    }, location.origin);
  })`);
  assert.equal(result.action, 'complete');
  assert.equal(result.authCode, 'synthetic-test-code');
  assert.equal(result.transport, 'desktop');
  assert.equal(requests, 1);
  assert.equal(auth.flow, null);
  assert.equal(partitions.length, 1);
  assert.equal(partitions[0].getStoragePath(), null);
  console.log('PASS: sandboxed preload, IPC, isolated HTTPS login and captured redirect in real Electron');
})().then(() => finish(0), error => { console.error(error); void finish(1); });
async function finish(code) {
  clearTimeout(deadline);
  await auth?.finish({ action: 'cancelled' });
  owner?.destroy();
  web?.close(); idp?.close();
  // Chromium may still hold userData files on Windows; OS temporary-directory
  // cleanup can remove them later if necessary.
  try { fs.rmSync(directory, { recursive: true, force: true }); } catch { /* temp only */ }
  app.exit(code);
}
