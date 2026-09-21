'use strict';
const { EventEmitter } = require('node:events');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const execute = promisify(execFile);
const { inspectSystem, assess, locations } = require('./ollama-system.cjs');
const release = require('./ollama-release.json');
const endpoint = 'http://127.0.0.1:11434';
const channel = 'secretary:ollama';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

function modelName(value) {
  if (typeof value !== 'string' || value.length > 160 || !/^(?:[a-zA-Z0-9][a-zA-Z0-9_-]*\/)?[a-zA-Z0-9][a-zA-Z0-9._-]*(?::[a-zA-Z0-9][a-zA-Z0-9._-]*)?$/.test(value) || value.includes('..')) throw Error('model_invalid');
  return value.includes(':') ? value : `${value}:latest`;
}
function selection(message) {
  const model = modelName(message.model);
  const contextLength = message.contextLength;
  if (!Number.isInteger(contextLength) || contextLength < 4096 || contextLength > 131072) throw Error('context_invalid');
  return { model, contextLength };
}
async function boundedJSON(response, limit = 1024 * 1024) {
  if (!response.ok) { await response.body?.cancel(); throw Error('network'); }
  let size = 0, parts = [];
  for await (const chunk of response.body) {
    size += chunk.length; if (size > limit) throw Error('network'); parts.push(Buffer.from(chunk));
  }
  return JSON.parse(Buffer.concat(parts).toString('utf8'));
}
async function modelSize(model, fetcher, signal) {
  const [name, tag] = model.split(':');
  const repo = name.includes('/') ? name : `library/${name}`;
  const url = `https://registry.ollama.ai/v2/${repo}/manifests/${encodeURIComponent(tag)}`;
  const data = await boundedJSON(await fetcher(url, { redirect: 'error', signal: AbortSignal.any([signal, AbortSignal.timeout(20000)]) }));
  if (!Array.isArray(data.layers) || !data.layers.length || data.layers.length > 100) throw Error('model_unknown');
  let total = 0;
  for (const layer of data.layers) {
    if (!Number.isSafeInteger(layer.size) || layer.size < 0) throw Error('model_unknown');
    total += layer.size;
  }
  if (!Number.isSafeInteger(total) || total <= 0) throw Error('model_unknown');
  return total;
}
// net.fetch rejects manual redirects instead of exposing their Location header.
// Use Chromium's request API so downloads retain system proxy support and each
// redirect can still be checked by download() before another request is sent.
function downloadFetcher(net) {
  return (url, { signal }) => new Promise((resolve, reject) => {
    signal.throwIfAborted();
    const request = net.request({ url, method: 'GET', redirect: 'manual' });
    const abort = () => { request.abort(); reject(signal.reason); };
    signal.addEventListener('abort', abort, { once: true });
    request.on('close', () => signal.removeEventListener('abort', abort));
    request.on('error', reject);
    request.on('redirect', (status, _method, location) => {
      resolve(new Response(null, { status, headers: { location } }));
      request.abort();
    });
    request.on('response', response => {
      let finished = false;
      const body = new ReadableStream({
        start(controller) {
          const fail = error => { if (!finished) { finished = true; controller.error(error); } };
          response.on('data', chunk => {
            if (finished) return;
            controller.enqueue(new Uint8Array(chunk));
            if (controller.desiredSize <= 0) response.pause();
          });
          response.on('end', () => { if (!finished) { finished = true; controller.close(); } });
          response.on('error', fail);
          request.on('error', fail);
          response.on('aborted', () => fail(new Error('download')));
        },
        pull() { response.resume(); },
        cancel() { finished = true; request.abort(); },
      });
      resolve({ ok: response.statusCode >= 200 && response.statusCode < 300,
        status: response.statusCode, body });
    });
    request.end();
  });
}
async function download(artifact, destination, fetcher, signal, progress) {
  let url = artifact.url, response;
  for (let redirects = 0; redirects <= 5; redirects++) {
    const parsed = new URL(url);
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password || !['github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'].includes(parsed.hostname)) throw Error('download');
    response = await fetcher(url, { redirect: 'manual', signal });
    if (![301, 302, 303, 307, 308].includes(response.status)) break;
    await response.body?.cancel();
    url = new URL(response.headers.get('location'), url).href;
  }
  if (!response?.ok) { await response?.body?.cancel(); throw Error('download'); }
  const handle = await fs.open(destination, 'wx', 0o600), hash = crypto.createHash('sha256');
  let received = 0, last = 0;
  try {
    for await (const chunk of response.body) {
      signal.throwIfAborted(); received += chunk.length;
      if (received > artifact.bytes) throw Error('integrity');
      hash.update(chunk);
      // writeFile handles short writes; the file position advances per chunk.
      await handle.writeFile(chunk);
      if (Date.now() - last > 200) { last = Date.now(); progress(received / artifact.bytes); }
    }
  } finally { await handle.close(); }
  if (received !== artifact.bytes || hash.digest('hex') !== artifact.sha256) throw Error('integrity');
  progress(1);
}
async function installedPath(platform) {
  const candidates = platform === 'darwin' ? ['/Applications/Ollama.app', path.join(os.homedir(), 'Applications/Ollama.app')] :
    [path.join(locations(platform).install, 'ollama app.exe')];
  for (const candidate of candidates) { try { await fs.access(platform === 'darwin' ? path.join(candidate, 'Contents/Resources/ollama') : candidate); return candidate; } catch { /* Not installed here. */ } }
  return null;
}
async function launch(platform, target) {
  if (platform === 'darwin') {
    // A user LaunchAgent avoids the Ollama GUI's system-wide move/CLI prompts.
    const { plist } = require('./services.cjs');
    const label = 'net.muratov.secretary.ollama';
    const agents = path.join(os.homedir(), 'Library/LaunchAgents');
    const logs = path.join(os.homedir(), 'Library/Logs/AI Secretary');
    await fs.mkdir(agents, { recursive: true }); await fs.mkdir(logs, { recursive: true });
    const definition = { Label: label, ProgramArguments: [path.join(target, 'Contents/Resources/ollama'), 'serve'],
      EnvironmentVariables: { OLLAMA_HOST: '127.0.0.1:11434', OLLAMA_MODELS: locations(platform).models },
      RunAtLoad: true, KeepAlive: true, ThrottleInterval: 10,
      StandardOutPath: path.join(logs, 'ollama.log'), StandardErrorPath: path.join(logs, 'ollama-error.log') };
    const file = path.join(agents, label + '.plist');
    await fs.writeFile(file, '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0">' + plist(definition) + '</plist>', { mode: 0o600 });
    const domain = `gui/${process.getuid()}`;
    await execute('/bin/launchctl', ['bootout', `${domain}/${label}`], { timeout: 30000 }).catch(() => {});
    await execute('/bin/launchctl', ['enable', `${domain}/${label}`], { timeout: 30000 });
    await execute('/bin/launchctl', ['bootstrap', domain, file], { timeout: 30000 });
  }
  else {
    // The official GUI owns its background server and login registration.
    const { spawn } = require('node:child_process');
    await new Promise((resolve, reject) => {
      const child = spawn(target, [], { detached: true, stdio: 'ignore', windowsHide: true });
      child.once('error', reject); child.once('spawn', () => { child.unref(); resolve(); });
    });
  }
}
async function installArtifact(platform, artifact, directory) {
  if (platform === 'win32') {
    try { await execute(artifact, ['/SILENT', '/CURRENTUSER', '/NORESTART', `/DIR=${locations(platform).install}`], { timeout: 30 * 60 * 1000, windowsHide: true, maxBuffer: 1024 * 1024 }); }
    catch (error) { if (error.code !== 3010) throw Error('installer'); }
    const target = await installedPath(platform); if (!target) throw Error('installer'); return target;
  }
  const unpacked = path.join(directory, 'unpacked');
  await execute('/usr/bin/ditto', ['-x', '-k', artifact, unpacked], { timeout: 5 * 60 * 1000 });
  const app = path.join(unpacked, 'Ollama.app');
  await execute('/usr/bin/codesign', ['--verify', '--deep', '--strict', app], { timeout: 60000 });
  await execute('/usr/sbin/spctl', ['--assess', '--type', 'execute', app], { timeout: 60000 });
  const destination = path.join(locations(platform).install, 'Ollama.app');
  await fs.mkdir(path.dirname(destination), { recursive: true });
  // Never replace another installation; directory creation is an atomic claim.
  await fs.mkdir(destination);
  try { await execute('/usr/bin/ditto', [app, destination], { timeout: 5 * 60 * 1000 }); }
  catch (error) { await fs.rm(destination, { recursive: true, force: true }); throw error; }
  return destination;
}

