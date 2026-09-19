module.exports = {
  appId: 'net.muratov.secretary.desktop',
  productName: 'AI Secretary',
  artifactName: 'AI-Secretary-${version}-mac-universal.${ext}',
  directories: { output: '../dist/macos', buildResources: 'assets' },
  files: ['main.cjs', 'services.cjs', 'splash.html', 'package.json'],
  extraResources: [{ from: '../dist/macos/payload', to: 'server' }],
  asar: true,
  mac: {
    target: [{ target: 'dir', arch: ['universal'] }],
    category: 'public.app-category.productivity',
    minimumSystemVersion: '13.0',
    identity: null,
    icon: 'assets/secretary.icns',
    // The build verifies and ad-hoc signs every nested Mach-O after merging.
    // Developer ID signing/notarization is an optional, separate release step.
    hardenedRuntime: false,
    gatekeeperAssess: false,
    x64ArchFiles: 'Contents/Resources/server/**',
    extendInfo: { NSHumanReadableCopyright: 'AI Secretary contributors' }
  },
  afterPack: require('./sign.cjs'),
  publish: null
};
