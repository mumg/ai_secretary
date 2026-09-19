// Isolated real launchd/PostgreSQL smoke test. Never uses the normal application's data.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { Services, run, availablePort } = require('./services.cjs');

async function main() {
  const payload = path.resolve(process.argv[2] || 'dist/macos/payload');
  const data = await fs.mkdtemp(path.join(os.homedir(), 'Library/Application Support/AI Secretary smoke тест & '));
  const services = new Services({ payload, data, label: `net.muratov.secretary.smoke.${process.pid}`, progress: console.log });
  const c = { publicHost: '' }, used = [];
  for (const key of ['apiPort', 'parserPort', 'databasePort', 'httpsPort', 'httpPort']) {
    c[key] = await availablePort(0, used); used.push(c[key]);
  }
  await fs.writeFile(path.join(data, 'connection.json'), JSON.stringify(c));
  try {
    const origin = await services.ensure();
    assert.equal((await fetch(`${origin}/health/ready`).then(r => r.json())).status, 'ready');
    for (const route of ['/app/', '/admin', '/app/assets/app.js', '/api/v1/admin/settings', '/api/v1/admin/gateway']) {
      const r = await fetch(origin + route);
      assert.equal(r.status, 200, route);
    }
    const pdf = await fs.readFile(path.join(__dirname, '../document-parser/testdata/text.pdf'));
    const form = new FormData(); form.append('file', new Blob([pdf], { type: 'application/pdf' }), 'text.pdf');
    const parsed = await fetch(`http://127.0.0.1:${c.parserPort}/extract`, { method: 'POST', body: form });
    assert.equal(parsed.status, 200, `parser: ${await parsed.text()}`);
    for (const name of ['database', 'parser', 'api', 'worker']) assert.match(await services.loaded(name), /state = running/);
    const executable = path.resolve(payload, '../../MacOS/AI Secretary');
    if (await fs.stat(executable).then(s => s.isFile(), () => false)) {
      await run(executable, ['--secretary-smoke-test'], { env: { ...process.env,
        AI_SECRETARY_SMOKE_ROOT: data, AI_SECRETARY_SMOKE_LABEL: services.label }, timeout: 180000 });
      const renderer = JSON.parse(await fs.readFile(path.join(data, 'electron-smoke.json')));
      assert.equal(renderer.tabs, 5);
      assert.equal(renderer.node, 'undefined');
      assert.equal(renderer.require, 'undefined');
      assert.equal(renderer.nativeSSO, true);
      await fs.mkdir('dist/macos', { recursive: true });
      await fs.copyFile(path.join(data, 'electron-app.png'), 'dist/macos/smoke-electron.png');
      // The actual Electron process has exited; all launchd jobs must remain running.
      for (const name of ['database', 'parser', 'api', 'worker']) assert.match(await services.loaded(name), /state = running/);
      console.log('PASS: packaged Electron renders app/settings with sandbox; services survive UI exit');
    }
    const key = await fs.readFile(path.join(data, 'secrets/master-key'));
    const keyPath = path.join(data, 'secrets/master-key');
    await fs.rename(keyPath, keyPath + '.saved');
    try {
      await assert.rejects(() => services.ensure(), /Отсутствует secrets\/master-key/);
      assert.match(await services.loaded('api'), /state = running/);
    } finally { await fs.rename(keyPath + '.saved', keyPath); }
    await services.sql(c, 'CREATE TABLE secretary_smoke_marker (id int); INSERT INTO secretary_smoke_marker VALUES (42);');
    // Death of the UI/installer does not own the services. Exercise an actual API crash/restart.
    await run('/bin/launchctl', ['kill', 'SIGKILL', services.target('api')]);
    await services.waitURL(`${origin}/health/live`);
    await services.stopAll();
    await services.ensure();
    assert.equal(await services.sql(c, 'SELECT id FROM secretary_smoke_marker;'), '42');
    assert.deepEqual(await fs.readFile(path.join(data, 'secrets/master-key')), key);
    // Simulate a previous release to exercise the real cold-backup/migration path.
    const current = JSON.parse(await fs.readFile(path.join(data, 'installed.json')));
    await fs.writeFile(path.join(data, 'installed.json'), JSON.stringify({ ...current, version: '0.0.1', digest: '0'.repeat(64) }));
    await services.ensure();
    const backups = await fs.readdir(path.join(data, 'backups'));
    assert.equal(backups.length, 1);
    assert.deepEqual(await fs.readFile(path.join(data, 'backups', backups[0], 'secrets/master-key')), key);
    assert.equal(await services.sql(c, 'SELECT id FROM secretary_smoke_marker;'), '42');
    // Uninstall preserves data, reinstall attaches the same cluster and secrets.
    await services.uninstall();
    assert.ok(await fs.stat(path.join(data, 'postgres/PG_VERSION')));
    await services.ensure();
    assert.equal(await services.sql(c, 'SELECT id FROM secretary_smoke_marker;'), '42');
    const installed = JSON.parse(await fs.readFile(path.join(data, 'installed.json')));
    await fs.writeFile(path.join(data, 'installed.json'), JSON.stringify({ ...installed, version: '999.0.0' }));
    await assert.rejects(() => services.ensure(), /старой версии/);
    await fs.writeFile(path.join(data, 'installed.json'), JSON.stringify(installed));
    console.log('PASS: services, database, API, web, parser, restart, reinstall, data preservation, downgrade guard');
  } finally {
    await services.uninstall();
    await fs.rm(data, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
