'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const net = require('node:net');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');

const SERVICE_NAMES = ['database', 'parser', 'api', 'worker', 'proxy'];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const exists = async file => { try { await fs.access(file); return true; } catch { return false; } };
const readJSON = async file => JSON.parse(await fs.readFile(file, 'utf8'));
const xml = value => String(value).replace(/[<>&"']/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' }[c]));
function plist(value) {
  if (Array.isArray(value)) return `<array>${value.map(plist).join('')}</array>`;
  if (typeof value === 'boolean') return value ? '<true/>' : '<false/>';
  if (typeof value === 'number') return `<integer>${value}</integer>`;
  if (typeof value === 'object') return `<dict>${Object.entries(value).map(([k, v]) => `<key>${xml(k)}</key>${plist(v)}`).join('')}</dict>`;
  return `<string>${xml(value)}</string>`;
}
function compareVersions(a, b) {
  if (![a, b].every(v => /^\d+\.\d+\.\d+$/.test(v))) throw Error('Некорректная версия выпуска');
  const aa = a.split('.').map(Number), bb = b.split('.').map(Number);
  for (let i = 0; i < 3; i++) if (aa[i] !== bb[i]) return Math.sign(aa[i] - bb[i]);
  return 0;
}
async function atomic(file, value) {
  const tmp = `${file}.${crypto.randomUUID()}.tmp`;
  await fs.writeFile(tmp, value, { mode: 0o600 });
  await fs.rename(tmp, file);
}
async function json(file, value) { await atomic(file, JSON.stringify(value, null, 2) + '\n'); }
function run(executable, args = [], options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, { env: options.env || process.env, cwd: options.cwd,
      stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    child.stdout.on('data', b => { stdout = (stdout + b).slice(-1048576); });
    child.stderr.on('data', b => { stderr = (stderr + b).slice(-16384); });
    const timer = setTimeout(() => child.kill('SIGKILL'), options.timeout || 120000);
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('close', code => {
      clearTimeout(timer);
      if (code === 0) resolve(stdout.trim());
      else reject(Error(`${path.basename(executable)}: ${options.private ? 'операция не выполнена' : stderr.trim()} (код ${code})`));
    });
    child.stdin.on('error', () => {});
    child.stdin.end(options.input || '');
  });
}
async function availablePort(preferred, reserved = []) {
  const probe = port => new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => {
      const actual = server.address().port;
      server.close(() => resolve(actual));
    });
  });
  try { if (!reserved.includes(preferred)) return await probe(preferred); } catch { /* Pick an unused port. */ }
  for (;;) { const p = await probe(0); if (!reserved.includes(p)) return p; }
}
function validateConnection(c) {
  const ports = [c.apiPort, c.parserPort, c.databasePort, c.httpsPort, c.httpPort];
  if (ports.some(p => !Number.isInteger(p) || p < 1024 || p > 65535) || new Set(ports).size !== ports.length)
    throw Error('В connection.json нужны пять разных портов от 1024 до 65535');
  if (c.publicHost && !/^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(c.publicHost))
    throw Error('publicHost должен содержать только публичное доменное имя');
  return c;
}

