import { ReactNode, useEffect, useRef, useState } from "react";
import { api, IndexSchedule, UpdateInfo } from "../lib/api";
import { AppInfo, appInfo, askConfirm, openPath, openUrl } from "../lib/desktop";
import { useEngine } from "../lib/engine";
import { setPrefs, usePrefs } from "../lib/prefs";
import { IcDownload, IcExternal, IcRefresh } from "../components/Icons";
import { ModelsSettings } from "./ModelsSettings";
import { SCHEDULES, fmtIn, fmtWhen, scheduleOf } from "../lib/schedule";

function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button role="switch" aria-checked={on} aria-label={label} className={`toggle ${on ? "on" : ""}`}
      onClick={() => onChange(!on)}>
      <span className={on ? "" : "cur"}>Off</span><span className={on ? "cur" : ""}>On</span>
    </button>
  );
}

function Slider({ label, hint, value, min, max, onCommit }: {
  label: string; hint: string; value: number; min: number; max: number; onCommit: (v: number) => void;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  const t = useRef<ReturnType<typeof setTimeout>>(undefined);
  const set = (n: number) => {
    setV(n);
    clearTimeout(t.current);
    t.current = setTimeout(() => onCommit(n), 450);
  };
  const id = `sl-${label.replace(/\s/g, "")}`;
  return (
    <Row label={label} hint={hint} htmlFor={id}>
      <div className="slider">
        <input id={id} type="range" min={min} max={max} step={5} value={v}
          onChange={(e) => set(Number(e.target.value))}
          style={{ ["--p" as string]: `${((v - min) / (max - min)) * 100}%` }} />
        <output htmlFor={id}>{v}%</output>
      </div>
    </Row>
  );
}

/** One settings line: text on the left, its control on the right. Every row uses this. */
function Row({ label, hint, htmlFor, children }: {
  label: string; hint?: ReactNode; htmlFor?: string; children?: ReactNode;
}) {
  return (
    <div className="set-row">
      <div className="set-text">
        <label htmlFor={htmlFor}>{label}</label>
        {hint && <p>{hint}</p>}
      </div>
      {children && <div className="set-control">{children}</div>}
    </div>
  );
}

function Select<T extends string>({ id, value, onChange, children }: {
  id: string; value: T; onChange: (v: T) => void; children: ReactNode;
}) {
  return (
    <select id={id} className="select" value={value} onChange={(e) => onChange(e.target.value as T)}>
      {children}
    </select>
  );
}

type SectionId = "indexing" | "models" | "performance" | "updates" | "privacy";

function TokenBox({ set, onSaved }: { set: boolean; onSaved: (u: UpdateInfo) => void }) {
  const { toast } = useEngine();
  const [v, setV] = useState("");
  const save = async (token: string) => {
    try {
      onSaved(await api.setUpdateToken(token));
      setV("");
      toast(token ? "Token saved on this PC" : "Token removed", "ok");
    } catch (e) { toast(e instanceof Error ? e.message : "Couldn't save the token", "error"); }
  };
  return (
    <div className="btn-row">
      <input className="input mono" type="password" autoComplete="off" placeholder={set ? "•••••• (saved)" : "github_pat_…"}
        value={v} onChange={(e) => setV(e.target.value)} aria-label="GitHub access token" />
      <button className="btn btn-ghost" disabled={!v.trim()} onClick={() => save(v.trim())}>Save</button>
      {set && <button className="btn btn-ghost" onClick={() => save("")}>Remove</button>}
    </div>
  );
}

function RepoBox({ repo, custom, hasDefault, busy, onSaved }:
  { repo: string; custom: boolean; hasDefault: boolean; busy: boolean; onSaved: (u: UpdateInfo) => void }) {
  const { toast } = useEngine();
  const [v, setV] = useState(repo);
  useEffect(() => { setV(repo); }, [repo]);
  const save = async (value: string) => {
    try {
      const u = await api.setUpdateRepo(value);
      onSaved(u);
      toast(value ? `Updates will come from ${u.repo}` : "Using the repository built into this version", "ok");
    } catch (e) { toast(e instanceof Error ? e.message : "Couldn't save the repository", "error"); }
  };
  const changed = v.trim() !== repo && v.trim() !== "";
  return (
    <div className="btn-row">
      <input className="input mono" type="text" autoComplete="off" spellCheck={false} placeholder="owner/name or https://github.com/owner/name"
        value={v} onChange={(e) => setV(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && changed && !busy) save(v.trim()); }}
        aria-label="GitHub repository for updates" />
      <button className="btn btn-ghost" disabled={!changed || busy} onClick={() => save(v.trim())}>Save</button>
      {custom && hasDefault && <button className="btn btn-ghost" disabled={busy} onClick={() => save("")}>Reset</button>}
    </div>
  );
}

