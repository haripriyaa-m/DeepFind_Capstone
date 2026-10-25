/**
 * Bridge to the desktop shell (desktop/main.py, pywebview + WebView2).
 * The shell injects `window.pywebview.api`; in a plain browser (vite dev) every
 * function falls back to a browser equivalent so the UI still works.
 */

interface ShellApi {
  pick_folders(): Promise<string[] | null>;
  confirm(message: string, title: string): Promise<boolean>;
  app_info(): Promise<AppInfo>;
  open_path(which: "logs" | "data" | "models"): Promise<boolean>;
  open_url(url: string): Promise<boolean>;
}

export interface AppInfo {
  version: string;
  data_dir: string;
  models_dir: string;
  log_dir: string;
  platform: string;
}

declare global {
  interface Window { pywebview?: { api: ShellApi } }
}

let ready: Promise<ShellApi | null> | null = null;

/** Resolves with the shell API, or null when running in a normal browser. */
export function shell(): Promise<ShellApi | null> {
  if (!ready) {
    ready = new Promise((resolve) => {
      if (window.pywebview?.api) return resolve(window.pywebview.api);
      const done = () => resolve(window.pywebview?.api ?? null);
      window.addEventListener("pywebviewready", done, { once: true });
      // pywebview injects early; if nothing arrives we're in a browser
      setTimeout(done, 1500);
    });
  }
  return ready;
}

export const inDesktop = () => !!window.pywebview;

export async function pickFolders(): Promise<string[]> {
  const s = await shell();
  if (!s) {
    const p = window.prompt("Folder path to watch");
    return p ? [p.trim()] : [];
  }
  return (await s.pick_folders()) ?? [];
}

export async function askConfirm(message: string, title = "DeepFind"): Promise<boolean> {
  const s = await shell();
  return s ? s.confirm(message, title) : window.confirm(message);
}

export async function appInfo(): Promise<AppInfo | null> {
  const s = await shell();
  return s ? s.app_info() : null;
}

export async function openPath(which: "logs" | "data" | "models") {
  const s = await shell();
  if (s) await s.open_path(which);
}

/** Open an https link in the default browser (window.open does nothing useful inside WebView2). */
export async function openUrl(url: string) {
  const s = await shell();
  if (s) await s.open_url(url);
  else window.open(url, "_blank", "noopener");
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}
