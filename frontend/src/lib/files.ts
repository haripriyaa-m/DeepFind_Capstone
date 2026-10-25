/** File-type families. Colours are the validated categorical slots (see styles/tokens.css). */

export type Family = "docs" | "pdf" | "sheets" | "images" | "videos" | "code" | "other";

export const FAMILIES: { id: Family; label: string; color: string }[] = [
  { id: "docs",   label: "Documents",       color: "var(--cat-1)" },
  { id: "pdf",    label: "PDFs",            color: "var(--cat-2)" },
  { id: "sheets", label: "Sheets & slides", color: "var(--cat-3)" },
  { id: "images", label: "Images",          color: "var(--cat-4)" },
  { id: "videos", label: "Videos",          color: "var(--f-videos)" },
  { id: "code",   label: "Code & text",     color: "var(--cat-5)" },
  { id: "other",  label: "Other",           color: "var(--cat-6)" },
];

const MAP: Record<string, Family> = {
  docx: "docs", doc: "docs", md: "docs", txt: "docs", rtf: "docs", odt: "docs",
  pdf: "pdf",
  xlsx: "sheets", xls: "sheets", csv: "sheets", pptx: "sheets", ppt: "sheets",
  png: "images", jpg: "images", jpeg: "images", webp: "images", tif: "images", tiff: "images", bmp: "images",
  mp4: "videos", mkv: "videos", mov: "videos", avi: "videos", webm: "videos", m4v: "videos",
  py: "code", rs: "code", js: "code", ts: "code", html: "code", css: "code", json: "code",
  yaml: "code", yml: "code", toml: "code", sh: "code",
};

export const familyOf = (ext: string): Family => MAP[ext.toLowerCase()] ?? "other";
export const familyMeta = (f: Family) => FAMILIES.find((x) => x.id === f)!;
export const hasPreview = (ext: string) =>
  ["png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "pdf", "mp4", "mkv", "mov", "avi", "webm", "m4v"]
    .includes(ext.toLowerCase());

/** 75.3 -> "1:15", 3725 -> "1:02:05" */
export function fmtTime(sec: number): string {
  const s = Math.max(0, Math.round(sec));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const two = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${two(m)}:${two(r)}` : `${m}:${two(r)}`;
}

/** "C:\Users\k\Documents\Q3\notes.txt" -> "…\Documents\Q3" (folder only, shortened) */
export function shortFolder(path: string, keep = 3): string {
  const sep = path.includes("\\") ? "\\" : "/";
  const parts = path.split(sep).filter(Boolean);
  parts.pop();
  if (parts.length <= keep) return (path.startsWith(sep) ? sep : "") + parts.join(sep);
  return "…" + sep + parts.slice(-keep).join(sep);
}

export const fmtInt = (n: number | undefined) => (n ?? 0).toLocaleString();
