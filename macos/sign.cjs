const { execFileSync } = require('node:child_process');
const path = require('node:path');

module.exports = async context => {
  // Signing slices would create different CodeResources files before the merge.
  if (context.arch !== require('builder-util').Arch.universal) return;
  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`);
  if (process.env.MACOS_SIGNING_IDENTITY) {
    const payload = path.resolve(app, 'Contents/Resources/server');
    // The payload was signed before files.json was generated. Re-signing it
    // would change the timestamp and invalidate the runtime's checksums.
    await require('@electron/osx-sign').signAsync({
      app,
      identity: process.env.MACOS_SIGNING_IDENTITY,
      keychain: process.env.MACOS_SIGNING_KEYCHAIN || undefined,
      platform: 'darwin',
      type: 'distribution',
      preAutoEntitlements: false,
      preEmbedProvisioningProfile: false,
      gatekeeperAssess: false,
      ignore: [file => path.resolve(file) === payload || path.resolve(file).startsWith(payload + path.sep)],
    });
    return;
  }
  execFileSync('python3', [path.join(__dirname, 'sign.py'),
    app],
    { stdio: 'inherit' });
};
