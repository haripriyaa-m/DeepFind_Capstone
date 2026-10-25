import { useState } from "react";
import { askConfirm, pickFolders } from "../lib/desktop";
import { useEngine } from "../lib/engine";
import { fmtInt } from "../lib/files";
import { IcFolder, IcPlus, IcTrash } from "../components/Icons";

function splitPath(p: string) {
  const sep = p.includes("\\") ? "\\" : "/";
  const parts = p.split(sep).filter(Boolean);
  const name = parts.pop() ?? p;
  return { name };
}

export function FoldersView() {
  const { settings, library, saveSettings, toast, refreshLibrary, status } = useEngine();
  const [busy, setBusy] = useState(false);
  const folders = settings?.watched_folders ?? [];
  const total = Math.max(1, library?.total ?? 0);

  const add = async () => {
    const picked = await pickFolders();
    if (!picked.length) return;
    setBusy(true);
    const s = await saveSettings({ watched_folders: Array.from(new Set([...folders, ...picked])) });
    setBusy(false);
    if (s) { toast(picked.length > 1 ? `${picked.length} folders added` : "Folder added — indexing started", "ok"); refreshLibrary(); }
  };

  const remove = async (f: string) => {
    const ok = await askConfirm(
      `Stop watching this folder?\n\n${f}\n\nIts files will be removed from the search index. Nothing is deleted from your disk.`,
      "Remove folder");
    if (!ok) return;
    setBusy(true);
    const s = await saveSettings({ watched_folders: folders.filter((x) => x !== f) });
    setBusy(false);
    if (s) { toast("Folder removed from the index", "ok"); refreshLibrary(); }
  };

  return (
    <section className="view page" aria-label="Folders">
      <header className="page-head">
        <div>
          <h1>Folders</h1>
          <p>DeepFind indexes everything inside these folders and keeps up with changes automatically.</p>
        </div>
        <button className="btn btn-primary" onClick={add} disabled={busy}><IcPlus width={16} height={16} /> Add folder</button>
      </header>

      {folders.length === 0 ? (
        <div className="panel empty-panel">
          <div className="empty-icon"><IcFolder width={26} height={26} /></div>
          <h2>No folders yet</h2>
          <p>Add Documents, Desktop, Pictures or a project folder — anything you search often.</p>
          <button className="btn btn-primary" onClick={add} disabled={busy}><IcPlus width={16} height={16} /> Add your first folder</button>
        </div>
      ) : (
        <div className="panel list-panel">
          <div className="list-head">
            <span>Folder</span><span>Files</span><span>Share of index</span><span />
          </div>
          {folders.map((f) => {
            const { name } = splitPath(f);
            const n = library?.folders?.[f];
            const scanning = status?.current_file?.startsWith(f) || status?.tier2_current?.startsWith(f);
            return (
              <div className="list-row" key={f}>
                <div className="lr-main">
                  <span className="lr-icon"><IcFolder width={18} height={18} /></span>
                  <div className="lr-text">
                    <b>{name}{scanning && <span className="badge live">Indexing</span>}</b>
                    <span className="mono" title={f}>{f}</span>
                  </div>
                </div>
                <span className="lr-num">{n != null ? fmtInt(n) : "—"}</span>
                <div className="lr-share">
                  <div className="bar"><span style={{ width: `${n != null ? Math.max(2, (n / total) * 100) : 0}%` }} /></div>
                  <span>{n != null ? `${Math.round((n / total) * 100)}%` : ""}</span>
                </div>
                <button className="icon-btn danger" onClick={() => remove(f)} disabled={busy}
                  aria-label={`Stop watching ${name}`} title="Stop watching">
                  <IcTrash width={16} height={16} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      <p className="footnote">
        Skipped automatically: <span className="mono">node_modules</span>, <span className="mono">.git</span>,{" "}
        <span className="mono">.venv</span>, build output, partial downloads and Office lock files.
      </p>
    </section>
  );
}
