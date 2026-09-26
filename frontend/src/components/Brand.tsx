/**
 * ══════════════════════════════════════════════════════════════════════════
 * THE G'S ARC WAS OPENED FROM ±25.1° TO ±42° ON 2026-09-26, AND THE OLD
 * GEOMETRY WAS MEASURABLY BROKEN.
 *
 * The arc used to run from ±25.1° with a 9-unit round cap on each end.
 * Measure what that leaves between the cap and the crossbar:
 *
 *     upper arc end, incl. round cap   reaches y = 54.6
 *     crossbar top edge                sits at  y = 55.5
 *     clear counter                    0.92px   — in a 120-unit box
 *
 * Both of the G's counters were sealed to under a pixel, so at every size
 * anyone actually saw — favicon, header, loading mark, the og image — the
 * right-hand side filled in solid and the mark read as a C with a nub. It
 * was not a rendering artefact and no amount of lighting fixed it: there
 * was no aperture to light.
 *
 * At ±42° the counter opens to 9.25px and the crossbar reads as a crossbar.
 * Nothing else moved: same centre (60,60), same radius 34, same stroke
 * weight, same crossbar, same silhouette, same ring. The arc is 17° shorter
 * at each end and that is the entire change.
 *
 * ⚠ IT IS CHANGED IN ALL FIVE PLACES AT ONCE — gen-website, internals-tm,
 * gen-portal, business-os and the promo film — plus both .svg files and the
 * raster icons regenerated from them. The mark existing in two shapes is
 * worse than either shape. If this is ever revised again, revise it
 * everywhere in the same change.
 *
 * The old path, for the record:  M90.8 45.6 A34 34 0 1 0 90.8 74.4
 * ══════════════════════════════════════════════════════════════════════════
 */
/**
 * Brand marks — Orbit G and the wordmark.
 *
 * Geometry is transcribed exactly from 06-brand/logo/svg/, WITH ONE
 * DELIBERATE CORRECTION — see the banner above Mark. Do not eyeball any
 * other adjustment; re-export from the design canvas (06-brand/source/).
 *
 * The wordmark is drawn as inline SVG rather than loaded as <img> for two
 * reasons: the <text> nodes need the page's Jost webfont to render correctly,
 * and inline SVG inherits currentColor so one component serves light and dark.
 *
 * TWO RULES THAT DO NOT BEND (06-brand/README.md):
 *   - Never re-space the wordmark below +300 tracking (0.3em).
 *   - Never bar the A. The A is a custom barless apex glyph, not Jost's stock A.
 *     That is why the wordmark is split into "GENM" + glyph + "RS".
 */

type MarkProps = {
  /** Rendered size in px. Minimum 24 — 06-brand/README.md. */
  size?: number;
  className?: string;
};

/**
 * Orbit G. Planet = G, ellipse = trajectory at -30deg.
 * Clear space equal to the ring height (x) is required on all four sides;
 * callers are responsible for that margin.
 */
export function Mark({ size = 40, className }: MarkProps) {
  return (
    <svg
      viewBox="0 0 120 120"
      width={size}
      height={size}
      className={className}
      role="img"
      aria-label="Genmars"
      focusable="false"
    >
      <path
        d="M85.27 37.25 A34 34 0 1 0 85.27 82.75"
        fill="none"
        stroke="var(--mark-g, #8B5A48)"
        strokeWidth="9"
        strokeLinecap="round"
      />
      <path
        d="M74 60 H92.5"
        fill="none"
        stroke="var(--mark-g, #8B5A48)"
        strokeWidth="9"
        strokeLinecap="round"
      />
      <ellipse
        cx="60"
        cy="60"
        rx="55"
        ry="17"
        fill="none"
        stroke="var(--mark-orbit, #DB7B51)"
        strokeWidth="4"
        transform="rotate(-30 60 60)"
      />
    </svg>
  );
}

type WordmarkProps = {
  /** Rendered width in px. Minimum 110 for the horizontal lockup. */
  width?: number;
  /** Show the "NEXT-GENERATION SOFTWARE" tagline beneath the wordmark. */
  withTagline?: boolean;
  className?: string;
};

/**
 * Horizontal lockup: mark + wordmark, optional tagline.
 *
 * Tracking check, against 06-brand/README.md:
 *   wordmark  letterSpacing 13.2 / fontSize 44 = 0.300em  = +300 exactly
 *   tagline   letterSpacing 4.8  / fontSize 11 = 0.436em  ~ +440
 */
export function Wordmark({
  width = 220,
  withTagline = true,
  className,
}: WordmarkProps) {
  const height = (width / 640) * (withTagline ? 160 : 140);

  return (
    <svg
      viewBox={`0 0 640 ${withTagline ? 160 : 140}`}
      width={width}
      height={height}
      className={className}
      role="img"
      aria-label="Genmars Tech"
      focusable="false"
    >
      <g transform="translate(8,26)">
        <path
          d="M85.27 37.25 A34 34 0 1 0 85.27 82.75"
          fill="none"
          stroke="var(--mark-g, #8B5A48)"
          strokeWidth="9"
          strokeLinecap="round"
          transform="scale(0.9)"
        />
        <path
          d="M74 60 H92.5"
          fill="none"
          stroke="var(--mark-g, #8B5A48)"
          strokeWidth="9"
          strokeLinecap="round"
          transform="scale(0.9)"
        />
        <ellipse
          cx="60"
          cy="60"
          rx="55"
          ry="17"
          fill="none"
          stroke="var(--mark-orbit, #DB7B51)"
          strokeWidth="4"
          transform="scale(0.9) rotate(-30 60 60)"
        />
      </g>

      {/* Wordmark, split around the custom A. Jost Regular, +300 tracking. */}
      <text
        x="222"
        y="88"
        fontFamily="var(--font-jost), Jost, sans-serif"
        fontSize="44"
        fontWeight="400"
        letterSpacing="13.2"
        fill="var(--wordmark-ink, #2E2B34)"
      >
        GENM
      </text>

      {/* The barless A — stroke 13/100 em, width .66em, height .72em cap. */}
      <g transform="translate(400,56) scale(0.32)">
        <path
          d="M8 95 L46 8 L84 95"
          fill="none"
          stroke="var(--wordmark-ink, #2E2B34)"
          strokeWidth="13"
          strokeLinejoin="miter"
        />
      </g>

      <text
        x="446"
        y="88"
        fontFamily="var(--font-jost), Jost, sans-serif"
        fontSize="44"
        fontWeight="400"
        letterSpacing="13.2"
        fill="var(--wordmark-ink, #2E2B34)"
      >
        RS
      </text>

      {withTagline && (
        <text
          x="223"
          y="114"
          fontFamily="var(--font-jost), Jost, sans-serif"
          fontSize="11"
          fontWeight="300"
          letterSpacing="4.8"
          fill="var(--tagline-ink, #7A7480)"
        >
          NEXT-GENERATION SOFTWARE
        </text>
      )}
    </svg>
  );
}
