import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import type { ApiRouter } from "./router";

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".webmanifest": "application/manifest+json",
};

/**
 * Ephemeral HTTP adapter ONLY used by Playwright E2E tests in headless CI.
 * Production desktop runs 100% in-process via Electron's app:// custom protocol (zero HTTP port).
 */
export function startTestHttpServer(
  router: ApiRouter,
  staticDir: string
): Promise<{ url: string; close: () => Promise<void> }> {
  const staticRoot = path.resolve(staticDir);
  const server = http.createServer(async (req, res) => {
    const reqUrl = req.url || "/";
    const parsed = new URL(reqUrl, "http://127.0.0.1");
    const pathname = parsed.pathname;

    if (pathname.startsWith("/api/")) {
      const chunks: Buffer[] = [];
      for await (const chunk of req) {
        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
      }
      const rawBuf = Buffer.concat(chunks);
      const isBinary = pathname === "/api/backup/restore";
      const out = await router.dispatch({
        method: req.method || "GET",
        path: `${pathname}${parsed.search}`,
        body: isBinary ? undefined : rawBuf.toString("utf-8"),
        binaryBody: isBinary ? rawBuf : undefined,
      });
      const headers: Record<string, string> = { ...(out.headers || {}), "Content-Type": out.contentType };
      res.writeHead(out.status, headers);
      if (out.status === 204) {
        res.end();
        return;
      }
      if (out.isBinary && Array.isArray(out.body)) {
        res.end(Buffer.from(out.body));
        return;
      }
      res.end(
        out.contentType.includes("application/json") && typeof out.body !== "string"
          ? JSON.stringify(out.body)
          : String(out.body ?? "")
      );
      return;
    }

    let rel = "index.html";
    if (pathname === "/favicon.ico") rel = "icon.ico";
    else if (pathname.startsWith("/static/")) rel = pathname.slice("/static/".length);
    const target = path.resolve(staticRoot, rel);
    if (!target.startsWith(staticRoot + path.sep) || !fs.existsSync(target)) {
      res.writeHead(404);
      res.end("Not found");
      return;
    }
    const ext = path.extname(target).toLowerCase();
    res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
    res.end(fs.readFileSync(target));
  });

  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address() as { port: number };
      resolve({
        url: `http://127.0.0.1:${addr.port}`,
        close: () => new Promise<void>((r) => server.close(() => r())),
      });
    });
  });
}
