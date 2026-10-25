import { ReactNode, useEffect, useState } from "react";
import { IcFolder, IcPulse, IcRefresh, IcSearch, IcSliders } from "./components/Icons";
import { EngineProvider, useEngine } from "./lib/engine";
import { fmtInt } from "./lib/files";
import { ActivityView, engineState } from "./views/ActivityView";
import { FoldersView } from "./views/FoldersView";
import { SearchView } from "./views/SearchView";
import { SettingsView } from "./views/SettingsView";

declare global {
  namespace JSX {
    interface IntrinsicElements {
      [elementName: string]: any;
    }
  }
}

type ViewId = "search" | "folders" | "activity" | "settings";

const NAV: { id: ViewId; label: string; hint: string; icon: ReactNode }[] = [
  { id: "search", label: "Search", hint: "Find a file", icon: <IcSearch width={18} height={18} /> },
  { id: "folders", label: "Folders", hint: "Choose which folders to search", icon: <IcFolder width={18} height={18} /> },
  { id: "activity", label: "Activity", hint: "See what DeepFind is doing", icon: <IcPulse width={18} height={18} /> },
  { id: "settings", label: "Settings", hint: "Limits, AI models and updates", icon: <IcSliders width={18} height={18} /> },
];

/** Small app logo: blue rounded square with a magnifier. */
function Logo({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" className="logo-mark">
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <circle cx="14.5" cy="14.5" r="6.2" fill="none" stroke="#fff" strokeWidth="2.6" />
      <path d="m19.4 19.4 5.2 5.2" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" />
    </svg>
  );
}

/** Top bar: logo, the four sections, and a live status pill. */
function TopBar({ view, setView }: { view: ViewId; setView: (v: ViewId) => void }) {
  const { status, settings, library, models, update } = useEngine();
  const st = engineState(status, settings ?? undefined);
  const needsModels = models && !models.installed;
  return (
    <header className="topbar">
      <div className="tb-brand">
        <Logo />
        <span>DeepFind</span>
      </div>
      <nav className="tb-nav" aria-label="Main">
        {NAV.map(({ id, label, hint, icon }, i) => (
          <button key={id} className={`tb-item ${view === id ? "on" : ""}`} onClick={() => setView(id)}
            aria-current={view === id ? "page" : undefined} title={`${hint} (Alt+${i + 1})`}>
            {icon}
            <span>{label}</span>
            {id === "settings" && (update?.available || needsModels) && (
              <i className="tb-flag" title={update?.available ? `Version ${update.latest?.version} is available` : "AI models are not installed yet"} />
            )}
          </button>
        ))}
      </nav>
      <button className={`tb-status tone-${st.tone}`} onClick={() => setView("activity")} title={st.why}>
        <i />
        <span>{st.word}</span>
        <span className="tb-dim">{fmtInt(library?.total ?? status?.db_rows)} files</span>
      </button>
    </header>
  );
}

function Boot() {
  const { phase, bootNote, error, retry } = useEngine();
  const failed = phase === "failed";
  return (
    <div className="boot" role="status">
      <div className="boot-card">
        <Logo size={56} />
        <span className="boot-name">DeepFind</span>
        <p className="boot-note">{failed ? "The search engine couldn't start." : bootNote || "Getting things ready…"}</p>
        {failed ? (
          <div className="boot-error">
            <code>{error}</code>
            <p>Close every DeepFind window and open it again. The log is in
              <span className="mono"> %LOCALAPPDATA%\DeepFind\logs</span>.</p>
            <button className="btn btn-primary" onClick={retry}><IcRefresh width={16} height={16} /> Try again</button>
          </div>
        ) : (
          <span className="boot-line" aria-hidden="true" />
        )}
      </div>
    </div>
  );
}

function Toasts() {
  const { toasts } = useEngine();
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((t) => <div key={t.id} className={`toast ${t.kind}`}>{t.text}</div>)}
    </div>
  );
}

function Shell() {
  const { phase } = useEngine();
  const [view, setView] = useState<ViewId>("search");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey && !e.ctrlKey) {
        const n = NAV[Number(e.key) - 1];
        if (n) { e.preventDefault(); setView(n.id); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const block = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest("input, textarea, .selectable")) e.preventDefault();
    };
    if (!import.meta.env.DEV) window.addEventListener("contextmenu", block);
    return () => window.removeEventListener("contextmenu", block);
  }, []);

  const ready = phase === "ready";

  return (
    <div className="app">
      {ready ? (
        <>
          <TopBar view={view} setView={setView} />
          <div className="stage">
            <main className="content">
              <div hidden={view !== "search"} className="view-slot">
                <SearchView active={view === "search"} goFolders={() => setView("folders")} />
              </div>
              {view === "folders" && <div className="view-slot"><FoldersView /></div>}
              {view === "activity" && <div className="view-slot"><ActivityView /></div>}
              {view === "settings" && <div className="view-slot"><SettingsView /></div>}
            </main>
          </div>
        </>
      ) : (
        <Boot />
      )}
      <Toasts />
    </div>
  );
}

export default function App() {
  return (
    <EngineProvider>
      <Shell />
    </EngineProvider>
  );
}