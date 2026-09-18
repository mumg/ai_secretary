// Runtime dependencies are served locally: no CDN or build step in production.
const fs = require("node:fs");
const path = require("node:path");
const destination = path.resolve(
  __dirname,
  "../backend/web/assets",
);
const source = path.join(__dirname, "node_modules/markdown-it");
fs.copyFileSync(
  path.join(source, "dist/browser/markdown-it.umd.min.js"),
  path.join(destination, "markdown-it.min.js"),
);
fs.copyFileSync(
  path.join(source, "LICENSE"),
  path.join(destination, "markdown-it.LICENSE.txt"),
);