class OllamaManager extends EventEmitter {
  constructor({ platform = process.platform, fetcher = fetch, inspector = inspectSystem,
    size = modelSize, downloader = download, downloadFetch = fetcher, installer = installArtifact, find = installedPath, starter = launch } = {}) {
    super(); Object.assign(this, { platform, fetcher, inspector, size, downloader, downloadFetch, installer, find, starter });
    this.state = { phase: 'idle', ready: false, installed: false, report: null, error: null };
    this.busy = false;
  }
  update(value) { this.state = { ...this.state, ...value }; this.emit('state', this.state); }
  async runtimeStatus() {
    if (this.runtimePending) return this.runtimePending;
    this.runtimePending = (async () => {
      const get = route => this.fetcher(endpoint + route, { redirect: 'error', signal: AbortSignal.timeout(3000) }).then(response => boundedJSON(response));
      try {
        const version = await get('/api/version');
        if (typeof version.version !== 'string') throw Error('version');
        const runtime = { available: true, version: version.version.slice(0, 80), endpoint, models: null, installedModels: null };
        try {
          const result = await get('/api/ps');
          if (!Array.isArray(result.models)) throw Error('models');
          const number = value => Number.isFinite(value) && value >= 0 ? value : null;
          runtime.models = result.models.slice(0, 100).map(model => ({
            name: String(model.name || model.model || '').slice(0, 160),
            size: number(model.size), sizeVRAM: number(model.size_vram),
            contextLength: number(model.context_length),
            quantization: typeof model.details?.quantization_level === 'string' ? model.details.quantization_level.slice(0, 32) : null,
          }));
        } catch { /* Version is available even when model telemetry is not. */ }
        try {
          const result = await get('/api/tags');
          if (!Array.isArray(result.models)) throw Error('models');
          runtime.installedModels = result.models.slice(0, 1000)
            .map(model => model.name || model.model).filter(name => typeof name === 'string').map(name => name.slice(0, 160));
        } catch { /* A running server alone does not confirm that the chosen model exists. */ }
        return runtime;
      } catch { return { available: false, endpoint, models: null, installedModels: null }; }
    })();
    try { return await this.runtimePending; } finally { this.runtimePending = null; }
  }
  async probe() {
    try {
      const data = await boundedJSON(await this.fetcher(`${endpoint}/api/version`, { redirect: 'error', signal: AbortSignal.timeout(2000) }), 4096);
      return typeof data.version === 'string' && /^\d+\.\d+\.\d+/.test(data.version);
    } catch { return false; }
  }
  async inspect(selected, signal) {
    this.update({ phase: 'inspecting', error: null, progress: null, selected, report: null, modelReady: false });
    let system;
    try { system = await this.inspector(this.platform); } catch { throw Error('inspection'); }
    signal.throwIfAborted();
    let bytes = null;
    try { bytes = await this.size(selected.model, this.fetcher, signal); } catch { signal.throwIfAborted(); }
    const ready = await this.probe(), target = await this.find(this.platform);
    signal.throwIfAborted();
    const report = assess(system, bytes, selected.contextLength, { installing: !ready && !target });
    this.update({ report, ready, installed: ready || Boolean(target) });
    return { report, ready, target };
  }
  async pull(selected, signal) {
    this.update({ phase: 'pulling', progress: null });
    const response = await this.fetcher(`${endpoint}/api/pull`, { method: 'POST', redirect: 'error',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: selected.model, stream: true }),
      signal: AbortSignal.any([signal, AbortSignal.timeout(4 * 60 * 60 * 1000)]) });
    if (!response.ok) {await response.body?.cancel();throw Error('model_pull');}
    let pending = '', success = false, last = 0;
    const decoder = new TextDecoder();
    const accept = line => {
      if (!line.trim()) return;
      const message = JSON.parse(line);
      if (message.error) throw Error('model_pull');
      if (message.status === 'success') success = true;
      if (Date.now()-last>200 && Number.isFinite(message.total) && message.total>0 && Number.isFinite(message.completed)) {
        last=Date.now();this.update({ progress: Math.max(0,Math.min(1,message.completed/message.total)) });
      }
    };
    for await (const chunk of response.body) {
      signal.throwIfAborted(); pending += decoder.decode(chunk, { stream: true });
      let index; while ((index=pending.indexOf('\n'))>=0) {accept(pending.slice(0,index));pending=pending.slice(index+1);}
      if (pending.length>65536) throw Error('model_pull');
    }
    pending+=decoder.decode();accept(pending);
    if (!success) throw Error('model_pull');
    this.update({ phase: 'verifying', progress: null });
    try {
      const result = await boundedJSON(await this.fetcher(`${endpoint}/api/generate`, { method: 'POST', redirect: 'error',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: selected.model, prompt: 'OK', stream: false, keep_alive: 0, options: { num_ctx: selected.contextLength, num_predict: 1, num_gpu: -1 } }),
        signal: AbortSignal.any([signal, AbortSignal.timeout(10*60*1000)]) }));
      if (result.done!==true || result.error) throw Error('model_test');
    } catch { signal.throwIfAborted();throw Error('model_test'); }
    this.update({ phase: 'model_ready', modelReady: true, progress: 1 });
  }
  cancel() {
    if (['inspecting', 'downloading', 'pulling', 'verifying'].includes(this.state.phase)) this.abort?.abort();
  }
  async run(action, message) {
    if (this.busy) return;
    this.busy = true; this.abort = new AbortController();
    let directory;
    try {
      const selected = selection(message);
      const { report, ready, target } = await this.inspect(selected, this.abort.signal);
      if (action === 'inspect' || !report.eligible) { this.update({ phase: report.eligible ? 'checked' : 'blocked' }); return; }
      this.abort.signal.throwIfAborted();
      if (action === 'pull') { if (!ready) throw Error('not_running'); await this.pull(selected, this.abort.signal); return; }
      if (ready) {
        if (action === 'setup') await this.pull(selected, this.abort.signal);
        else this.update({ phase: 'ready' });
        return;
      }
      let app = target;
      if (!app) {
        if (action === 'start') throw Error('not_installed');
        const artifact = release[this.platform]; if (!artifact) throw Error('platform');
        directory = await fs.mkdtemp(path.join(os.tmpdir(), 'secretary-ollama-'));
        const file = path.join(directory, artifact.filename);
        this.update({ phase: 'downloading', progress: 0 });
        await this.downloader(artifact, file, this.downloadFetch, AbortSignal.any([this.abort.signal, AbortSignal.timeout(30 * 60 * 1000)]), progress => this.update({ progress }));
        this.abort.signal.throwIfAborted();
        // Space and RAM may have changed during a large download. Check again
        // before executing an installer; never rely on a renderer's old report.
        const current = assess(await this.inspector(this.platform), report.modelBytes, selected.contextLength, { stagedBytes: artifact.bytes });
        this.abort.signal.throwIfAborted();
        this.update({ report: current });
        if (!current.eligible) { this.update({ phase: 'blocked' }); return; }
        this.update({ phase: 'installing', progress: null });
        try { app = await this.find(this.platform) || await this.installer(this.platform, file, directory); }
        catch { throw Error("installer"); }
        this.update({ installed: true });
      }
      this.update({ phase: 'starting', progress: null });
      await this.starter(this.platform, app);
      for (let attempt = 0; attempt < 90; attempt++) {
        if (await this.probe()) {
          this.update({ phase: 'ready', ready: true });
          if (action === 'setup') await this.pull(selected, this.abort.signal);
          return;
        }
        await delay(1000);
      }
      throw Error('start_timeout');
    } catch (error) {
      const known = ['model_invalid', 'context_invalid', 'inspection', 'download', 'integrity', 'installer', 'not_installed', 'platform', 'start_timeout', 'not_running', 'model_pull', 'model_test'];
      this.update({ phase: this.abort.signal.aborted ? 'cancelled' : 'error', error: known.includes(error.message) ? error.message : ({ downloading: 'download', pulling: 'model_pull', starting: 'start_timeout', inspecting: 'inspection', installing: 'installer' }[this.state.phase] || 'operation'), progress: null });
    } finally {
      if (directory) await fs.rm(directory, { recursive: true, force: true }).catch(() => {});
      this.busy = false; this.emit('state', this.state);
    }
  }
}
function trusted(event, getContents, getOrigin) {
  try { const url = new URL(event.senderFrame.url);
    return event.sender === getContents() && event.senderFrame === event.sender.mainFrame && url.origin === getOrigin() && ['/admin', '/admin/'].includes(url.pathname);
  } catch { return false; }
}
function registerIPC(ipcMain, manager, getContents, getOrigin) {
  const publish = () => { const contents = getContents();
    if (contents && !contents.isDestroyed() && trusted({ sender: contents, senderFrame: contents.mainFrame }, getContents, getOrigin))
      contents.send(channel, { ...manager.state, busy: manager.busy });
  };
  manager.on('state', publish);
  ipcMain.on(channel, (event, message) => {
    if (!trusted(event, getContents, getOrigin) || !message || typeof message !== 'object') return;
    if (message.action === 'status') publish();
    else if (message.action === 'runtime') {
      void manager.runtimeStatus().then(runtime => {
        if (trusted(event, getContents, getOrigin) && !event.sender.isDestroyed())
          event.sender.send(channel, { runtimeOnly: true, runtime });
      });
    }
    else if (message.action === 'cancel') manager.cancel();
    else if (['inspect', 'setup', 'install', 'start', 'pull'].includes(message.action)) void manager.run(message.action, message);
  });
}
module.exports = { OllamaManager, registerIPC, trusted, modelName, modelSize, download, downloadFetcher, assess, endpoint };
