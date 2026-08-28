import styles from "./EmptyState.module.css";

/**
 * No orders yet.
 *
 * ── WHY THIS SCREEN IS CAREFUL ──────────────────────────────────────────────
 * Signing up creates an ACCOUNT, not an order. Charter 02 §I gives the
 * commercial partners qualification and the founder a capacity veto, so
 * self-serve signup deliberately cannot route around either. That means an
 * empty dashboard is the ordinary state for a new account, not a failure — and
 * it must not read as one.
 *
 * It also must not imply an engagement that does not exist. No "we'll be in
 * touch shortly", no response-time promise: Charter 03 §IV's standing rule is
 * that we never put a commitment in front of a client we have not tested under
 * real conditions. So this says what is true — nothing is agreed yet, here is
 * how something gets agreed — and nothing more.
 *
 * `hasEnquiry` splits two situations this screen originally conflated. Someone
 * who has just finished onboarding HAS told us what they need, in detail. The
 * first version asked them "not talked to us yet?", which reads as nobody
 * having so much as opened it — the exact impression a portal exists to avoid.
 */
export function EmptyState({ hasEnquiry = false }: { hasEnquiry?: boolean }) {
  return (
    <div className={`wrap ${styles.wrap}`}>
      <div className={styles.art} aria-hidden="true">
        <WaitingMark />
      </div>

      <div className={styles.copy}>
        <p className="eyebrow">Your work</p>
        <h1 className={styles.title}>
          {hasEnquiry ? "We have what you sent us." : "Nothing is underway yet."}
        </h1>
        <p className="lede">
          {hasEnquiry
            ? "Your enquiry is with us and someone will read it properly. Work appears here once we have talked it through, agreed scope, and signed a statement of work."
            : "Your account is set up. Work appears here once scope is agreed and a statement of work is signed — we create the engagement on our side, and it shows up on this page."}
        </p>

        <ol className={styles.steps}>
          <li className={hasEnquiry ? styles.stepDone : undefined}>
            <span className={styles.stepNum}>01</span>
            <span>
              <strong>
                {hasEnquiry
                  ? "You have told us what is breaking."
                  : "A conversation about the problem."}
              </strong>{" "}
              {hasEnquiry
                ? "The next move is ours: we work out whether we are the right people for it, and come back to you either way."
                : "What is actually breaking, what it costs you, and whether we are the right people for it."}
            </span>
          </li>
          <li>
            <span className={styles.stepNum}>02</span>
            <span>
              <strong>A written statement of work.</strong> Scope, what is
              explicitly excluded, milestones, and price — agreed before anything
              is built.
            </span>
          </li>
          <li>
            <span className={styles.stepNum}>03</span>
            <span>
              <strong>It appears here.</strong> Scope, weekly progress notes,
              milestone status, and your named point of contact.
            </span>
          </li>
        </ol>

        <p className={styles.cta}>
          {hasEnquiry ? (
            <>
              Something to add, or something changed?{" "}
              <a href="mailto:info@genmars.co.ke">Email us</a>.
            </>
          ) : (
            <>
              Not talked to us yet?{" "}
              <a href="https://genmars.co.ke/contact/">Start a conversation</a>.
            </>
          )}
        </p>
      </div>
    </div>
  );
}

/**
 * The orbit with nothing on it yet.
 *
 * Same geometry as the loading mark and the Orbit G — the trajectory at −30°,
 * drawn dashed and empty. It reads as "the track is ready, nothing is on it",
 * which is precisely the state. Static: this is not loading, and an animation
 * here would suggest something is in progress.
 */
function WaitingMark() {
  return (
    <svg viewBox="0 0 240 200" className={styles.svg} focusable="false">
      <ellipse
        cx="120" cy="100" rx="106" ry="33"
        transform="rotate(-30 120 100)"
        className={styles.track}
      />
      <ellipse
        cx="120" cy="100" rx="70" ry="22"
        transform="rotate(-30 120 100)"
        className={styles.trackInner}
      />
      <circle cx="120" cy="100" r="9" className={styles.centre} />
    </svg>
  );
}
