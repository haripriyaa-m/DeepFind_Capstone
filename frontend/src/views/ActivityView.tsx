import { useMemo, useState } from "react";
import { api, Status } from "../lib/api";
import { askConfirm } from "../lib/desktop";
import { semanticReady, useEngine } from "../lib/engine";
import { FAMILIES, Family, familyOf, fmtInt } from "../lib/files";
import { Spark } from "../components/Spark";
import { fmtIn, fmtWhen, scheduleOf } from "../lib/schedule";
import { IcAlert, IcCheck, IcRefresh, IcWarn } from "../components/Icons";

function Gauge({ label, value, limit }: { label: string; value?: number; limit?: number }) {
  const v = Math.round(value ?? 0);
  const over = limit != null && v > limit;
  return (
    <div className={`gauge ${over ? "over" : ""}`}>
      <div className="gauge-head">
        <span>{label}</span>
        <b>{v}%</b>
        {limit != null && <em>limit {limit}%</em>}
      </div>
      <div className="gauge-track">
        <span className="gauge-fill" style={{ width: `${Math.min(100, v)}%` }} />
        {limit != null && <span className="gauge-limit" style={{ left: `${limit}%` }} title={`Limit ${limit}%`} />}
      </div>
      {over && <div className="gauge-note"><IcWarn width={13} height={13} /> Above limit — image processing is waiting</div>}
    </div>
  );
}

export function engineState(s: Status | null, lim?: { cpu_limit: number; ram_limit: number; pause_on_battery: boolean }) {
  if (!s || !s.ready) return { word: "Starting", why: "Opening the index", tone: "warn" as const };
  if (s.paused) {
    const why = [
      lim && (s.cpu_percent ?? 0) > lim.cpu_limit ? "CPU busy" : "",
      lim && (s.ram_percent ?? 0) > lim.ram_limit ? "memory busy" : "",
      lim?.pause_on_battery && s.on_battery ? "on battery" : "",
    ].filter(Boolean).join(" · ");
    return { word: "Paused", why: `Waiting: ${why || "system busy"}`, tone: "warn" as const };
  }
  const waiting = s.pending_changes ?? 0;
  if (s.idle && waiting > 0) {
    return { word: "Changes waiting", tone: "warn" as const,
             why: `${waiting} changed ${waiting === 1 ? "file" : "files"} will be read at the next index run` };
  }
  if (s.idle) return { word: "Up to date", why: "Watching for changes", tone: "ok" as const };
  return { word: "Indexing", why: "Reading new and changed files", tone: "live" as const };
}

