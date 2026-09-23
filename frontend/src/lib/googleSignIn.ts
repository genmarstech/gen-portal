/**
 * What the Google callback's ?error= code means, in words.
 *
 * ── WHY THESE ARE SPECIFIC, WHEN A FAILED PASSWORD SIGN-IN IS NOT ──────────
 *
 * The password form deliberately says one thing for unknown-address and
 * wrong-password, because telling them apart is a free account-enumeration
 * oracle. None of these are reachable without already holding the Google
 * account for the address, so there is nothing to protect: the person can only
 * ever be asking about an address they control.
 *
 * What one message cost instead was somebody stuck on a screen that would not
 * say whether the problem was their Google account, their Genmars account, or
 * our server — and so gave them no way to fix any of the three.
 *
 * ⚠ KEEP THE KEYS IN STEP WITH GOOGLE_ERROR_CODES IN accounts/views.py. An
 *   unknown code falls through to the vague one, which is correct behaviour
 *   and a bad experience — it means the server refused for a reason this file
 *   has not caught up with.
 */
export const GOOGLE_SIGN_IN_ERRORS: Record<string, string> = {
  unverified:
    "Google has not verified the email address on that account, so we cannot " +
    "use it to identify you. Verify it with Google, or sign up with an email " +
    "address and password.",
  inactive:
    "That account has been deactivated. Get in touch if you think that is wrong.",
  locked:
    "Too many failed attempts on that account. Try again in a few minutes — " +
    "the lock clears on its own.",
  expired:
    "That sign-in took too long, or was started in another tab. Try again.",
  unavailable:
    "We could not reach Google just now. Try again in a moment, or sign in " +
    "with your email address and password.",
};

/**
 * Used when the code is one this file does not know.
 *
 * A named constant and not another entry in the map above, because an index
 * lookup is `string | undefined` under noUncheckedIndexedAccess — so a
 * fallback that is itself a lookup does not actually guarantee a message.
 */
export const GOOGLE_SIGN_IN_UNKNOWN =
  "We could not sign you in with Google. Try again.";

/** The message for whatever is in ?error=, or null if it is not ours. */
export function googleSignInError(search: string): string | null {
  const code = new URLSearchParams(search).get("error");
  if (!code) return null;
  return GOOGLE_SIGN_IN_ERRORS[code] ?? GOOGLE_SIGN_IN_UNKNOWN;
}
