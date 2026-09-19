const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('Developer ID signs only the universal app and leaves hashed server resources untouched', async () => {
  let options;
  const sandbox = {
    module: { exports: {} }, __dirname: path.resolve(__dirname, '..'),
    process: { env: { MACOS_SIGNING_IDENTITY: 'CERT', MACOS_SIGNING_KEYCHAIN: '/ci/keychain' } },
    require(name) {
      if (name === 'node:child_process') return { execFileSync: () => assert.fail('unexpected ad-hoc signing') };
      if (name === 'node:path') return path;
      if (name === 'builder-util') return { Arch: { universal: 4 } };
      if (name === '@electron/osx-sign') return { signAsync: async opts => { options = opts; } };
      throw Error(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../sign.cjs'), 'utf8'), sandbox);
  const context = { arch: 1, appOutDir: '/build', packager: { appInfo: { productFilename: 'AI Secretary' } } };
  await sandbox.module.exports(context);
  assert.equal(options, undefined);
  await sandbox.module.exports({ ...context, arch: 4 });
  assert.equal(options.identity, 'CERT');
  assert.equal(options.keychain, '/ci/keychain');
  const payload = '/build/AI Secretary.app/Contents/Resources/server';
  assert.equal(options.ignore[0](payload), true);
  assert.equal(options.ignore[0](payload + '/postgres/bin/postgres'), true);
  assert.equal(options.ignore[0](payload + '-other/binary'), false);
  assert.equal(options.ignore[0](options.app), false);
});
