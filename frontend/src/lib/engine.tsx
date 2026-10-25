/**
 * Engine context: waits for the backend to be ready, polls status/library/models,
 * owns settings, and exposes toasts. One place for every background timer.
 */
import { createContext, ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, ApiError, Library, ModelsInfo, Settings, Status, UpdateInfo } from "./api";

export type Phase = "booting" | "ready" | "failed";
export interface Toast { id: number; text: string; kind: "info" | "ok" | "error" }
export interface Sample { t: number; cpu: number; ram: number }

interface EngineCtx {
  phase: Phase;
  bootNote: string;
  error: string | null;
  version: string | null;
  status: Status | null;
  settings: Settings | null;
  library: Library | null;
  models: ModelsInfo | null;
  update: UpdateInfo | null;
  setUpdate: (u: UpdateInfo) => void;
  history: Sample[];
  saveSettings: (patch: Partial<Settings>) => Promise<Settings | null>;
  refreshLibrary: () => void;
  refreshModels: () => Promise<void>;
  setModels: (m: ModelsInfo) => void;
  retry: () => void;
  toast: (text: string, kind?: Toast["kind"]) => void;
  toasts: Toast[];
}

const Ctx = createContext<EngineCtx | null>(null);

/** Semantic search works when the text model is on disk and loaded without error. */
export function semanticReady(status: Status | null, models: ModelsInfo | null): boolean {
  if (status?.semantic_error) return false;
  if (models) return models.parts.text;
  return !!status?.semantic_available;
}
export const useEngine = () => {
  const c = useContext(Ctx);
  if (!c) throw new Error("useEngine outside EngineProvider");
  return c;
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const HISTORY = 60;

export function EngineProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>("booting");
  const [bootNote, setBootNote] = useState("Starting the search engine…");
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState<string | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [library, setLibrary] = useState<Library | null>(null);
  const [models, setModels] = useState<ModelsInfo | null>(null);
  const [update, setUpdate] = useState<UpdateInfo | null>(null);
  const announced = useRef("");
  const [history, setHistory] = useState<Sample[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [attempt, setAttempt] = useState(0);
  const toastId = useRef(0);

  const toast = useCallback((text: string, kind: Toast["kind"] = "info") => {
    const id = ++toastId.current;
    setToasts((t) => [...t.slice(-3), { id, text, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 5200 : 2600);
  }, []);

  // ---- boot: wait for /api/health ready ---------------------------------------
  useEffect(() => {
    let dead = false;
    (async () => {
      setPhase("booting");
      setError(null);
      const t0 = Date.now();
      while (!dead) {
        try {
          const h = await api.health();
          setVersion(h.version ?? null);
          if (h.error) { setError(h.error); setPhase("failed"); return; }
          if (h.ready) break;
          setBootNote("Opening your index…");
        } catch {
          const waited = (Date.now() - t0) / 1000;
          setBootNote(waited > 6 ? "Still starting — the first launch takes a little longer…" : "Starting the search engine…");
          if (waited > 90) { setError("The engine did not respond within 90 seconds."); setPhase("failed"); return; }
        }
        await sleep(500);
      }
      if (dead) return;
      try { setSettings(await api.settings()); } catch { /* retried by views */ }
      try { setModels(await api.models()); } catch { /* optional */ }
      setPhase("ready");
    })();
    return () => { dead = true; };
  }, [attempt]);

  // ---- status polling -----------------------------------------------------------
  useEffect(() => {
    if (phase !== "ready") return;
    let dead = false;
    let timer: ReturnType<typeof setTimeout>;
    let last: Status | null = null;
    const tick = async () => {
      try {
        const s = await api.status();
        if (dead) return;
        last = s;
        setStatus(s);
        if (typeof s.cpu_percent === "number") {
          setHistory((h) => [...h.slice(-(HISTORY - 1)), { t: Date.now(), cpu: s.cpu_percent!, ram: s.ram_percent ?? 0 }]);
        }
      } catch (e) {
        if (e instanceof ApiError && e.status === 500) { setError(e.message); setPhase("failed"); return; }
      }
      const busy = last && !last.idle;
      timer = setTimeout(tick, document.hidden ? 5000 : busy ? 1200 : 2000);
    };
    tick();
    return () => { dead = true; clearTimeout(timer); };
  }, [phase]);

  const refreshLibrary = useCallback(() => {
    api.library().then(setLibrary).catch(() => {});
  }, []);

  useEffect(() => {
    if (phase !== "ready") return;
    refreshLibrary();
    const iv = setInterval(() => { if (!document.hidden) refreshLibrary(); }, 8000);
    return () => clearInterval(iv);
  }, [phase, refreshLibrary]);

  const refreshModels = useCallback(async () => {
    try { setModels(await api.models()); } catch { /* ignore */ }
  }, []);

  // poll faster while a model download is running
  useEffect(() => {
    if (models?.job.state !== "running" && models?.tesseract_job?.state !== "running") return;
    const iv = setInterval(refreshModels, 1000);
    return () => clearInterval(iv);
  }, [models?.job.state, models?.tesseract_job?.state, refreshModels]);

  // ---- updates: the check itself runs in the engine (once a day, if enabled); we just look ----
  useEffect(() => {
    if (phase !== "ready") return;
    let dead = false;
    const tick = async () => {
      try {
        const u = await api.update();
        if (dead) return;
        setUpdate(u);
        if (u.just_updated) {
          toast(`DeepFind updated to version ${u.current}`, "ok");
          api.ackUpdate().catch(() => {});
        }
        if (u.available && u.latest && announced.current !== u.latest.version) {
          announced.current = u.latest.version;
          toast(`Version ${u.latest.version} is available — open Settings to update`, "info");
        }
      } catch { /* engine busy; try again next minute */ }
    };
    tick();
    const iv = setInterval(tick, 60000);
    return () => { dead = true; clearInterval(iv); };
  }, [phase, toast]);

  // follow the download closely while it runs
  useEffect(() => {
    if (update?.state !== "downloading") return;
    const iv = setInterval(() => { api.update().then(setUpdate).catch(() => {}); }, 700);
    return () => clearInterval(iv);
  }, [update?.state]);

  const saveSettings = useCallback(async (patch: Partial<Settings>) => {
    try {
      const s = await api.saveSettings(patch);
      setSettings(s);
      if (s.invalid_folders?.length) toast(`Skipped missing folder: ${s.invalid_folders.join(", ")}`, "error");
      return s;
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not save settings", "error");
      return null;
    }
  }, [toast]);

  const retry = useCallback(() => setAttempt((a) => a + 1), []);

  return (
    <Ctx.Provider value={{
      phase, bootNote, error, version, status, settings, library, models, update, setUpdate, history,
      saveSettings, refreshLibrary, refreshModels, setModels, retry, toast, toasts,
    }}>
      {children}
    </Ctx.Provider>
  );
}