export function ActivityView() {
  const { status: s, settings, library, history, toast, models } = useEngine();
  const [busy, setBusy] = useState(false);
  const st = engineState(s, settings ?? undefined);

  const fam = useMemo(() => {
    const c: Record<Family, number> = { docs: 0, pdf: 0, sheets: 0, images: 0, videos: 0, code: 0, other: 0 };
    for (const [ext, n] of Object.entries(library?.types ?? {})) c[familyOf(ext)] += n;
    return c;
  }, [library]);
  const total = Object.values(fam).reduce((a, b) => a + b, 0);

  const reindex = async () => {
    const ok = await askConfirm(
      "Re-read every file in your watched folders?\n\nSearch keeps working while it runs. Images and scanned PDFs take longer.",
      "Rebuild index");
    if (!ok) return;
    setBusy(true);
    try { await api.reindex(); toast("Rebuilding the index", "ok"); }
    catch (e) { toast(e instanceof Error ? e.message : "Reindex failed", "error"); }
    finally { setBusy(false); }
  };

  const indexNow = async () => {
    try { await api.indexNow(); toast("Indexing started", "ok"); }
    catch (e) { toast(e instanceof Error ? e.message : "Could not start indexing", "error"); }
  };
  const schedule = settings?.index_schedule ?? s?.index_schedule ?? "realtime";
  const waiting = s?.pending_changes ?? 0;
  const ocrId = s?.ocr_engine ?? settings?.ocr_engine ?? models?.ocr_engine ?? "rapidocr";
  const ocrInfo = models?.ocr_engines?.find((e) => e.id === ocrId);
  const now = s?.tier2_current || s?.current_file;
  const queue = (s?.work_queue ?? 0) + (s?.tier2_queue ?? 0);

  return (
    <section className="view page" aria-label="Activity">
      <header className="page-head">
        <div>
          <h1>Activity</h1>
          <p>What the indexing engine is doing, and how much of your machine it uses.</p>
        </div>
        <div className="head-actions">
          <button className="btn btn-primary" onClick={indexNow}>
            <IcRefresh width={16} height={16} /> Index now
          </button>
          <button className="btn btn-ghost" onClick={reindex} disabled={busy}
            title="Forget everything and read all files again">
            Rebuild index
          </button>
        </div>
      </header>

      <div className="stat-grid">
        <div className="panel stat">
          <span className="stat-label">Files indexed</span>
          <b className="stat-value">{fmtInt(s?.db_rows)}</b>
          <span className="stat-foot">{fmtInt(s?.indexed_files)} fully processed</span>
        </div>
        <div className="panel stat">
          <span className="stat-label">In queue</span>
          <b className="stat-value">{fmtInt(queue)}</b>
          <span className="stat-foot">{fmtInt(s?.tier2_queue)} images &amp; scans waiting</span>
        </div>
        <div className="panel stat">
          <span className="stat-label">Images &amp; scans</span>
          <b className="stat-value">{fmtInt(s?.tier2_processed)}</b>
          <span className="stat-foot">{fmtInt(s?.tier2_failed)} could not be read</span>
        </div>
        <div className="panel stat">
          <span className="stat-label">Errors</span>
          <b className={`stat-value ${s?.errors ? "warn" : ""}`}>{fmtInt(s?.errors)}</b>
          <span className="stat-foot">since the app started</span>
        </div>
      </div>

      <div className="two-col">
        <div className="panel card">
          <h2 className="card-title">Engine</h2>
          <div className={`engine-state tone-${st.tone}`}>
            <i /><div><b>{st.word}</b><span>{st.why}</span></div>
          </div>
          <dl className="kv">
            <div><dt>Indexing</dt><dd>{scheduleOf(schedule).label}</dd></div>
            <div><dt>Last run</dt><dd>{fmtWhen(s?.last_index_run ?? settings?.last_index_run)}</dd></div>
            {s?.next_index_run
              ? <div><dt>Next run</dt><dd>{fmtIn(s.next_index_run)}</dd></div>
              : schedule !== "realtime" && <div><dt>Changes waiting</dt><dd>{waiting}</dd></div>}
            <div><dt>Current file</dt><dd className="mono ellip" title={now ?? ""}>{now ?? "—"}</dd></div>
            <div><dt>Keyword search</dt><dd><span className="ok-line"><IcCheck width={14} height={14} /> Ready</span></dd></div>
            <div>
              <dt>Semantic search</dt>
              <dd>{semanticReady(s, models)
                ? <span className="ok-line"><IcCheck width={14} height={14} /> Ready</span>
                : <span className="warn-line" title={s?.semantic_error ?? ""}><IcAlert width={14} height={14} />
                    {models && !models.parts.text ? "AI model not installed" : "Unavailable"}</span>}</dd>
            </div>
            <div>
              <dt>Text engine</dt>
              <dd>{ocrInfo
                ? (ocrInfo.available
                  ? <span className="ok-line"><IcCheck width={14} height={14} /> {ocrInfo.label}</span>
                  : <span className="warn-line" title={ocrInfo.detail}><IcAlert width={14} height={14} /> {ocrInfo.label} unavailable</span>)
                : "—"}</dd>
            </div>
            <div>
              <dt>Image reading</dt>
              <dd>{s?.vision_available === false || (models && !(models.parts.ocr || models.parts.vision))
                ? <span className="warn-line"><IcAlert width={14} height={14} /> Needs AI models</span>
                : <span className="ok-line"><IcCheck width={14} height={14} /> Available</span>}</dd>
            </div>
          </dl>
        </div>

        <div className="panel card">
          <h2 className="card-title">System</h2>
          <Gauge label="CPU" value={s?.cpu_percent} limit={settings?.cpu_limit} />
          <div className="spark-wrap">
            <span className="spark-cap">CPU, last 2 minutes</span>
            <Spark data={history} field="cpu" limit={settings?.cpu_limit} label="CPU" />
          </div>
          <Gauge label="Memory" value={s?.ram_percent} limit={settings?.ram_limit} />
          <div className="power">
            <span className={`badge ${s?.on_battery ? "warn" : "ok"}`}>{s?.on_battery ? "On battery" : "Plugged in"}</span>
            {settings?.pause_on_battery && <span className="muted">Image processing pauses on battery</span>}
          </div>
        </div>
      </div>

      <div className="panel card">
        <div className="card-title-row">
          <h2 className="card-title">Library</h2>
          <span className="muted">{fmtInt(total)} files, by kind</span>
        </div>
        <ul className="hbars">
          {FAMILIES.map((f) => ({ ...f, n: fam[f.id] })).sort((x, y) => y.n - x.n).map((f) => (
            <li key={f.id} className={f.n ? "" : "zero"} title={`${f.label}: ${fmtInt(f.n)}`}>
              <span className="hb-label">{f.label}</span>
              <span className="hb-track"><span style={{ width: `${total ? Math.max(f.n ? 1.5 : 0, (f.n / Math.max(...Object.values(fam), 1)) * 100) : 0}%` }} /></span>
              <b>{fmtInt(f.n)}</b>
              <span className="hb-pct">{total ? `${Math.round((f.n / total) * 100)}%` : "—"}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="panel card">
        <div className="card-title-row">
          <h2 className="card-title">Ignored Files</h2>
          <span className="muted">Unsupported language or unreadable</span>
        </div>
        {s?.ignored_files && s.ignored_files.length > 0 ? (
          <ul className="file-list">
            {s.ignored_files.map((file, i) => (
              <li key={i} className="mono ellip" title={file} style={{ padding: "4px 0", fontSize: "0.9em", color: "var(--text-muted)" }}>
                {file.split(/[/\\]/).pop()}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">No files have been ignored.</p>
        )}
      </div>
    </section>
  );
}