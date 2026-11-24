import path from "node:path";
import { app, BrowserWindow, Menu } from "electron";
import { TrafficDatabase } from "./db";
import { registerIpcHandlers } from "./ipc";

let mainWindow: BrowserWindow | null = null;
let trafficDb: TrafficDatabase | null = null;

async function createWindow(): Promise<void> {
  const isSmoke = process.env.NTS_TRAFFIC_SMOKE_TEST === "1";
  const userDataDir = process.env.NTS_TRAFFIC_DATA_DIR || app.getPath("userData");
  const dbPath = path.join(userDataDir, "nts_traffic.db");
  const defaultRgDir = path.join(userDataDir, "radiograms");

  trafficDb = await TrafficDatabase.open(dbPath, defaultRgDir);
  registerIpcHandlers(trafficDb, () => mainWindow);

  mainWindow = new BrowserWindow({
    width: 1320,
    height: 860,
    minWidth: 960,
    minHeight: 640,
    show: !isSmoke,
    title: "W5CDN NTS Traffic Manager",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: true,
    },
  });

  // Enable right-click native OS spellcheck suggestions on editable fields
  mainWindow.webContents.on("context-menu", (_event, params) => {
    const menuItems: Electron.MenuItemConstructorOptions[] = [];
    if (params.misspelledWord && params.dictionarySuggestions.length > 0) {
      for (const suggestion of params.dictionarySuggestions.slice(0, 6)) {
        menuItems.push({
          label: suggestion,
          click: () => mainWindow?.webContents.replaceMisspelling(suggestion),
        });
      }
      menuItems.push({ type: "separator" });
    }
    if (params.isEditable) {
      menuItems.push(
        { role: "cut" },
        { role: "copy" },
        { role: "paste" },
        { type: "separator" },
        { role: "selectAll" }
      );
    } else if (params.selectionText) {
      menuItems.push({ role: "copy" });
    }
    if (menuItems.length > 0) {
      Menu.buildFromTemplate(menuItems).popup();
    }
  });

  await mainWindow.loadFile(path.join(__dirname, "..", "renderer", "index.html"));
  if (isSmoke) {
    const title = await mainWindow.webContents.executeJavaScript("document.title");
    const opCall = await mainWindow.webContents.executeJavaScript(
      "window.ntsApi.getSettings().then(s => s.operator_call)"
    );
    console.log(`ELECTRON_SMOKE_OK title=${title} op=${opCall}`);
    mainWindow.close();
    return;
  }
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  if (trafficDb) {
    trafficDb.close();
    trafficDb = null;
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
