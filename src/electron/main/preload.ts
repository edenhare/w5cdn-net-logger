import { contextBridge, ipcRenderer } from "electron";

const desktopApi = {
  saveTextFile: (filename: string, content: string, dialogKind = "txt") =>
    ipcRenderer.invoke("desktop:saveTextFile", { filename, content, dialogKind }),
};

contextBridge.exposeInMainWorld("desktopApi", desktopApi);
// Backwards-compatible alias for existing saveTextContent() calls in app.js
contextBridge.exposeInMainWorld("pywebview", {
  api: {
    save_text_file: (filename: string, content: string, dialogKind = "txt") =>
      ipcRenderer.invoke("desktop:saveTextFile", { filename, content, dialogKind }),
  },
});
