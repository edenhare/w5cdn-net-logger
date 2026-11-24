import fs from "node:fs";
import path from "node:path";
import { app, BrowserWindow, dialog, ipcMain, Menu, protocol, shell } from "electron";
import { APP_TITLE } from "../shared/defaults";
import { loadConfigFile, NetLoggerDb } from "./db";
import { ApiRouter } from "./router";

protocol.registerSchemesAsPrivileged([
  {
    scheme: "app",
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
      corsEnabled: true,
      stream: true,
    },
  },
]);

const MIME_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".webmanifest": "application/manifest+json",
};

let mainWindow: BrowserWindow | null = null;
let db: NetLoggerDb | null = null;

function resolveDataDir(): { dataDir: string; sharedDir: string | null } {
  if (process.env.NET_LOGGER_DATA_DIR) {
    return { dataDir: path.resolve(process.env.NET_LOGGER_DATA_DIR), sharedDir: null };
  }
  if (app.isPackaged) {
    const suiteRoot = process.env.W5CDN_SUITE_DIR
      ? path.resolve(process.env.W5CDN_SUITE_DIR)
      : path.join(app.getPath("appData"), "W5CDN");
    const sharedDir = path.join(suiteRoot, "shared");
    const suiteAppDir = path.join(suiteRoot, "Net-Logger");
    const legacyDir = path.join(app.getPath("appData"), "w5cdn-net-logger");
    // Use W5CDN/Net-Logger by default; seamlessly preserve an existing legacy DB if Net-Logger has none yet
    const dataDir =
      !fs.existsSync(path.join(suiteAppDir, "net_logger.db")) && fs.existsSync(path.join(legacyDir, "net_logger.db"))
        ? legacyDir
        : suiteAppDir;
    return { dataDir, sharedDir };
  }
  return { dataDir: process.cwd(), sharedDir: null };
}

if (app.isPackaged && !process.env.NET_LOGGER_DATA_DIR) {
  const { dataDir } = resolveDataDir();
  fs.mkdirSync(dataDir, { recursive: true });
  app.setPath("userData", dataDir);
}

function resolveStaticDir(): string {
  const packagedDist = path.join(__dirname, "..", "static");
  if (fs.existsSync(packagedDist)) return packagedDist;
  return path.resolve("src", "static");
}

function registerAppProtocol(router: ApiRouter, staticDir: string): void {
  const staticRoot = path.resolve(staticDir);

  protocol.handle("app", async (request) => {
    const url = new URL(request.url);
    const pathname = url.pathname;

    // 1. In-process API dispatch (zero localhost HTTP port!)
    if (pathname.startsWith("/api/")) {
      let body: unknown = undefined;
      let binaryBody: Buffer | undefined = undefined;
      if (request.method !== "GET" && request.method !== "HEAD") {
        const contentType = (request.headers.get("content-type") || "").toLowerCase();
        if (pathname === "/api/backup/restore" || contentType.includes("application/zip") || contentType.includes("octet-stream")) {
          const ab = await request.arrayBuffer();
          binaryBody = Buffer.from(ab);
        } else {
          const text = await request.text();
          if (text) body = text;
        }
      }

      const res = await router.dispatch({
        method: request.method,
        path: `${pathname}${url.search}`,
        body,
        binaryBody,
      });

      const headers = new Headers(res.headers || {});
      headers.set("Content-Type", res.contentType);
      if (res.status === 204) {
        return new Response(null, { status: 204, headers });
      }
      if (res.isBinary && Array.isArray(res.body)) {
        return new Response(new Uint8Array(res.body), { status: res.status, headers });
      }
      const payload =
        res.contentType.includes("application/json") && typeof res.body !== "string"
          ? JSON.stringify(res.body)
          : String(res.body ?? "");
      return new Response(payload, { status: res.status, headers });
    }

    // 2. Static asset serving from dist/static
    let rel = "index.html";
    if (pathname === "/favicon.ico") {
      rel = "icon.ico";
    } else if (pathname.startsWith("/static/")) {
      rel = pathname.slice("/static/".length);
    } else if (pathname !== "/" && pathname !== "/index.html") {
      rel = pathname.replace(/^\/+/, "");
    }

    const target = path.resolve(staticRoot, rel);
    if (!target.startsWith(staticRoot + path.sep) && target !== path.join(staticRoot, "index.html")) {
      return new Response("Forbidden", { status: 403 });
    }
    if (!fs.existsSync(target) || !fs.statSync(target).isFile()) {
      return new Response("Not Found", { status: 404 });
    }
    const ext = path.extname(target).toLowerCase();
    return new Response(fs.readFileSync(target), {
      status: 200,
      headers: { "Content-Type": MIME_TYPES[ext] || "application/octet-stream" },
    });
  });
}