export function SettingsView() {
  const { settings, saveSettings, toast, version, models, status, update, setUpdate } = useEngine();
  const prefs = usePrefs();
  const [info, setInfo] = useState<AppInfo | null>(null);
  const [open, setOpen] = useState<SectionId | null>(null);
  useEffect(() => { appInfo().then(setInfo); }, []);

  if (!settings) return <section className="view page"><p className="muted">Loading settings…</p></section>;

  const commit = async (patch: Parameters<typeof saveSettings>[0]) => {
    const s = await saveSettings(patch);
    if (s) toast("Settings saved", "ok");
  };

  // ---- indexing -----------------------------------------------------------------------
  const schedule: IndexSchedule = settings.index_schedule ?? "realtime";
  const sched = scheduleOf(schedule);
  const pending = status?.pending_changes ?? 0;
  const changeSchedule = async (id: IndexSchedule) => {
    const s2 = await saveSettings({ index_schedule: id });
    if (s2) toast(`Indexing: ${scheduleOf(id).label.toLowerCase()}`, "ok");
  };
  const indexNow = async () => {
    try { await api.indexNow(); toast("Indexing started", "ok"); }
    catch (e) { toast(e instanceof Error ? e.message : "Could not start indexing", "error"); }
  };
  const nextRun = status?.next_index_run ? fmtIn(status.next_index_run) : "";

  // ---- updates ------------------------------------------------------------------------
  const latest = update?.latest;
  const upBusy = update?.state === "checking" || update?.state === "downloading" || update?.state === "installing";
  const mb = (n: number) => `${Math.round(n / 1048576)} MB`;
  const dl = update?.progress;
  const checkNow = async () => {
    try {
      setUpdate({ ...(update as NonNullable<typeof update>), state: "checking", error: "" });
      const u = await api.checkUpdate();
      setUpdate(u);
      if (u.error) toast(u.error, "error");
      else toast(u.available ? `Version ${u.latest?.version} is available` : "You have the latest version", "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not check for updates", "error");
      api.update().then(setUpdate).catch(() => {});
    }
  };
  const startUpdate = async () => {
    try { setUpdate(await api.downloadUpdate()); }
    catch (e) { toast(e instanceof Error ? e.message : "Could not start the download", "error"); }
  };
  const installUpdate = async () => {
    if (!latest) return;
    const ok = await askConfirm(
      `DeepFind will close, install version ${latest.version} and open again.\n\nYour index and settings are kept.`, "Update DeepFind");
    if (!ok) return;
    try { setUpdate(await api.installUpdate()); toast("Installing the update…", "ok"); }
    catch (e) { toast(e instanceof Error ? e.message : "Could not start the installer", "error"); }
  };
  const updateHint =
    !update ? "…"
    : !update.configured ? "Updates aren't set up in this build."
    : update.state === "installing" ? "Installing — DeepFind will reopen in a moment."
    : update.state === "checking" ? "Checking…"
    : update.state === "downloading" ? `Downloading version ${latest?.version}…`
    : update.state === "ready" ? `Version ${latest?.version} is downloaded and ready to install.`
    : update.available ? `Version ${latest?.version} is available.`
    : update.state === "up_to_date" ? `You have the latest version. Checked ${fmtWhen(update.checked_at)}.`
    : "Not checked yet.";

  const ocrLabel = settings.ocr_engine === "off" ? "Text in images off"
    : settings.ocr_engine === "tesseract" ? `Tesseract (${(settings.ocr_languages ?? ["eng"]).join("+")})` : "RapidOCR";
  const sections: { id: SectionId; title: string; desc: string; now: string; flag?: boolean }[] = [
    { id: "indexing", title: "Indexing", desc: "When DeepFind reads new and changed files.", now: sched.label },
    { id: "models", title: "Models", desc: "Choose and download the models that read text in images and find photos — compare them, pick per job, add your own.",
      now: `${ocrLabel} · ${settings.image_model === "off" ? "image search off" : "image search on"}`, flag: !!models && !models.installed },
    { id: "performance", title: "Performance", desc: "Keep your computer responsive while DeepFind works.",
      now: `CPU ${settings.cpu_limit}% · memory ${settings.ram_limit}%${settings.pause_on_battery ? " · pauses on battery" : ""}` },
    { id: "updates", title: "Updates", desc: "Install new versions over this one. Your index, folders and settings are kept.",
      now: `Version ${update?.current ?? info?.version ?? version ?? "—"}${update?.available ? ` · ${latest?.version} available` : ""}`, flag: !!update?.available },
    { id: "privacy", title: "Privacy & about", desc: "Recent searches, data and log folders.", now: "Offline · CPU only · no telemetry" },
  ];
  const current = sections.find((x) => x.id === open);

  if (!current) {
    return (
      <section className="view page settings-page" aria-label="Settings">
        <header className="page-head">
          <div>
            <h1>Settings</h1>
            <p>Pick a section to see and change its options.</p>
          </div>
        </header>
        <ul className="settings-list">
          {sections.map((x) => (
            <li key={x.id}>
              <button className="settings-item" onClick={() => setOpen(x.id)}>
                <span className="si-text">
                  <b>{x.title}{x.flag && <i className="si-flag" aria-label="needs attention" />}</b>
                  <span>{x.desc}</span>
                  <em>{x.now}</em>
                </span>
                <span className="si-go" aria-hidden>›</span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    );
  }

  return (
    <section className="view page settings-page" aria-label={`Settings — ${current.title}`}>
      <header className="page-head">
        <div>
          <button className="back-link" onClick={() => setOpen(null)}>‹ Settings</button>
          <h1>{current.title}</h1>
          <p>{current.desc}</p>
        </div>
      </header>
      {open === "models" ? <ModelsSettings /> : (
      <div className="settings-stack settings-detail">
        {open === "indexing" && (<>
        {/* 1 ─ Indexing */}
        <div className="panel card">
          <div className="card-head">
            <h2 className="card-title">Indexing</h2>
            <p className="card-desc">When DeepFind reads new and changed files.</p>
          </div>
          <Row label="How often to index" htmlFor="index-schedule" hint={sched.hint}>
            <Select id="index-schedule" value={schedule} onChange={changeSchedule}>
              {SCHEDULES.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
            </Select>
          </Row>
          <Row label="Status"
            hint={schedule === "realtime"
              ? "Changes are read as soon as they happen."
              : pending === 0 ? "No changes waiting." : `${pending} changed ${pending === 1 ? "file is" : "files are"} waiting for the next run.`}>
            <span className="set-value">
              Last run: <b>{fmtWhen(status?.last_index_run ?? settings.last_index_run)}</b>
              {nextRun && <> · Next: <b>{nextRun}</b></>}
            </span>
          </Row>
          <Row label="Index now" hint="Read everything that changed since the last run.">
            <button className="btn btn-primary" onClick={indexNow}>
              <IcRefresh width={16} height={16} /> Index now
            </button>
          </Row>
        </div>

        </>)}
        {open === "performance" && (<>
        {/* 4 ─ Performance */}
        <div className="panel card">
          <div className="card-head">
            <h2 className="card-title">Performance</h2>
            <p className="card-desc">Keep your computer responsive while DeepFind works.</p>
          </div>
          <Slider label="CPU limit" value={settings.cpu_limit} min={5} max={100}
            hint="Image and scan processing waits while CPU use is above this."
            onCommit={(n) => commit({ cpu_limit: n })} />
          <Slider label="Memory limit" value={settings.ram_limit} min={10} max={100}
            hint="Same for memory — keeps 8 GB machines responsive."
            onCommit={(n) => commit({ ram_limit: n })} />
          <Row label="Pause on battery" hint="Hold image processing until you're plugged in. Documents are always indexed.">
            <Toggle label="Pause on battery" on={settings.pause_on_battery}
              onChange={(v) => commit({ pause_on_battery: v })} />
          </Row>
        </div>

        </>)}
        {open === "updates" && (<>
        {/* 5 ─ Updates */}
        <div className="panel card">
          <div className="card-head">
            <h2 className="card-title">Updates</h2>
            <p className="card-desc">New versions are installed over this one. Your index, folders and settings are kept.</p>
          </div>
          <Row label="Version" hint={updateHint}>
            <span className="set-value"><b>{update?.current ?? info?.version ?? version ?? "—"}</b></span>
          </Row>
          <Row label="Check automatically"
            hint="Once a day DeepFind asks github.com whether a newer version exists. It sends nothing about your files.">
            <Toggle label="Check for updates automatically" on={settings.auto_update_check ?? true}
              onChange={(v) => commit({ auto_update_check: v })} />
          </Row>
          <Row label="Update source"
            hint={update?.repo
              ? <>Checking <span className="mono">github.com/{update.repo}</span>{update.repo_custom ? " (your choice)." : " (built into this version)."}</>
              : "No repository yet — paste the GitHub link or owner/name of the DeepFind repository."}>
            <RepoBox repo={update?.repo ?? ""} custom={!!update?.repo_custom} hasDefault={!!update?.repo_default}
              busy={upBusy} onSaved={(u) => { setUpdate(u); checkNow(); }} />
          </Row>
          <Row label="Private repository access"
            hint={update?.token_set ? "A read-only access token is saved on this PC."
              : "Only needed if the repository is private: paste a fine-grained GitHub token with read access to its Contents."}>
            <TokenBox set={!!update?.token_set} onSaved={setUpdate} />
          </Row>
          {update?.error && update.state !== "installing" && <div className="notice bad">{update.error}</div>}
          {update?.available && latest?.notes && update.state !== "installing" && (
            <pre className="update-notes selectable" aria-label="What's new">
              {latest.notes.replace(/^#{1,6}\s*/gm, "").replace(/^[-*]\s+/gm, "• ").trim()}
            </pre>
          )}
          {update?.state === "downloading" && (
            <div className="dl-progress">
              <div className={`bar ${dl?.total ? "" : "indeterminate"}`}>
                <span style={dl?.total ? { width: `${Math.min(100, Math.round((dl.done / dl.total) * 100))}%` } : undefined} />
              </div>
              <span className="muted">{mb(dl?.done ?? 0)}{dl?.total ? ` of ${mb(dl.total)}` : ""}</span>
            </div>
          )}
          {update?.available && !update.can_self_update && <p className="field-warn">{update.blocker}</p>}
          <div className="card-foot">
            {update?.state === "ready" ? (
              <button className="btn btn-primary" onClick={installUpdate}>
                <IcRefresh width={16} height={16} /> Restart &amp; install {latest?.version}
              </button>
            ) : update?.available && !upBusy ? (
              update.can_self_update ? (
                <button className="btn btn-primary" onClick={startUpdate}>
                  <IcDownload width={16} height={16} /> Download version {latest?.version}
                </button>
              ) : latest?.page ? (
                <button className="btn btn-primary" onClick={() => openUrl(latest.page)}>
                  <IcExternal width={14} height={14} /> Open download page
                </button>
              ) : null
            ) : null}
            <button className="btn btn-ghost" onClick={checkNow} disabled={upBusy || !update?.configured}>
              Check for updates
            </button>
          </div>
        </div>

        </>)}
        {open === "privacy" && (<>
        {/* 6 ─ Privacy & about */}
        <div className="panel card">
          <div className="card-head">
            <h2 className="card-title">Privacy &amp; about</h2>
            <p className="card-desc">Offline · CPU only · no telemetry.</p>
          </div>
          <Row label="Recent searches"
            hint={prefs.recent.length ? `${prefs.recent.length} stored on this PC only.` : "Nothing stored."}>
            <button className="btn btn-ghost" disabled={!prefs.recent.length}
              onClick={() => { setPrefs((p) => ({ ...p, recent: [] })); toast("Recent searches cleared", "ok"); }}>
              Clear
            </button>
          </Row>
          {info && (
            <Row label="Data and logs" hint={<span className="mono ellip" title={info.data_dir}>{info.data_dir}</span>}>
              <div className="btn-row">
                <button className="btn btn-ghost" onClick={() => openPath("data")}>Data <IcExternal width={14} height={14} /></button>
                <button className="btn btn-ghost" onClick={() => openPath("logs")}>Logs <IcExternal width={14} height={14} /></button>
              </div>
            </Row>
          )}
        </div>
        </>)}
      </div>
      )}
    </section>
  );
}
