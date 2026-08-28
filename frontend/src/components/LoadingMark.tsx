import styles from "./LoadingMark.module.css";

type Props = {
  /** Rendered size in px. 16 for inline/button use, 28 corner, 56+ overlay. */
  size?: number;
  /** Accessible label. Pass null for decorative use beside visible text. */
  label?: string | null;
  className?: string;
};

/**
 * The loading mark — the Orbit G, in motion.
 *
 * The geometry is the real mark from 06-brand/logo/svg/genmars-mark-color.svg,
 * unaltered: the G is a planet, the ellipse is its trajectory at -30deg.
 *
 * The loading state is expressed by sending a light around that trajectory —
 * a dashed segment whose offset animates, so a short arc travels the orbit like
 * a body completing a pass. Nothing is stretched, recoloured outside the palette
 * or re-proportioned, which keeps it compliant with the mark rules in
 * 06-brand/README.md while still reading unmistakably as "working".
 *
 * The ellipse circumference is ~242 units (Ramanujan's approximation for
 * a=55, b=17), which is where the dasharray and offset numbers come from.
 *
 * Under prefers-reduced-motion the orbit stops travelling and the whole mark
 * breathes on opacity instead — still legibly "busy", with no motion.
 */
export function LoadingMark({ size = 28, label = "Loading", className }: Props) {
  return (
    <span
      className={`${styles.wrap} ${className ?? ""}`}
      role={label ? "status" : undefined}
      aria-live={label ? "polite" : undefined}
    >
      <svg
        viewBox="0 0 120 120"
        width={size}
        height={size}
        className={styles.svg}
        aria-hidden="true"
        focusable="false"
      >
        {/* Planet — the G. Holds still; it is the thing being orbited. */}
        <g className={styles.planet}>
          <path
            d="M90.8 45.6 A34 34 0 1 0 90.8 74.4"
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
        </g>

        {/* The trajectory, dimmed — the path the light travels. */}
        <ellipse
          cx="60"
          cy="60"
          rx="55"
          ry="17"
          fill="none"
          stroke="var(--mark-orbit, #DB7B51)"
          strokeWidth="4"
          transform="rotate(-30 60 60)"
          className={styles.track}
        />

        {/* The travelling light. */}
        <ellipse
          cx="60"
          cy="60"
          rx="55"
          ry="17"
          fill="none"
          stroke="var(--mark-orbit, #DB7B51)"
          strokeWidth="4"
          strokeLinecap="round"
          transform="rotate(-30 60 60)"
          className={styles.comet}
        />
      </svg>

      {label ? <span className="visually-hidden">{label}</span> : null}
    </span>
  );
}
