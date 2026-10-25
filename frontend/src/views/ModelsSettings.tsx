/**
 * Settings -> Models. Pick which models to download (nothing is fetched until you tick it and press
 * Download), compare them, choose which one does each job, and add your own.
 */
import { useCallback, useEffect, useState } from "react";
import { api, Catalog, CatalogItem, CustomModelIn, ModelRole, OcrEngineId } from "../lib/api";
import { askConfirm, inDesktop, openPath } from "../lib/desktop";
import { useEngine } from "../lib/engine";
import { IcCheck, IcDownload, IcPlus, IcTrash } from "../components/Icons";

const mb = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)} GB` : `${n} MB`);

function Meter({ label, value }: { label: string; value: number }) {
  if (!value) return <span className="meter muted">{label}: —</span>;
  return (
    <span className="meter" title={`${label}: ${value} of 5`}>
      {label}
      <span className="meter-dots" aria-label={`${value} of 5`}>
        {[1, 2, 3, 4, 5].map((i) => <i key={i} className={i <= value ? "on" : ""} />)}
      </span>
    </span>
  );
}

function StatusTag({ it }: { it: CatalogItem }) {
  if (it.active && it.status !== "installed")
    return <span className="badge badge-bad" title="Chosen in “In use”, but not downloaded yet">Chosen · not downloaded</span>;
  if (it.active) return <span className="badge badge-ok"><IcCheck width={12} height={12} /> In use</span>;
  if (it.status === "installed") return <span className="badge badge-ok">Downloaded</span>;
  if (it.status === "downloading") return <span className="badge badge-info">Downloading…</span>;
  if (it.status === "queued") return <span className="badge badge-info">Waiting</span>;
  if (it.status === "failed") return <span className="badge badge-bad">Failed</span>;
  return null;
}

function ModelCard({ it, checked, onCheck, onRemove, onDelete, onCancel, progress }: {
  it: CatalogItem; checked: boolean; onCheck: (v: boolean) => void;
  onRemove: () => void; onDelete: () => void; onCancel: () => void; progress?: string;
}) {
  const canTick = it.status === "available" || it.status === "failed";
  return (
    <li className={`mcard ${it.active ? "is-active" : ""} ${checked ? "is-checked" : ""}`}>
      <label className="mcard-pick">
        <input type="checkbox" disabled={!canTick} checked={checked || it.status === "installed" || it.status === "queued" || it.status === "downloading"}
          onChange={(e) => onCheck(e.target.checked)} aria-label={`Download ${it.name}`} />
      </label>
      <div className="mcard-body">
        <div className="mcard-title">
          <b>{it.name}</b>
          {it.custom && <span className="badge">Your model</span>}
          <StatusTag it={it} />
          <span className="mcard-size">{it.size_mb ? `≈ ${mb(it.size_mb)}` : ""}</span>
        </div>
        <p className="mcard-sum">{it.summary}</p>
        <div className="mcard-meta">
          <Meter label="Speed" value={it.speed} />
          <Meter label="Accuracy" value={it.accuracy} />
          {it.languages && <span>Languages: <b>{it.languages}</b></span>}
          {it.dim ? <span>Vector size: <b>{it.dim}</b></span> : null}
        </div>
        {(it.good || it.weak) && (
          <ul className="mcard-pros">
            {it.good && <li className="pro">+ {it.good}</li>}
            {it.weak && <li className="con">− {it.weak}</li>}
          </ul>
        )}
        {it.status === "downloading" && progress && <p className="muted small">{progress}</p>}
        {it.status === "failed" && it.error && <p className="field-warn">{it.error}</p>}
        <div className="mcard-actions">
          {it.status === "queued" && <button className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>}
          {it.status === "installed" && !it.active && (
            <button className="btn btn-ghost btn-sm" onClick={onRemove}><IcTrash width={14} height={14} /> Remove download</button>
          )}
          {it.custom && !it.active && (
            <button className="btn btn-ghost btn-sm" onClick={onDelete}><IcTrash width={14} height={14} /> Delete from list</button>
          )}
        </div>
      </div>
    </li>
  );
}

const EMPTY_CUSTOM: CustomModelIn = { kind: "tess-lang", name: "" };

function AddCustom({ onAdded }: { onAdded: (c: Catalog, name: string) => void }) {
  const { toast } = useEngine();
  const [m, setM] = useState<CustomModelIn>(EMPTY_CUSTOM);
  const [busy, setBusy] = useState(false);
  const f = (k: keyof CustomModelIn) => ({
    value: (m[k] as string | number | undefined) ?? "",
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      setM({ ...m, [k]: k === "dim" ? Number(e.target.value) : e.target.value }),
  });
  const forWhat = m.kind === "clip" ? "Photos by what they show + search by meaning" : "Text inside images & scanned PDFs";
  const add = async () => {
    setBusy(true);
    try {
      const c = await api.addCustomModel(m);
      onAdded(c, m.name);
      setM({ ...EMPTY_CUSTOM, kind: m.kind });
    } catch (e) {
      toast(e instanceof Error ? e.message : "Couldn't add the model", "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="panel card add-model">
      <div className="card-head">
        <h2 className="card-title">Add your own model</h2>
        <p className="card-desc">Give it a name, say what kind it is, and where to get it. It appears in the list above, ready to tick and download.</p>
      </div>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="cm-kind">Type of model</label>
          <select id="cm-kind" className="select" value={m.kind}
            onChange={(e) => setM({ ...EMPTY_CUSTOM, name: m.name, kind: e.target.value as CustomModelIn["kind"] })}>
            <option value="tess-lang">Tesseract language pack</option>
            <option value="rapidocr">RapidOCR / PaddleOCR model files</option>
            <option value="clip">Image-search model (Hugging Face, CLIP-style ONNX)</option>
          </select>
          <p className="field-hint">Used for: <b>{forWhat}</b></p>
        </div>
        <div className="field">
          <label htmlFor="cm-name">Name</label>
          <input id="cm-name" className="input" placeholder="e.g. Hindi scans" maxLength={60} {...f("name")} />
        </div>

        {m.kind === "tess-lang" && <>
          <div className="field">
            <label htmlFor="cm-code">Language code</label>
            <input id="cm-code" className="input mono" placeholder="hin, tam, chi_tra, script/Devanagari…" {...f("code")} />
            <p className="field-hint">Tesseract's name for the language (the file is &lt;code&gt;.traineddata).</p>
          </div>
          <div className="field">
            <label htmlFor="cm-url">Download link (optional)</label>
            <input id="cm-url" className="input mono" placeholder="https://… .traineddata" {...f("url")} />
            <p className="field-hint">Empty = the official fast pack. Paste a tessdata_best link for higher accuracy.</p>
          </div>
        </>}

        {m.kind === "rapidocr" && <>
          <div className="field">
            <label htmlFor="cm-det">Detection model (.onnx) link</label>
            <input id="cm-det" className="input mono" placeholder="https://…det….onnx" {...f("det_url")} />
          </div>
          <div className="field">
            <label htmlFor="cm-rec">Recognition model (.onnx) link</label>
            <input id="cm-rec" className="input mono" placeholder="https://…rec….onnx" {...f("rec_url")} />
          </div>
          <div className="field">
            <label htmlFor="cm-keys">Character list (.txt) link — optional</label>
            <input id="cm-keys" className="input mono" placeholder="https://…dict.txt" {...f("keys_url")} />
          </div>
          <div className="field">
            <label htmlFor="cm-lang">Languages it reads</label>
            <input id="cm-lang" className="input" placeholder="e.g. Chinese + English" {...f("languages")} />
          </div>
        </>}

        {m.kind === "clip" && <>
          <div className="field">
            <label htmlFor="cm-repo">Hugging Face repository</label>
            <input id="cm-repo" className="input mono" placeholder="owner/name" {...f("repo")} />
            <p className="field-hint">Must contain ONNX files and preprocessor_config.json (fastembed layout).</p>
          </div>
          <div className="field">
            <label htmlFor="cm-dim">Vector size</label>
            <input id="cm-dim" className="input" type="number" placeholder="512" {...f("dim")} />
            <p className="field-hint">From the model card (e.g. 512, 768). Checked after download.</p>
          </div>
          <div className="field">
            <label htmlFor="cm-tf">Text model file</label>
            <input id="cm-tf" className="input mono" placeholder="onnx/text_model.onnx" {...f("text_file")} />
          </div>
          <div className="field">
            <label htmlFor="cm-if">Image model file</label>
            <input id="cm-if" className="input mono" placeholder="onnx/vision_model.onnx" {...f("image_file")} />
          </div>
          <div className="field">
            <label htmlFor="cm-ir">Image repository (if different)</label>
            <input id="cm-ir" className="input mono" placeholder="same as above" {...f("image_repo")} />
          </div>
        </>}

        <div className="field span-2">
          <label htmlFor="cm-note">What is it good for? (shown in the list)</label>
          <input id="cm-note" className="input" maxLength={200} placeholder="e.g. Old Hindi textbooks" {...f("note")} />
        </div>
      </div>
      <div className="card-foot">
        <button className="btn btn-primary" onClick={add} disabled={busy || !m.name.trim()}>
          <IcPlus width={16} height={16} /> Add to the list
        </button>
      </div>
    </div>
  );
}

export function ModelsSettings() {
  const { toast, refreshModels } = useEngine();
  const [cat, setCat] = useState<Catalog | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const load = useCallback(() => api.catalog().then(setCat).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  const busy = !!cat && (cat.jobs.current !== null || cat.jobs.queue.length > 0);
  useEffect(() => {
    if (!busy) return;
    const iv = setInterval(load, 1000);
    return () => clearInterval(iv);
  }, [busy, load]);
  useEffect(() => { if (!busy) refreshModels(); }, [busy, refreshModels]);   // badges in Search/Activity

  const items = cat?.items ?? [];
  const byRole = (r: ModelRole) => items.filter((i) => i.role === r && i.kind !== "tess-lang");
  const langs = items.filter((i) => i.kind === "tess-lang");
  const pickedItems = items.filter((i) => picked.has(i.id));
  const tessItem = items.find((i) => i.id === "tesseract");
  const needsEngine = pickedItems.some((i) => i.requires === "tesseract") && !picked.has("tesseract")
    && tessItem && tessItem.status !== "installed" && tessItem.status !== "queued" && tessItem.status !== "downloading";
  const pickedSize = pickedItems.reduce((n, i) => n + (i.size_mb || 0), 0) + (needsEngine ? tessItem!.size_mb : 0);
  const prog = cat?.jobs.progress;
  const progress = cat?.jobs.message
    ? `${cat.jobs.message}${prog?.total ? ` ${Math.round((prog.done / prog.total) * 100)}%` : ""}` : "";

  const check = (id: string, v: boolean) => setPicked((p) => {
    const n = new Set(p);
    if (v) n.add(id); else n.delete(id);
    return n;
  });
  const run = async (fn: () => Promise<Catalog>, ok?: string) => {
    try { setCat(await fn()); if (ok) toast(ok, "ok"); }
    catch (e) { toast(e instanceof Error ? e.message : "Something went wrong", "error"); }
  };
  const download = async () => {
    const ids = [...picked];
    await run(() => api.downloadCatalog(ids), `Downloading ${ids.length} model${ids.length === 1 ? "" : "s"}`);
    setPicked(new Set());
  };
  const remove = async (it: CatalogItem) => {
    if (await askConfirm(`Remove the downloaded files of “${it.name}”? You can download it again later.`, "Remove model"))
      run(() => api.removeModel(it.id), "Removed");
  };
  const del = async (it: CatalogItem) => {
    if (await askConfirm(`Delete “${it.name}” from the list (and its files)?`, "Delete model"))
      run(() => api.deleteCustomModel(it.id), "Deleted");
  };

  // ---- what is in use ----
  const a = cat?.active;
  const installed = (pred: (i: CatalogItem) => boolean) => items.filter((i) => pred(i) && i.status === "installed");
  const rapidSets = installed((i) => i.kind === "rapidocr");
  const tessReady = installed((i) => i.kind === "tesseract").length > 0;
  const langPacks = installed((i) => i.kind === "tess-lang");
  const imageModels = installed((i) => i.kind === "clip");
  const setActive = async (patch: Parameters<typeof api.setActiveModels>[0], what: string, warn?: string) => {
    if (warn && !(await askConfirm(warn, "Change model"))) return;
    run(() => api.setActiveModels(patch), what);
  };
  const changeImage = (id: string) => {
    const cur = items.find((i) => i.id === a?.image_model);
    const next = items.find((i) => i.id === id);
    const full = id !== "off" && cur && next && cur.dim !== next.dim;
    setActive({ image_model: id }, id === "off" ? "Image search switched off" : `Now using ${next?.name}`,
      full ? "This model uses a different vector size, so DeepFind rebuilds the whole index (all files are read again). Continue?"
        : id !== "off" ? "Your pictures and scans will be processed again with the new model in the background. Continue?" : undefined);
  };
  const toggleLang = (code: string, on: boolean) => {
    const next = on ? [...(a?.ocr_languages ?? []), code] : (a?.ocr_languages ?? []).filter((c) => c !== code);
    setActive({ ocr_languages: next }, "Languages updated — images are read again");
  };

  if (!cat || !a) return <p className="muted">Loading models…</p>;

  return (
    <div className="settings-stack models-page">
      {/* in use */}
      <div className="panel card">
        <div className="card-head">
          <h2 className="card-title">In use</h2>
          <p className="card-desc">Which downloaded model does each job. Only downloaded models can be chosen.</p>
        </div>
        <div className="set-row">
          <div className="set-text">
            <label htmlFor="use-ocr">Text inside images &amp; scanned PDFs</label>
            <p>{a.ocr_engine === "off" ? "Not reading text in images." : a.ocr_engine === "tesseract"
              ? `Tesseract · ${a.ocr_languages.join(" + ")}` : items.find((i) => i.id === a.ocr_model)?.name}</p>
          </div>
          <div className="set-control">
            <select id="use-ocr" className="select"
              value={a.ocr_engine === "rapidocr" ? `r:${a.ocr_model}` : a.ocr_engine}
              onChange={(e) => {
                const v = e.target.value;
                if (v.startsWith("r:")) setActive({ ocr_engine: "rapidocr", ocr_model: v.slice(2) }, "OCR model changed — images are read again");
                else setActive({ ocr_engine: v as OcrEngineId }, v === "off" ? "Text in images switched off" : "Now using Tesseract — images are read again");
              }}>
              {rapidSets.map((i) => <option key={i.id} value={`r:${i.id}`}>{i.name}</option>)}
              {a.ocr_engine === "rapidocr" && !rapidSets.some((i) => i.id === a.ocr_model) &&
                <option value={`r:${a.ocr_model}`}>{items.find((i) => i.id === a.ocr_model)?.name ?? a.ocr_model} (not downloaded)</option>}
              <option value="tesseract" disabled={!tessReady}>Tesseract{tessReady ? "" : " (not downloaded)"}</option>
              <option value="off">Off</option>
            </select>
          </div>
        </div>
        {a.ocr_engine === "tesseract" && (
          <div className="set-row">
            <div className="set-text">
              <label>Tesseract languages</label>
              <p>Tick every language your documents use. More languages = a bit slower.</p>
            </div>
            <div className="set-control lang-ticks">
              {langPacks.length === 0 && <span className="muted">Download a language pack below.</span>}
              {langPacks.map((l) => (
                <label key={l.id} className="tick">
                  <input type="checkbox" checked={a.ocr_languages.includes(l.code!)}
                    onChange={(e) => toggleLang(l.code!, e.target.checked)} /> {l.languages ?? l.name}
                </label>
              ))}
            </div>
          </div>
        )}
        <div className="set-row">
          <div className="set-text">
            <label htmlFor="use-img">Photos by what they show &amp; search by meaning</label>
            <p>{a.image_model === "off" ? "Off — keyword search only." : items.find((i) => i.id === a.image_model)?.name}</p>
          </div>
          <div className="set-control">
            <select id="use-img" className="select" value={a.image_model} onChange={(e) => changeImage(e.target.value)}>
              {imageModels.map((i) => <option key={i.id} value={i.id}>{i.name}</option>)}
              {a.image_model !== "off" && !imageModels.some((i) => i.id === a.image_model) &&
                <option value={a.image_model}>{items.find((i) => i.id === a.image_model)?.name ?? a.image_model} (not downloaded)</option>}
              <option value="off">Off</option>
            </select>
          </div>
        </div>
      </div>

      {/* catalogue per job */}
      {cat.roles.map((r) => (
        <div key={r.id} className="panel card">
          <div className="card-head">
            <h2 className="card-title">{r.label}</h2>
            <p className="card-desc">{r.description} Tick the ones you want, then press Download below.</p>
          </div>
          <ul className="mcards">
            {byRole(r.id).map((it) => (
              <ModelCard key={it.id} it={it} checked={picked.has(it.id)} onCheck={(v) => check(it.id, v)}
                onRemove={() => remove(it)} onDelete={() => del(it)}
                onCancel={() => run(() => api.cancelCatalog(it.id))} progress={progress} />
            ))}
          </ul>
          {r.id === "ocr" && (
            <>
              <h3 className="sub-title">Tesseract language packs</h3>
              <p className="card-desc small">Each pack lets Tesseract read one more language. The Tesseract engine is downloaded automatically with your first pack.</p>
              <ul className="lang-grid">
                {langs.map((l) => {
                  const done = l.status === "installed";
                  const going = l.status === "queued" || l.status === "downloading";
                  return (
                    <li key={l.id} className={done ? "done" : ""} title={l.error || l.summary}>
                      <label className="tick">
                        <input type="checkbox" disabled={done || going} checked={done || going || picked.has(l.id)}
                          onChange={(e) => check(l.id, e.target.checked)} />
                        <span>{l.languages ?? l.name}{l.custom ? " ★" : ""}</span>
                      </label>
                      <em>{done ? (l.active ? "in use" : "downloaded") : going ? "…" : l.status === "failed" ? "failed" : l.size_mb ? `${l.size_mb} MB` : ""}</em>
                      {done && !l.active && (
                        <button className="icon-btn" aria-label={`Remove ${l.name}`} onClick={() => remove(l)}><IcTrash width={13} height={13} /></button>
                      )}
                      {l.custom && !l.active && !done && (
                        <button className="icon-btn" aria-label={`Delete ${l.name}`} onClick={() => del(l)}><IcTrash width={13} height={13} /></button>
                      )}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>
      ))}

      <AddCustom onAdded={(c, name) => { setCat(c); toast(`“${name}” added — tick it and press Download`, "ok"); }} />

      {/* download bar */}
      <div className="dl-bar">
        <span>
          {busy ? <>{progress || "Working…"} {cat.jobs.queue.length > 0 && <span className="muted">· {cat.jobs.queue.length} waiting</span>}</>
            : picked.size ? <>{picked.size} selected · ≈ {mb(pickedSize)}</> : <span className="muted">Nothing selected. Downloaded models work offline.</span>}
        </span>
        {inDesktop() && <button className="btn btn-ghost" onClick={() => openPath("models")}>Open models folder</button>}
        <button className="btn btn-primary" onClick={download} disabled={!picked.size}>
          <IcDownload width={16} height={16} /> Download selected
        </button>
      </div>
    </div>
  );
}
