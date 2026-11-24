import { BrowserWindow, dialog, ipcMain, shell } from "electron";
import {
  ARL_TEXTS,
  buildPrintableHtml,
  buildServiceMessageDraft,
  formatArrlBookText,
  formatArrlRadiogramText,
} from "../shared/arrl";
import type {
  AppSettings,
  BookCreateInput,
  QueueView,
  RadiogramInput,
  SvcTemplateKind,
} from "../shared/types";
import type { TrafficDatabase } from "./db";

export function registerIpcHandlers(
  db: TrafficDatabase,
  getMainWindow: () => BrowserWindow | null
): void {
  ipcMain.handle("settings:get", () => db.getSettings());
  ipcMain.handle("settings:update", (_e, patch: Partial<AppSettings>) => db.updateSettings(patch));
  ipcMain.handle("settings:pickFolder", async () => {
    const win = getMainWindow();
    const opts: Electron.OpenDialogOptions = {
      title: "Select Shared Radiograms Routing Folder",
      properties: ["openDirectory", "createDirectory"],
    };
    const res = win ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts);
    if (res.canceled || !res.filePaths.length) return null;
    return res.filePaths[0];
  });
  ipcMain.handle("settings:openFolder", async () => {
    const { radiograms_dir } = db.getSettings();
    await shell.openPath(radiograms_dir);
    return radiograms_dir;
  });

  ipcMain.handle("arl:list", () => ARL_TEXTS);

  ipcMain.handle("rg:list", (_e, opts?: { queue?: QueueView; search?: string; bookId?: number | null }) => {
    return db.listRadiograms(opts?.queue || "all", opts?.search || "", opts?.bookId ?? null);
  });
  ipcMain.handle("rg:counts", () => db.getQueueCounts());
  ipcMain.handle("rg:get", (_e, id: number) => db.getRadiogram(id));
  ipcMain.handle("rg:create", (_e, input: RadiogramInput) => db.createRadiogram(input));
  ipcMain.handle("rg:update", (_e, id: number, patch: RadiogramInput) => db.updateRadiogram(id, patch));
  ipcMain.handle("rg:delete", (_e, id: number) => db.deleteRadiogram(id));
  ipcMain.handle("rg:logs", (_e, id: number) => db.getHandlingLog(id));
  ipcMain.handle("rg:formatText", (_e, id: number) => {
    const rg = db.getRadiogram(id);
    if (!rg) throw new Error(`Radiogram #${id} not found`);
    return formatArrlRadiogramText(rg);
  });
  ipcMain.handle("rg:buildSvcDraft", (_e, id: number, kind: SvcTemplateKind) => {
    const rg = db.getRadiogram(id);
    if (!rg) throw new Error(`Radiogram #${id} not found`);
    const s = db.getSettings();
    return buildServiceMessageDraft(rg, kind, s.operator_call, s.operator_qth);
  });

  ipcMain.handle("book:list", () => db.listBooks());
  ipcMain.handle("book:create", (_e, input: BookCreateInput) => db.createBook(input));
  ipcMain.handle("book:formatText", (_e, bookId: number) => {
    const book = db.listBooks().find((b) => b.id === bookId);
    if (!book) throw new Error(`Book #${bookId} not found`);
    return formatArrlBookText(book.title, db.getBookMembers(bookId));
  });

  ipcMain.handle("stats:sar", (_e, month: string) => db.getMonthlySarStats(month));

  ipcMain.handle("print:radiograms", async (_e, ids: number[], layout: "1up" | "2up" = "1up") => {
    const items = ids
      .map((id) => db.getRadiogram(id))
      .filter((item): item is NonNullable<typeof item> => item !== null);
    if (!items.length) throw new Error("No radiograms selected for printing");

    const html = buildPrintableHtml(items, layout);
    const printWin = new BrowserWindow({
      show: false,
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false },
    });
    await printWin.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
    return await new Promise<boolean>((resolve) => {
      printWin.webContents.print({ silent: false, printBackground: true }, (success) => {
        if (success) {
          for (const item of items) {
            db.addHandlingLog(item.id, "PRINTED", "", layout.toUpperCase());
          }
        }
        printWin.close();
        resolve(success);
      });
    });
  });
}
