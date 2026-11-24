import { contextBridge, ipcRenderer } from "electron";

const desktopApi = {
  saveTextFile: (filename: string, content: string, dialogKind = "txt") =>
    ipcRenderer.invoke("desktop:saveTextFile", { filename, content, dialogKind }),
};

contextBridge.exposeInMainWorld("desktopApi", desktopApi);
