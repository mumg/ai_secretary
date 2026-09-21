const path = require('node:path');
module.exports = {
  appId: 'net.muratov.secretary.desktop',
  productName: 'AI Secretary',
  directories: { output: '../dist/windows/electron' },
  files: ['i18n.cjs', 'translations.json', 'main.cjs', 'services.cjs', 'windows-services.cjs', 'mts-auth.cjs', 'ollama.cjs', 'ollama-system.cjs', 'ollama-release.json', 'preload.cjs', 'splash.html', 'package.json'],
  asar: true,
  win: { target: [{ target: 'dir', arch: ['x64'] }], icon: path.join(__dirname, 'assets/secretary.ico'),
    // The outer Inno Setup package installs/elevates services. Electron itself
    // runs as the original, non-administrator user.
    requestedExecutionLevel: 'asInvoker',
    signAndEditExecutable: process.platform === 'win32' },
  publish: null
};
