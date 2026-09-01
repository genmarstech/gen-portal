import styles from "./ReturnNotice.module.css";

/**
 * "You were sent here, and you will be sent back."
 *
 * A visitor bounced off genmars.co.ke mid-task needs two things said before
 * they will spend a minute on a password: why the detour, and that the detour
 * ends. Without it, an unexplained jump to a different domain asking for
 * credentials looks exactly like the thing everyone is told to close.
 *
 * Renders nothing when there is no return target, so every auth screen can
 * include it unconditionally.
 *
 * The URL is displayed as its HOST, not its full href. The host is the part
 * that carries the trust ("this is genmars.co.ke, where I just was"), and it
 * is also the part the allowlist actually checked — showing a long path would
 * imply we vouched for more than we did.
 */
export function ReturnNotice({ returnTo }: { returnTo: string | null }) {
  if (!returnTo) return null;

  let host: string;
  try {
    host = new URL(returnTo).host;
  } catch {
    // Unreachable: safeReturnTo() parsed this already. Failing quiet rather
    // than throwing, because a decorative banner must never take a form down.
    return null;
  }

  return (
    <p className={styles.notice}>
      Set up your account and we will take you straight back to{" "}
      <span className={styles.host}>{host}</span> to finish what you started.
    </p>
  );
}
