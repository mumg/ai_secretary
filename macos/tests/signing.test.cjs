const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('Developer ID signs only the universal app and leaves hashed server resources untouched', async () => {
  let options;
  let copied = false;
  let cleared = false;
  const sandbox = {
    module: { exports: {} }, __dirname: path.resolve(__dirname, '..'),
    console: { log() {} },
    setInterval: () => 1,
    clearInterval: timer => { assert.equal(timer, 1); cleared = true; },
    process: { env: { MACOS_SIGNING_IDENTITY: 'CERT', MACOS_SIGNING_KEYCHAIN: '/ci/keychain' } },
    require(name) {
      if (name === 'node:child_process') return { execFileSync: () => assert.fail('unexpected ad-hoc signing') };
      if (name === 'node:path') return path;
      if (name === 'node:fs/promises') return { cp: async (source, target, opts) => {
        assert.equal(source, path.resolve(__dirname, '../../dist/macos/payload'));
        assert.equal(target, path.resolve('/build/AI Secretary.app/Contents/Resources/server'));
        assert.equal(opts.verbatimSymlinks, true);
        copied = true;
      } };
      if (name === 'builder-util') return { Arch: { universal: 4 } };
      if (name === '@electron/osx-sign') return { signAsync: async opts => {
        assert.equal(copied, true);
        options = opts;
      } };
      throw Error(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../sign.cjs'), 'utf8'), sandbox);
  const context = { arch: 1, appOutDir: '/build', packager: { appInfo: { productFilename: 'AI Secretary' } } };
  await sandbox.module.exports(context);
  assert.equal(options, undefined);
  assert.equal(copied, false);
  await sandbox.module.exports({ ...context, arch: 4 });
  assert.equal(options.identity, 'CERT');
  assert.equal(options.keychain, '/ci/keychain');
  assert.equal(cleared, true);
  assert.equal(options.optionsForFile(options.app).hardenedRuntime, true);
  const payload = '/build/AI Secretary.app/Contents/Resources/server';
  assert.equal(typeof options.ignore, 'function');
  assert.equal(options.ignore(payload), true);
  assert.equal(options.ignore(payload + '/postgres/bin/postgres'), true);
  assert.equal(options.ignore(payload + '-other/binary'), false);
  assert.equal(options.ignore(options.app), false);

  // Exercise the real dependency's option normalization and directory walk.
  // Mock only codesign execution, so this also runs on Windows CI without keys.
  const os = require('node:os');
  const fsp = require('node:fs/promises');
  const child = require('node:child_process');
  const temporary = await fsp.mkdtemp(path.join(os.tmpdir(), 'secretary-sign-ignore-'));
  const app = path.join(temporary, 'Test.app');
  const server = path.join(app, 'Contents/Resources/server');
  await fsp.mkdir(server, { recursive: true });
  await fsp.writeFile(path.join(server, 'binary'), Buffer.from([0xcf, 0xfa, 0xed, 0xfe, 0, 0, 0, 0]));
  const originalExec = child.execFile;
  const signed = [];
  child.execFile = (file, args, execOptions, callback) => {
    assert.equal(file, 'codesign');
    if (args.includes('--sign')) signed.push(args.at(-1));
    queueMicrotask(() => callback(null, '', ''));
  };
  try {
    await require('@electron/osx-sign').signAsync({
      ...options, app, identity: '-', identityValidation: false, keychain: undefined,
      ignore: file => options.ignore(path.join('/build/AI Secretary.app', path.relative(app, file))),
    });
    assert.deepEqual(signed, [app], 'the dependency must not re-sign server binaries');
  } finally {
    child.execFile = originalExec;
    await fsp.rm(temporary, { recursive: true, force: true });
  }
});