class Services {
  constructor({ payload, data = path.join(os.homedir(), 'Library/Application Support/AI Secretary'),
    label = 'net.muratov.secretary', progress = () => {} }) {
    if (!/^[a-zA-Z0-9.-]+$/.test(label)) throw Error('Invalid launchd label');
    this.payload = path.resolve(payload);
    this.data = path.resolve(data);
    this.label = label;
    this.agents = path.join(os.homedir(), 'Library/LaunchAgents');
    this.domain = `gui/${process.getuid()}`;
    this.progress = progress;
  }
  target(name) { return `${this.domain}/${this.label}.${name}`; }
  agentFile(name) { return path.join(this.agents, `${this.label}.${name}.plist`); }
  async lock(action) {
    await fs.mkdir(this.data, { recursive: true, mode: 0o700 });
    await fs.chmod(this.data, 0o700);
    const file = path.join(this.data, 'setup.lock');
    let handle;
    for (let attempt = 0; attempt < 2; attempt++) {
      try { handle = await fs.open(file, 'wx', 0o600); break; }
      catch (e) {
        if (e.code !== 'EEXIST') throw e;
        const pid = Number(await fs.readFile(file, 'utf8'));
        if (!Number.isInteger(pid) || pid < 1) throw Error('Другая настройка служб уже выполняется');
        try { process.kill(pid, 0); throw Error('Другая настройка служб уже выполняется'); }
        catch (err) { if (err.code !== 'ESRCH') throw err; }
        await fs.unlink(file);
      }
    }
    if (!handle) throw Error('Не удалось заблокировать настройку служб');
    try { await handle.writeFile(String(process.pid)); return await action(); }
    finally { await handle.close(); await fs.unlink(file); }
  }
  async connection() {
    const file = path.join(this.data, 'connection.json');
    if (await exists(file)) return validateConnection(await readJSON(file));
    const c = { publicHost: '' }, reserved = [];
    for (const [key, preferred] of Object.entries({ apiPort: 18000, parserPort: 18080, databasePort: 15432, httpsPort: 18443, httpPort: 18081 })) {
      c[key] = await availablePort(preferred, reserved); reserved.push(c[key]);
    }
    await json(file, c);
    return c;
  }
  environment(c) {
    return {
      DATABASE_URL: `postgresql://improver@127.0.0.1:${c.databasePort}/improver?sslmode=disable`,
      DATABASE_PASSWORD_FILE: path.join(this.data, 'secrets/database-password'),
      APP_MASTER_KEY_FILE: path.join(this.data, 'secrets/master-key'),
      CLIENT_ISSUER_CERT_FILE: path.join(this.data, 'certificates/client-ca.pem'),
      CLIENT_ISSUER_KEY_FILE: path.join(this.data, 'certificates/client-ca.key'),
      DATA_DIR: path.join(this.data, 'data'),
      DOCUMENT_PARSER_URL: `http://127.0.0.1:${c.parserPort}`,
      OLLAMA_BASE_URL: 'http://127.0.0.1:11434',
      LOCAL_WEB_ONLY: String(!c.publicHost),
      PUBLIC_URL: c.publicHost ? `https://${c.publicHost}` : `http://127.0.0.1:${c.apiPort}`,
      LANG: 'en_US.UTF-8', LC_ALL: 'en_US.UTF-8'
    };
  }
  async secrets() {
    const cluster = await exists(path.join(this.data, 'postgres/PG_VERSION'));
    for (const name of ['master-key', 'database-password', 'postgres-admin-password']) {
      const file = path.join(this.data, 'secrets', name);
      if (await exists(file)) {
        if ((await fs.readFile(file, 'utf8')).trim().length < 32) throw Error(`Повреждён secrets/${name}`);
      } else {
        if (cluster) throw Error(`Отсутствует secrets/${name}; восстановите резервную копию`);
        await atomic(file, crypto.randomBytes(48).toString('hex') + '\n');
      }
    }
  }
  async certificates() {
    const dir = path.join(this.data, 'certificates');
    const cert = path.join(dir, 'client-ca.pem'), key = path.join(dir, 'client-ca.key');
    if (await exists(cert) && await exists(key)) return;
    if (await exists(cert) || await exists(key)) throw Error('Неполный комплект клиентской CA');
    await run('/usr/bin/openssl', ['req', '-x509', '-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256',
      '-nodes', '-keyout', key, '-out', cert, '-days', '3650', '-subj', '/CN=AI Secretary Client CA',
      '-config', path.join(this.runtime, 'ca.cnf')]);
    await fs.chmod(key, 0o600);
  }
  definition(name, c) {
    const binary = n => path.join(this.runtime, 'bin', n);
    let args, env = this.environment(c);
    switch (name) {
      case 'database':
        args = [path.join(this.runtime, 'postgres/bin/postgres'), '-D', path.join(this.data, 'postgres'),
          '-p', String(c.databasePort), '-h', '127.0.0.1', '-c', "unix_socket_directories=", '-c', 'password_encryption=scram-sha-256'];
        env = { LANG: 'en_US.UTF-8', LC_ALL: 'en_US.UTF-8' }; break;
      case 'parser': args = [binary('document-parser'), 'serve', '--listen', `127.0.0.1:${c.parserPort}`]; break;
      case 'api': args = [binary('improver'), 'serve', '--listen', `127.0.0.1:${c.apiPort}`, '--web-dir', path.join(this.runtime, 'web')]; break;
      case 'worker': args = [binary('improver'), 'worker']; break;
      case 'proxy':
        args = [binary('caddy'), 'run', '--config', path.join(this.data, 'Caddyfile'), '--adapter', 'caddyfile'];
        env = { XDG_DATA_HOME: path.join(this.data, 'caddy'), XDG_CONFIG_HOME: path.join(this.data, 'caddy') }; break;
      default: throw Error('Unknown service');
    }
    return { Label: `${this.label}.${name}`, ProgramArguments: args, EnvironmentVariables: env,
      WorkingDirectory: this.data, RunAtLoad: true, KeepAlive: true, ThrottleInterval: 10,
      ExitTimeOut: 60, Umask: 63, ProcessType: 'Background',
      StandardOutPath: path.join(this.data, 'logs', `${name}.log`),
      StandardErrorPath: path.join(this.data, 'logs', `${name}.log`) };
  }
  async loaded(name) {
    try { return await run('/bin/launchctl', ['print', this.target(name)]); } catch { return ''; }
  }
  async start(name, c) {
    const definition = this.definition(name, c);
    const content = `<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0">${plist(definition)}</plist>\n`;
    const file = this.agentFile(name);
    let previous = ''; try { previous = await fs.readFile(file, 'utf8'); } catch { /* first install */ }
    if (previous !== content) { await this.stop(name); await atomic(file, content); }
    if (!await this.loaded(name)) {
      await run('/bin/launchctl', ['enable', this.target(name)]);
      await run('/bin/launchctl', ['bootstrap', this.domain, file]);
    }
  }
  async stop(name) {
    const loaded = await this.loaded(name);
    if (!loaded) return;
    const pid = Number(loaded.match(/\bpid = (\d+)/)?.[1]);
    await run('/bin/launchctl', ['bootout', this.target(name)], { timeout: 90000 });
    if (pid) {
      for (let i = 0; i < 300; i++) {
        try { process.kill(pid, 0); } catch (e) { if (e.code === 'ESRCH') return; throw e; }
        await sleep(200);
      }
      throw Error(`Служба ${name} не завершилась; обновление отменено`);
    }
  }
  async stopAll() { for (const name of [...SERVICE_NAMES].reverse()) await this.stop(name); }
  async uninstall() {
    return this.lock(async () => {
      await this.stopAll();
      for (const name of SERVICE_NAMES) await fs.rm(this.agentFile(name), { force: true });
    });
  }
  async sql(c, statement) {
    const password = (await fs.readFile(path.join(this.data, 'secrets/postgres-admin-password'), 'utf8')).trim();
    return run(path.join(this.runtime, 'postgres/bin/psql'), ['-X', '-w', '-h', '127.0.0.1', '-p', String(c.databasePort),
      '-U', 'postgres', '-d', 'postgres', '-At', '-v', 'ON_ERROR_STOP=1'], {
      env: { ...process.env, PGPASSWORD: password, PGCONNECT_TIMEOUT: '3' }, input: statement, private: true, timeout: 10000
    });
  }
  async waitDatabase(c) {
    for (let i = 0; i < 90; i++) { try { await this.sql(c, 'SELECT 1;'); return; } catch { await sleep(1000); } }
    throw Error('PostgreSQL не запустился. Подробности в logs/database.log');
  }
  async waitURL(url, version) {
    for (let i = 0; i < 90; i++) {
      try {
        const response = await fetch(url, { signal: AbortSignal.timeout(2000) });
        if (response.ok && (!version || (await response.json()).version === version)) return;
      } catch { /* launchd is starting the service */ }
      await sleep(1000);
    }
    throw Error('Сервер не запустился. Откройте журнал через меню приложения');
  }
  async backup() {
    // Called with ALL services stopped: PostgreSQL and attachments form one snapshot.
    const backup = path.join(this.data, 'backups', new Date().toISOString().replace(/[:.]/g, '-'));
    await fs.mkdir(backup, { recursive: true, mode: 0o700 });
    for (const name of ['postgres', 'data', 'secrets', 'certificates', 'caddy', 'connection.json', 'installed.json', 'Caddyfile']) {
      const source = path.join(this.data, name);
      if (await exists(source)) await fs.cp(source, path.join(backup, name), { recursive: true, preserveTimestamps: true, verbatimSymlinks: true });
    }
    await json(path.join(backup, 'backup.json'), { format: 'cold-cluster', created: new Date().toISOString() });
    return backup;
  }
  async ensure() { return this.lock(() => this.configure()); }
  async configure() {
    this.progress('Подготовка серверных компонентов…');
    for (const name of ['runtime', 'data', 'secrets', 'certificates', 'caddy', 'logs', 'backups'])
      await fs.mkdir(path.join(this.data, name), { recursive: true, mode: 0o700 });
    await fs.mkdir(this.agents, { recursive: true });
    const manifest = await readJSON(path.join(this.payload, 'release.json'));
    if (!/^[a-f0-9]{64}$/.test(manifest.digest)) throw Error('Повреждён манифест приложения');
    compareVersions(manifest.version, manifest.version);
    this.runtime = path.join(this.data, 'runtime', `${manifest.version}-${manifest.digest.slice(0, 16)}`);
    const installedPath = path.join(this.data, 'installed.json');
    const installed = await exists(installedPath) ? await readJSON(installedPath) : null;
    if (installed && compareVersions(manifest.version, installed.version) < 0) throw Error('Установка более старой версии запрещена');
    if (installed && manifest.version === installed.version && manifest.digest !== installed.digest)
      throw Error('Код этой версии отличается от установленного. Требуется новый номер выпуска');
    const cluster = path.join(this.data, 'postgres');
    const hasCluster = await exists(path.join(cluster, 'PG_VERSION'));
    if (hasCluster && (await fs.readFile(path.join(cluster, 'PG_VERSION'), 'utf8')).trim() !== '17')
      throw Error('Обновление major-версии PostgreSQL требует отдельной миграции');
    const c = await this.connection();
    await this.secrets();
    const upgrading = hasCluster && (!installed || installed.digest !== manifest.digest);
    if (!await exists(this.runtime)) {
      const filesData = await fs.readFile(path.join(this.payload, 'files.json'));
      if (crypto.createHash('sha256').update(filesData).digest('hex') !== manifest.digest)
        throw Error('Повреждён список серверных компонентов');
      for (const [name, expected] of Object.entries(JSON.parse(filesData))) {
        const source = path.resolve(this.payload, name);
        if (!source.startsWith(this.payload + path.sep)) throw Error('Недопустимый путь в манифесте');
        const actual = expected.startsWith('symlink:') ? 'symlink:' + await fs.readlink(source) :
          crypto.createHash('sha256').update(await fs.readFile(source)).digest('hex');
        if (actual !== expected) throw Error(`Повреждён компонент: ${name}`);
      }
      const temporary = `${this.runtime}.partial`;
      await fs.rm(temporary, { force: true, recursive: true });
      await fs.cp(this.payload, temporary, { recursive: true, verbatimSymlinks: true });
      await fs.rename(temporary, this.runtime);
    }
    if (upgrading) {
      this.progress('Резервная копия перед обновлением…');
      const previousJobs = [];
      for (const name of SERVICE_NAMES) if (await this.loaded(name)) previousJobs.push(name);
      await this.stopAll();
      let backup;
      try { backup = await this.backup(); }
      catch (error) {
        // No schema/file changes have happened; restore the prior running installation.
        for (const name of previousJobs) await run('/bin/launchctl', ['bootstrap', this.domain, this.agentFile(name)]).catch(() => {});
        throw error;
      }
      await json(path.join(this.data, 'upgrade.json'), { phase: 'prepared', backup, target: manifest.version });
      // A failed migration must not let old API/worker jobs restart on the next login.
      for (const name of SERVICE_NAMES) await fs.rm(this.agentFile(name), { force: true });
    }
    await this.certificates();
    if (!hasCluster) {
      this.progress('Создание локальной базы данных…');
      // Interrupted initdb must not leave a half-created cluster in its final location.
      const temp = path.join(this.data, 'postgres.initializing');
      await fs.rm(temp, { recursive: true, force: true });
      await run(path.join(this.runtime, 'postgres/bin/initdb'), ['-D', temp, '-U', 'postgres',
        '--pwfile', path.join(this.data, 'secrets/postgres-admin-password'), '--auth=scram-sha-256', '--encoding=UTF8', '--locale=C']);
      await fs.rename(temp, cluster);
    }
    this.progress('Запуск базы данных…');
    await this.start('database', c);
    await this.waitDatabase(c);
    if (!installed || upgrading) {
      if (await this.sql(c, "SELECT 1 FROM pg_roles WHERE rolname='improver';") !== '1') {
        const password = (await fs.readFile(path.join(this.data, 'secrets/database-password'), 'utf8')).trim();
        if (!/^[a-f0-9]+$/.test(password)) throw Error('Некорректный пароль базы');
        await this.sql(c, `CREATE ROLE improver LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '${password}';`);
      }
      if (await this.sql(c, "SELECT 1 FROM pg_database WHERE datname='improver';") !== '1')
        await this.sql(c, 'CREATE DATABASE improver OWNER improver;');
      this.progress('Подготовка схемы базы данных…');
      await run(path.join(this.runtime, 'bin/improver'), ['migrate'], { env: { ...process.env, ...this.environment(c) }, timeout: 300000 });
    }
    this.progress('Запуск сервера и обработчика задач…');
    await this.start('parser', c);
    await this.waitURL(`http://127.0.0.1:${c.parserPort}/health`);
    await this.start('api', c);
    const origin = `http://127.0.0.1:${c.apiPort}`;
    await this.waitURL(`${origin}/health/live`, manifest.version);
    await this.waitURL(`${origin}/health/ready`);
    if (c.publicHost) {
      const ca = JSON.stringify(path.join(this.data, 'certificates/client-ca.pem'));
      await atomic(path.join(this.data, 'Caddyfile'), `{
  admin off
  http_port ${c.httpPort}
  https_port ${c.httpsPort}
}
${c.publicHost} {
  tls {
    client_auth {
      mode require_and_verify
      trust_pool file {
        pem_file ${ca}
      }
    }
  }
  reverse_proxy 127.0.0.1:${c.apiPort}
}
`);
      await run(path.join(this.runtime, 'bin/caddy'), ['validate', '--config', path.join(this.data, 'Caddyfile'), '--adapter', 'caddyfile']);
      await this.start('proxy', c);
    } else { await this.stop('proxy'); await fs.rm(this.agentFile('proxy'), { force: true }); }
    await this.start('worker', c);
    await json(installedPath, { ...manifest, runtime: this.runtime, origin });
    await json(path.join(this.data, 'upgrade.json'), { phase: 'complete', version: manifest.version });
    return origin;
  }
}

module.exports = { Services, SERVICE_NAMES, run, plist, compareVersions, validateConnection, availablePort };
