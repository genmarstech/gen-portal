import { BrandPanel } from "./BrandPanel";
import { ThemeToggle } from "../ThemeToggle";
import styles from "./AuthShell.module.css";

/**
 * The auth layout.
 *
 * Desktop (≥62rem): split shell — fixed brand panel at 530px, form column
 * beside it, form body 400px wide.
 * Mobile: single column, 375px design width, form only. The brand panel is
 * decoration and does not get to compete for space on a phone.
 *
 * Measurements are taken from the design artboards, not approximated:
 * gap 26px between blocks, 8px inside a label/field pair, 18px between fields.
 */
export function AuthShell({
  eyebrow,
  title,
  lede,
  children,
  footer,
  panelHeadline,
  panelSub,
}: {
  eyebrow: string;
  title: string;
  lede?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  panelHeadline?: React.ReactNode;
  panelSub?: string;
}) {
  return (
    <div className={styles.shell}>
      <BrandPanel headline={panelHeadline} sub={panelSub} />

      <main className={styles.column}>
        <div className={styles.toggle}>
          <ThemeToggle />
        </div>

        <div className={styles.body}>
          <MobileLockup />

          <header className={styles.head}>
            <p className={styles.eyebrow}>{eyebrow}</p>
            <h1 className={styles.title}>{title}</h1>
            {lede ? <p className={styles.lede}>{lede}</p> : null}
          </header>

          {children}

          {footer ? <div className={styles.footer}>{footer}</div> : null}
        </div>
      </main>
    </div>
  );
}

/** Shown only on mobile, where there is no brand panel to carry the mark. */
function MobileLockup() {
  return (
    <div className={styles.mobileLockup} aria-hidden="true">
      <svg viewBox="0 0 120 120" className={styles.mobileMark} focusable="false">
        <path d="M90.8 45.6 A34 34 0 1 0 90.8 74.4" fill="none"
              stroke="var(--mark-g)" strokeWidth="9" strokeLinecap="round" />
        <path d="M74 60 H92.5" fill="none"
              stroke="var(--mark-g)" strokeWidth="9" strokeLinecap="round" />
        <ellipse cx="60" cy="60" rx="55" ry="17" fill="none"
                 stroke="var(--mark-orbit)" strokeWidth="4"
                 transform="rotate(-30 60 60)" />
      </svg>
      <span className={styles.mobileWord}>
        <span>G</span><span>E</span><span>N</span><span>M</span>
        <svg viewBox="0 0 92 100" className={styles.mobileA} focusable="false">
          <path d="M8 95 L46 8 L84 95" fill="none" stroke="currentColor"
                strokeWidth="13" strokeLinejoin="miter" />
        </svg>
        <span>R</span><span>S</span>
      </span>
    </div>
  );
}
