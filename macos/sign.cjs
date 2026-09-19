const { execFileSync } = require('node:child_process');
const path = require('node:path');

module.exports = async context => {
  // Signing slices would create different CodeResources files before the merge.
  if (context.arch !== require('builder-util').Arch.universal) return;
  execFileSync('python3', [path.join(__dirname, 'sign.py'),
    path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`)],
    { stdio: 'inherit' });
};
