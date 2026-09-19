const { execFileSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs/promises');

module.exports = async context => {
  // Signing slices would create different CodeResources files before the merge.
  if (context.arch !== require('builder-util').Arch.universal) return;
  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);
  const payload = path.resolve(app, 'Contents/Resources/server');
  console.log('[macOS] Electron universal merge complete; copying the signed server payload once.');
  await fs.cp(path.resolve(__dirname, '../dist/macos/payload'), payload,
    { recursive: true, verbatimSymlinks: true, preserveTimestamps: true });
  console.log('[macOS] Server payload copied; starting application signing.');
  if (process.env.MACOS_SIGNING_IDENTITY) {
    const started = Date.now();
    let current = 'discovering Electron code';
    const heartbeat = setInterval(() => {
      console.log(`[macOS] Signing still running (${Math.round((Date.now() - started) / 1000)}s): ${current}`);
    }, 30000);
    // The payload was signed before files.json was generated. Re-signing it
    // would change the timestamp and invalidate the runtime's checksums.
    try {
      await require('@electron/osx-sign').signAsync({
        app,
        identity: process.env.MACOS_SIGNING_IDENTITY,
        keychain: process.env.MACOS_SIGNING_KEYCHAIN || undefined,
        platform: 'darwin',
        type: 'distribution',
        preAutoEntitlements: false,
        preEmbedProvisioningProfile: false,
        gatekeeperAssess: false,
        // osx-sign 1.3.3 drops array-valued ignore in validateOptsIgnore.
        // A single function is normalized correctly; do not wrap it in [].
        ignore: file => path.resolve(file) === payload || path.resolve(file).startsWith(payload + path.sep),
        optionsForFile: file => {
          current = path.relative(app, file) || path.basename(app);
          console.log(`[macOS] Signing: ${current}`);
          return { hardenedRuntime: true };
        },
      });
      console.log('[macOS] Developer ID signing and signature verification complete.');
    } finally {
      clearInterval(heartbeat);
    }
    return;
  }
  execFileSync('python3', [path.join(__dirname, 'sign.py'),
    app],
    { stdio: 'inherit' });
  console.log('[macOS] Ad-hoc signing complete.');
};
