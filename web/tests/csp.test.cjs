const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');

const root = path.resolve(__dirname, '../..');
const chrome = [process.env.CHROME_BIN,
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome',
].find(file => file && fs.existsSync(file));

test('browser renders the QR under production CSP while data scripts remain blocked', {
  skip: chrome ? false : 'Set CHROME_BIN to run the browser CSP regression test',
}, async t => {
  const policy = process.env.CSP_HEADER_FILE
    ? fs.readFileSync(process.env.CSP_HEADER_FILE, 'utf8').trim()
    : fs.readFileSync(path.join(root, 'infra/caddy/Caddyfile'), 'utf8')
      .match(/Content-Security-Policy "([^"]+)"/)[1].replaceAll('{$PUBLIC_HOST:localhost}', 'localhost');
  const qr = fs.readFileSync(path.join(root, 'android/app/src/androidTest/assets/mobile-identity/test-identity.png')).toString('base64');
  const server = http.createServer((req, res) => {
    if (req.url === '/check.js') {
      res.writeHead(200, { 'Content-Type': 'text/javascript' });
      res.end(`
        document.addEventListener('securitypolicyviolation', event => {
          if (event.effectiveDirective === 'script-src-elem' && event.blockedURI === 'data')
            document.body.dataset.scriptBlocked = 'yes';
        });
        const image = new Image();
        image.onload = () => { document.body.dataset.qrLoaded = image.naturalWidth > 0 ? 'yes' : 'no'; };
        image.onerror = () => { document.body.dataset.qrLoaded = 'no'; };
        image.src = 'data:image/png;base64,${qr}';
        const script = document.createElement('script');
        script.src = 'data:text/javascript,document.body.dataset.unsafeScript="executed"';
        document.head.append(script);
      `);
      return;
    }
    res.writeHead(200, { 'Content-Type': 'text/html', 'Content-Security-Policy': policy });
    res.end('<!doctype html><html><head><script src="/check.js" defer></script></head><body></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'secretary-csp-chrome-'));
  t.after(() => { server.close(); fs.rmSync(profile, { recursive: true, force: true }); });
  const { stdout } = await promisify(execFile)(chrome, [
    '--headless', '--disable-gpu', '--disable-background-networking', '--no-first-run',
    `--user-data-dir=${profile}`, '--virtual-time-budget=2000', '--dump-dom',
    `http://127.0.0.1:${server.address().port}/`,
  ], { timeout: 30_000, maxBuffer: 1_000_000 });
  assert.match(stdout, /data-qr-loaded="yes"/);
  assert.match(stdout, /data-script-blocked="yes"/);
  assert.doesNotMatch(stdout, /data-unsafe-script="executed"/);
});
