import { useSyncExternalStore } from "react";

/** Per-machine UI conveniences (recent searches, texture). Storage may be unavailable. */

const KEY = "deepfind.prefs.v1";

export interface Prefs {
  recent: string[];
  motion: boolean;
  texture: boolean;
}

const DEFAULTS: Prefs = { recent: [], motion: true, texture: true };

export function loadPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...DEFAULTS, ...JSON.parse(raw) } : { ...DEFAULTS };
  } catch {
    return { ...DEFAULTS };
  }
}

export function savePrefs(p: Prefs) {
  try { localStorage.setItem(KEY, JSON.stringify(p)); } catch { /* private mode etc. */ }
}

export function pushRecent(p: Prefs, q: string): Prefs {
  const t = q.trim();
  if (t.length < 2) return p;
  return { ...p, recent: [t, ...p.recent.filter((r) => r.toLowerCase() !== t.toLowerCase())].slice(0, 8) };
}

// ---- tiny shared store --------------------------------------------------------

let current = loadPrefs();
const subs = new Set<() => void>();

export function setPrefs(update: (p: Prefs) => Prefs) {
  current = update(current);
  savePrefs(current);
  subs.forEach((f) => f());
}

export function usePrefs(): Prefs {
  return useSyncExternalStore(
    (f) => { subs.add(f); return () => subs.delete(f); },
    () => current,
  );
}
