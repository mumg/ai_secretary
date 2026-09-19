'use strict';
const fs = require('node:fs/promises');
const path = require('node:path');

function connectionOrigin(text) {
  const match = text.match(/^URL=(.+)$/m);
  if (!match) throw Error('Не найден адрес установленного сервера');
  const url = new URL(match[1].trim());
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port ||
      url.username || url.password || !['/app', '/app/'].includes(url.pathname) || url.search || url.hash)
    throw Error('Недопустимый адрес локального сервера');
  return url.origin;
}
class WindowsServices {
  constructor({ root = path.resolve(process.resourcesPath, '../..'), progress = () => {} } = {}) {
    this.root = root;
    this.data = path.join(process.env.ProgramData || 'C:\\ProgramData', 'AI Secretary');
    this.progress = progress;
  }
  async ensure() {
    // Read the non-secret shortcut in Program Files. ProgramData secrets remain
    // accessible only to Administrators/SYSTEM/LocalService; the UI runs unelevated.
    const origin = connectionOrigin(await fs.readFile(path.join(this.root, 'Open.url'), 'utf8'));
    const version = (await fs.readFile(path.join(this.root, 'version'), 'utf8')).trim();
    this.progress('Подключение к службам Windows…');
    for (let i = 0; i < 60; i++) {
      try {
        const live = await fetch(`${origin}/health/live`, { signal: AbortSignal.timeout(2000) }).then(r => r.json());
        const ready = await fetch(`${origin}/health/ready`, { signal: AbortSignal.timeout(2000) }).then(r => r.json());
        if (live.version === version && ready.status === 'ready') return origin;
      } catch { /* Windows SCM may still be starting the services. */ }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    throw Error('Службы AISecretary не запустились. Проверьте их в «Службах Windows» или повторите установку.');
  }
}
module.exports = { WindowsServices, connectionOrigin };
