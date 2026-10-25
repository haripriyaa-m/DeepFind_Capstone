/** Line icons (1.8px stroke, round caps) + the file-type tile. */
import { SVGProps } from "react";
import { Family } from "../lib/files";

type P = SVGProps<SVGSVGElement>;
const s = (p: P) => ({
  width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
  strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
  "aria-hidden": true, ...p,
});

export const IcSearch = (p: P) => (<svg {...s(p)}><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></svg>);
export const IcFolder = (p: P) => (<svg {...s(p)}><path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v7a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 16.5z" /></svg>);
export const IcPulse = (p: P) => (<svg {...s(p)}><path d="M3 12h4l2.5-6 5 12 2.5-6h4" /></svg>);
export const IcSliders = (p: P) => (<svg {...s(p)}><path d="M4 7h10M18 7h2M4 17h4M12 17h8" /><circle cx="16" cy="7" r="2" /><circle cx="10" cy="17" r="2" /></svg>);
export const IcOpen = (p: P) => (<svg {...s(p)}><path d="M14 4h6v6M20 4l-9 9M18 14v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4" /></svg>);
export const IcCopy = (p: P) => (<svg {...s(p)}><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" /></svg>);
export const IcPlus = (p: P) => (<svg {...s(p)}><path d="M12 5v14M5 12h14" /></svg>);
export const IcX = (p: P) => (<svg {...s(p)}><path d="M6 6l12 12M18 6 6 18" /></svg>);
export const IcTrash = (p: P) => (<svg {...s(p)}><path d="M4 7h16M9 7V5h6v2M6.5 7l1 12h9l1-12" /></svg>);
export const IcRefresh = (p: P) => (<svg {...s(p)}><path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7" /></svg>);
export const IcWarn = (p: P) => (<svg {...s(p)}><path d="M12 4 2.8 19.5h18.4z" /><path d="M12 10v4M12 17h.01" /></svg>);
export const IcCheck = (p: P) => (<svg {...s(p)}><path d="m5 12.5 4.5 4.5L19 7.5" /></svg>);
export const IcClock = (p: P) => (<svg {...s(p)}><circle cx="12" cy="12" r="8" /><path d="M12 8v4l2.5 2" /></svg>);
export const IcSpark = (p: P) => (<svg {...s(p)}><path d="M12 3c.8 4.4 3.6 7.2 8 8-4.4.8-7.2 3.6-8 8-.8-4.4-3.6-7.2-8-8 4.4-.8 7.2-3.6 8-8z" /></svg>);
export const IcDownload = (p: P) => (<svg {...s(p)}><path d="M12 4v11M7 10l5 5 5-5M5 20h14" /></svg>);
export const IcCpu = (p: P) => (<svg {...s(p)}><rect x="6" y="6" width="12" height="12" rx="2" /><path d="M10 10h4v4h-4zM9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" /></svg>);
export const IcFile = (p: P) => (<svg {...s(p)}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></svg>);
export const IcImage = (p: P) => (<svg {...s(p)}><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="9" cy="10" r="1.8" /><path d="m21 16-5-5-9 9" /></svg>);
export const IcAlert = (p: P) => (<svg {...s(p)}><circle cx="12" cy="12" r="8.5" /><path d="M12 8v5M12 16h.01" /></svg>);
export const IcExternal = (p: P) => (<svg {...s(p)}><path d="M5 12h14M13 6l6 6-6 6" /></svg>);

/** Outlined stamp with the extension — reads like a printed label, not an app icon. */
export function FileTile({ ext, family, size = 40 }: { ext: string; family: Family; size?: number }) {
  const label = (ext || "file").slice(0, 4).toUpperCase();
  return (
    <span className="ftile" data-family={family} style={{ width: size, height: size }}>
      <b style={{ fontSize: Math.max(9, size * 0.26) }}>{label}</b>
    </span>
  );
}
