// Local-only review server for README captures; serves the real UI with synthetic API data.
const http = require("node:http"),
  fs = require("node:fs"),
  path = require("node:path");
const { createAPI } = require("./tests/fixtures.cjs");
const api = createAPI();
const root = path.resolve(__dirname, "../backend/src/improver/web");
http
  .createServer(async (req, res) => {
    try {
      if (req.url.startsWith("/api/v1/")) {
        let body = "";
        for await (const chunk of req) body += chunk;
        const r = await api.handle(req.url, { method: req.method, body });
        res.writeHead(r.status, { "Content-Type": "application/json" });
        res.end(JSON.stringify(await r.json()));
        return;
      }
      const pathname = new URL(req.url, "http://localhost").pathname;
      const file = pathname.startsWith("/app/assets/")
        ? path.join(root, "assets", path.basename(pathname))
        : path.join(root, "app.html");
      if (!fs.existsSync(file)) {
        res.writeHead(404);
        res.end();
        return;
      }
      res.writeHead(200, {
        "Content-Type": file.endsWith(".css")
          ? "text/css"
          : file.endsWith(".js")
            ? "text/javascript"
            : "text/html",
        "Content-Security-Policy":
          "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'",
      });
      res.end(fs.readFileSync(file));
    } catch {
      res.writeHead(500);
      res.end("Preview error");
    }
  })
  .listen(8766, "127.0.0.1", () =>
    console.log("Synthetic WEB preview: http://127.0.0.1:8766/app/"),
  );
