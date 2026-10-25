import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { familyOf, hasPreview } from "../lib/files";
import { FileTile } from "./Icons";

/** Thumbnail for images / PDFs / videos (a frame at `t` seconds) (served by /api/thumb), coloured type tile for everything else. */
export function FileVisual({ path, ext, size = 40, thumb = 96, className = "", t }: {
  path: string; ext: string; size?: number; thumb?: number; className?: string; t?: number | null;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [path]);
  const family = familyOf(ext);
  if (hasPreview(ext) && !failed) {
    return (
      <span className={`fthumb ${className}`} style={{ width: size, height: size }}>
        <img src={api.thumbUrl(path, thumb, t)} alt="" loading="lazy" decoding="async"
          onError={() => setFailed(true)} draggable={false} />
      </span>
    );
  }
  return <span className={className}><FileTile ext={ext} family={family} size={size} /></span>;
}
