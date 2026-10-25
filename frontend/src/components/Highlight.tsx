import { Fragment, useMemo } from "react";

const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Marks query words inside text. React escapes the text, so no HTML injection. */
export function Highlight({ text, query }: { text: string; query: string }) {
  const parts = useMemo(() => {
    const terms = Array.from(new Set(query.toLowerCase().match(/[\p{L}\p{N}_]{2,}/gu) ?? []));
    if (!terms.length || !text) return [text];
    const re = new RegExp(`(${terms.sort((a, b) => b.length - a.length).map(esc).join("|")})`, "giu");
    return text.split(re);
  }, [text, query]);
  return (
    <>
      {parts.map((p, i) => (i % 2 === 1 ? <mark key={i}>{p}</mark> : <Fragment key={i}>{p}</Fragment>))}
    </>
  );
}
