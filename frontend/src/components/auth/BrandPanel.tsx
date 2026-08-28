import { useId } from "react";
import styles from "./BrandPanel.module.css";

/**
 * The desktop split-shell brand panel.
 *
 * ── THE ARTWORK ─────────────────────────────────────────────────────────────
 * Original, built from the Orbit G's own geometry. The visual direction came
 * from three reference images now filed in 06-brand/references/direction/ —
 * they are third-party work and cannot ship, but they establish a language
 * worth building to:
 *
 *   dark ground · halftone dot texture · wireframe over solid mass ·
 *   fine technical annotation · one chromatic light source
 *
 * Everything below is drawn, not photographed, and uses only palette colours.
 * Nothing is stock, nothing is borrowed, and nothing asserts a fact — the
 * annotation labels are structural, not fabricated telemetry (Charter 04 §IV
 * rules out decorative numbers that read as claims).
 *
 * The panel keeps the Ignition gradient the design specifies and does NOT flip
 * with the theme: it is a fixed brand surface, and only the working column
 * beside it changes. That is deliberate — a gradient that inverted would make
 * the two themes look like two different products.
 */
export function BrandPanel({
  headline,
  sub,
}: {
  headline?: React.ReactNode;
  sub?: string;
}) {
  const id = useId().replace(/:/g, "");

  return (
    <aside className={styles.panel} aria-hidden="true">
      {/* ---- artwork ---- */}
      <svg
        className={styles.art}
        viewBox="0 0 600 900"
        preserveAspectRatio="xMidYMid slice"
        focusable="false"
      >
        <defs>
          {/* Halftone: a dot grid that thins toward the edges. */}
          <pattern
            id={`dots-${id}`}
            width="7"
            height="7"
            patternUnits="userSpaceOnUse"
          >
            <circle cx="1.4" cy="1.4" r="1.15" fill="#F4EFEC" />
          </pattern>
          <radialGradient id={`fade-${id}`} cx="72%" cy="24%" r="58%">
            <stop offset="0%" stopColor="#fff" stopOpacity="0.5" />
            <stop offset="65%" stopColor="#fff" stopOpacity="0.12" />
            <stop offset="100%" stopColor="#fff" stopOpacity="0" />
          </radialGradient>
          <mask id={`halftone-${id}`}>
            <rect width="600" height="900" fill={`url(#fade-${id})`} />
          </mask>
          <clipPath id={`body-${id}`}>
            <circle cx="470" cy="248" r="172" />
          </clipPath>
        </defs>

        {/* Halftone field over the whole panel, strongest at the body. */}
        <rect
          width="600"
          height="900"
          fill={`url(#dots-${id})`}
          mask={`url(#halftone-${id})`}
          opacity="0.5"
        />

        {/*
          The body sits UPPER-RIGHT and is cropped by the panel edge, rather
          than centred. Centred, it sat directly behind the headline and the two
          fought; a heavy scrim fixed the legibility and killed the gradient.
          Moving it out of the text's way is the better fix — the drawing keeps
          its presence and the Ignition warmth survives.
        */}
        <g clipPath={`url(#body-${id})`}>
          <circle cx="470" cy="248" r="172" fill="#1B171F" opacity="0.34" />
          {[-118, -64, 0, 64, 118].map((k) => (
            <ellipse
              key={k}
              cx="470"
              cy={248 + k}
              rx={Math.round(Math.sqrt(172 * 172 - k * k))}
              ry={Math.round(Math.sqrt(172 * 172 - k * k) * 0.2)}
              className={styles.wire}
            />
          ))}
          {[-136, -68, 0, 68, 136].map((x) => (
            <path
              key={x}
              d={`M ${470 + x} 78 Q ${470 + x * 1.5} 248 ${470 + x} 418`}
              className={styles.wire}
            />
          ))}
          <circle cx="470" cy="248" r="172" fill={`url(#term-${id})`} />
        </g>
        <defs>
          <radialGradient id={`term-${id}`} cx="72%" cy="72%" r="62%">
            <stop offset="0%" stopColor="#120F16" stopOpacity="0.55" />
            <stop offset="100%" stopColor="#120F16" stopOpacity="0" />
          </radialGradient>
        </defs>

        <circle cx="470" cy="248" r="172" className={styles.rim} />

        {/* The mark's own trajectory, at −30°, sweeping down across the panel. */}
        <ellipse
          cx="470"
          cy="248"
          rx="272"
          ry="84"
          transform="rotate(-30 470 248)"
          className={styles.orbit}
        />
        <ellipse
          cx="470"
          cy="248"
          rx="272"
          ry="84"
          transform="rotate(-30 470 248)"
          className={styles.comet}
        />

        {/* ---- technical annotation ---- */}
        {/*
          Structural only — no metrics, no counts, nothing that reads as a claim
          (Charter 04 §IV rules out decorative numbers that look like facts).

          Positions avoid the three text zones: the lockup at the top, the
          headline in the middle, and the tagline at the foot. The first pass
          put ORBIT through the headline and the registration line straight
          under the tagline.
        */}
        <g className={styles.anno}>
          <path d="M 60 214 H 176" />
          <path d="M 60 214 V 200" />
          <circle cx="176" cy="214" r="3" />
          <path d="M 470 76 V 34" />
          <circle cx="470" cy="76" r="3" />
          <path d="M 60 690 H 540" strokeDasharray="2 6" />
        </g>
        <g className={styles.annoText}>
          <text x="60" y="194">ORBIT · −30°</text>
          <text x="452" y="26">R 172</text>
          <text x="60" y="710">GENMARS TECH LIMITED · BN-93S95J2J</text>
        </g>
      </svg>

      {/* ---- content ---- */}
      <div className={styles.top}>
        <BrandLockup />
      </div>

      <div className={styles.middle}>
        <h2 className={styles.headline}>
          {headline ?? (
            <>
              Production software,
              <br />
              not prototypes.
            </>
          )}
        </h2>
        <p className={styles.sub}>
          {sub ??
            "Custom systems, mobile-money and payments integration, and the infrastructure that keeps them running."}
        </p>
      </div>

      <p className={styles.tagline}>Next-generation software</p>
    </aside>
  );
}

/**
 * Wordmark in panel ink. Split around the custom barless A — the wordmark's A
 * is a bare apex, never Jost's stock glyph (06-brand/README.md).
 */
function BrandLockup() {
  return (
    <div className={styles.lockup}>
      <svg viewBox="0 0 120 120" className={styles.lockupMark} focusable="false">
        <path
          d="M90.8 45.6 A34 34 0 1 0 90.8 74.4"
          fill="none"
          stroke="currentColor"
          strokeWidth="9"
          strokeLinecap="round"
        />
        <path
          d="M74 60 H92.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="9"
          strokeLinecap="round"
        />
        <ellipse
          cx="60"
          cy="60"
          rx="55"
          ry="17"
          fill="none"
          stroke="currentColor"
          strokeWidth="4"
          transform="rotate(-30 60 60)"
        />
      </svg>

      <span className={styles.lockupWord}>
        {"GENM".split("").map((c, i) => (
          <span key={i}>{c}</span>
        ))}
        <svg viewBox="0 0 92 100" className={styles.lockupA} focusable="false">
          <path
            d="M8 95 L46 8 L84 95"
            fill="none"
            stroke="currentColor"
            strokeWidth="13"
            strokeLinejoin="miter"
          />
        </svg>
        <span>R</span>
        <span>S</span>
      </span>
    </div>
  );
}
