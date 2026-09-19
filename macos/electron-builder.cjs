module.exports = {
  appId: 'net.muratov.secretary.desktop',
  productName: 'AI Secretary',
  artifactName: 'AI-Secretary-${version}-mac-universal.${ext}',
  directories: { output: '../dist/macos', buildResources: 'assets' },
  files: ['main.cjs', 'services.cjs', 'windows-services.cjs', 'mts-auth.cjs', 'preload.cjs', 'splash.html', 'package.json'],
  extraResources: [{ from: '../dist/macos/payload', to: 'server' }],
  asar: true,
  mac: {
    target: [{ target: 'dir', arch: ['universal'] }],
    category: 'public.app-category.productivity',
    minimumSystemVersion: '13.0',
    identity: null,
    icon: 'assets/secretary.icns',
    // afterPack signs only the merged universal app. Built-in signing stays
    // disabled; the release hook enables hardened runtime for Developer ID.
    hardenedRuntime: false,
    gatekeeperAssess: false,
    x64ArchFiles: 'Contents/Resources/server/**',
    extendInfo: { NSHumanReadableCopyright: 'AI Secretary contributors' }
  },
  afterPack: require('./sign.cjs'),
  publish: null
};
