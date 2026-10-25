/**
 * Print-shop pieces for the "Cobalt" look: shared SVG filters (rough stamp edges,
 * ink speckle), the paper/fabric texture layer, and the DeepFind stamp logo.
 * Everything is static — no animation, no glow.
 */

/** Mount once. Use with `filter: url(#rough)` / `url(#rough-strong)` in CSS. */
export function PrintFilters() {
  return (
    <svg width="0" height="0" style={{ position: "absolute" }} aria-hidden="true" focusable="false">
      <defs>
        {/* slightly chewed edges, like a rubber stamp */}
        <filter id="rough" x="-5%" y="-5%" width="110%" height="110%">
          <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="4" result="n" />
          <feDisplacementMap in="SourceGraphic" in2="n" scale="2.2" xChannelSelector="R" yChannelSelector="G" />
        </filter>
        {/* heavy roller-print: displaced edges + ink drop-outs inside the shapes */}
        <filter id="rough-strong" x="-5%" y="-5%" width="110%" height="110%">
          <feTurbulence type="fractalNoise" baseFrequency="0.04 0.3" numOctaves="2" seed="11" result="edge" />
          <feDisplacementMap in="SourceGraphic" in2="edge" scale="6" xChannelSelector="R" yChannelSelector="G" result="d" />
          {/* dry-brush drag marks: long horizontal streaks where the ink didn't take */}
          <feTurbulence type="fractalNoise" baseFrequency="0.006 0.11" numOctaves="4" seed="3" result="drag" />
          <feColorMatrix in="drag" type="matrix"
            values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 -7 4.85" result="holes" />
          <feComposite in="d" in2="holes" operator="in" />
        </filter>
      </defs>
    </svg>
  );
}

/** Woven paper/fabric grain over the ink-blue ground. */
export function Paper({ on = true }: { on?: boolean }) {
  return <div className={`paper ${on ? "" : "off"}`} aria-hidden="true" />;
}

/** "DF" knocked out of an off-white stamp. */
export function LogoMark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden="true" className="logo-mark">
      <g filter="url(#rough)">
        <rect x="1" y="1" width="38" height="38" fill="var(--paper)" />
        <text x="20" y="28.5" textAnchor="middle" fontFamily="Archivo Variable, Archivo, sans-serif"
          fontWeight="900" fontSize="22" letterSpacing="-1" fill="var(--blue)"
          style={{ fontStretch: "75%" }}>DF</text>
      </g>
    </svg>
  );
}
