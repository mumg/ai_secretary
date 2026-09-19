const { test } = require('node:test');
const assert = require('node:assert/strict');
const { Services, plist, compareVersions, validateConnection } = require('../services.cjs');
const connection = { apiPort: 18000, parserPort: 18080, databasePort: 15432, httpsPort: 18443, httpPort: 18081, publicHost: '' };

test('launchd runs independent jobs with private secret files, loopback ports and escaped paths', () => {
  const s = new Services({ payload: '/tmp/payload', data: '/tmp/AI Secretary & тест' });
  s.runtime = '/tmp/runtime & version';
  for (const name of ['database', 'parser', 'api', 'worker', 'proxy']) {
    const job = s.definition(name, connection);
    assert.equal(job.KeepAlive, true);
    assert.equal(job.RunAtLoad, true);
    assert.equal(job.Umask, 63);
    assert.ok(job.ProgramArguments[0].startsWith(s.runtime));
    assert.ok(!JSON.stringify(job).includes('PGPASSWORD'));
    assert.ok(!JSON.stringify(job).includes('Electron'));
    assert.match(plist(job), /&amp;/);
  }
  assert.equal(s.environment(connection).LOCAL_WEB_ONLY, 'true');
  assert.equal(s.environment({ ...connection, publicHost: 'secretary.example.org' }).PUBLIC_URL, 'https://secretary.example.org');
  assert.ok(s.definition('database', connection).ProgramArguments.includes('127.0.0.1'));
});
test('versions compare numerically, reject non-release versions', () => {
  assert.equal(compareVersions('0.1.10', '0.1.9'), 1);
  assert.equal(compareVersions('0.1.9', '0.2.0'), -1);
  assert.equal(compareVersions('1.0.0', '1.0.0'), 0);
  assert.throws(() => compareVersions('../secret', '1.0.0'));
});
test('connection configuration cannot inject Caddy directives or collide ports', () => {
  assert.deepEqual(validateConnection(connection), connection);
  for (const publicHost of ['https://example.org', 'example.org\nadmin :2019', 'example.org {', '../file'])
    assert.throws(() => validateConnection({ ...connection, publicHost }));
  assert.throws(() => validateConnection({ ...connection, apiPort: 5432, databasePort: 5432 }));
  assert.throws(() => validateConnection({ ...connection, httpsPort: 443 }));
});
test('stop order drains HTTP and worker clients before PostgreSQL', async () => {
  const s = new Services({ payload: '/tmp/payload' });
  const stopped = []; s.stop = async name => stopped.push(name);
  await s.stopAll();
  assert.deepEqual(stopped, ['proxy', 'worker', 'api', 'parser', 'database']);
});