function registerDesktopIpc(): void {
  ipcMain.handle(
    "desktop:saveTextFile",
    async (_event, args: { filename: string; content: string; dialogKind?: string }) => {
      const filtersByKind: Record<string, Electron.FileFilter[]> = {
        csv: [{ name: "CSV Spreadsheet", extensions: ["csv"] }],
        adi: [{ name: "ADIF Amateur Radio Log", extensions: ["adi", "adif"] }],
        txt: [{ name: "Text File", extensions: ["txt"] }],
      };
      const filters = filtersByKind[args.dialogKind || "txt"] || filtersByKind.txt;
      const opts: Electron.SaveDialogOptions = {
        defaultPath: args.filename || "export.txt",
        filters: [...filters, { name: "All Files", extensions: ["*"] }],
      };
      const res = mainWindow ? await dialog.showSaveDialog(mainWindow, opts) : await dialog.showSaveDialog(opts);
      if (res.canceled || !res.filePath) {
        return { saved: false, cancelled: true };
      }
      fs.writeFileSync(res.filePath, args.content ?? "", "utf-8");
      return { saved: true, path: res.filePath };
    }
  );
}

async function createWindow(): Promise<void> {
  const isSmoke = process.env.NET_LOGGER_SMOKE_TEST === "1";
  const { dataDir, sharedDir } = resolveDataDir();
  fs.mkdirSync(dataDir, { recursive: true });
  loadConfigFile(path.join(dataDir, "config.txt"));
  loadConfigFile(path.join(dataDir, ".env"));
  if (sharedDir) {
    fs.mkdirSync(path.join(sharedDir, "radiograms"), { recursive: true });
    fs.mkdirSync(path.join(sharedDir, "outbox"), { recursive: true });
    loadConfigFile(path.join(sharedDir, "config.txt"));
    if (!process.env.RADIOGRAMS_DIR && !fs.existsSync(path.join(dataDir, "radiograms"))) {
      process.env.RADIOGRAMS_DIR = path.join(sharedDir, "radiograms");
    }
  }

  const dbPath = process.env.NET_LOGGER_DB
    ? path.resolve(process.env.NET_LOGGER_DB)
    : path.join(dataDir, "net_logger.db");

  db = await NetLoggerDb.open(dbPath, dataDir);
  const router = new ApiRouter(db);
  const staticDir = resolveStaticDir();

  registerAppProtocol(router, staticDir);
  registerDesktopIpc();

  const iconPath = path.join(staticDir, process.platform === "win32" ? "icon.ico" : "icon.png");

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1024,
    minHeight: 680,
    show: !isSmoke,
    title: APP_TITLE,
    icon: fs.existsSync(iconPath) ? iconPath : undefined,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: true,
    },
  });

  // Safe external link handling (only http/https)
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const parsed = new URL(url);
      if (parsed.protocol === "http:" || parsed.protocol === "https:") {
        void shell.openExternal(url);
      }
    } catch {
      // Ignore malformed URLs
    }
    return { action: "deny" };
  });

  // Native right-click spellcheck suggestions + clipboard context menu
  mainWindow.webContents.on("context-menu", (_event, params) => {
    const items: Electron.MenuItemConstructorOptions[] = [];
    if (params.misspelledWord && params.dictionarySuggestions.length > 0) {
      for (const suggestion of params.dictionarySuggestions.slice(0, 6)) {
        items.push({
          label: suggestion,
          click: () => mainWindow?.webContents.replaceMisspelling(suggestion),
        });
      }
      items.push({ type: "separator" });
    }
    if (params.isEditable) {
      items.push(
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { type: "separator" },
        { role: "selectAll" }
      );
    } else if (params.selectionText) {
      items.push({ role: "copy" });
    }
    if (items.length > 0) {
      Menu.buildFromTemplate(items).popup();
    }
  });

  const rendererErrors: string[] = [];
  if (isSmoke) {
    mainWindow.webContents.on("console-message", (details) => {
      if (details.level === "error") rendererErrors.push(details.message);
    });
  }

  await mainWindow.loadURL("app://local/");

  if (isSmoke) {
    const result = await mainWindow.webContents.executeJavaScript(`
      (async () => {
        const health = await fetch('/api/health').then(r => r.json());
        const cfg = await fetch('/api/config').then(r => r.json());
        const net = await fetch('/api/nets', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: 'Electron Smoke Net', net_type: 'ARES', frequency: '147.000', mode: 'FM' }),
        }).then(r => r.json());
        const ci = await fetch('/api/nets/' + net.id + '/checkins', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ callsign: 'WM5Q', name: 'Wayne', qth: 'Whitehouse, TX', has_traffic: true, comments: '[16:12Z] Smoke traffic' }),
        }).then(r => r.json());
        const report = await fetch('/api/nets/' + net.id + '/export/summary').then(r => r.text());
        return {
          title: document.title,
          healthOk: health.ok,
          runtime: cfg.runtime,
          netId: net.id,
          ciCall: ci.callsign,
          hasReportLine: report.includes('WM5Q') && report.includes('Smoke traffic'),
        };
      })()
    `);
    if (!result.healthOk || !result.hasReportLine || rendererErrors.length > 0) {
      console.error("ELECTRON_SMOKE_FAILED", { result, rendererErrors });
      process.exitCode = 1;
    } else {
      console.log(
        `ELECTRON_NET_LOGGER_SMOKE_OK title=${result.title} health=${result.healthOk} runtime=${result.runtime} net=${result.netId} ci=${result.ciCall}`
      );
    }
    mainWindow.close();
    return;
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  if (db) {
    db.close();
    db = null;
  }
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    void createWindow();
  }
});
