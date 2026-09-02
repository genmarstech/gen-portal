/**
 * Cross-site return — bringing a visitor back to genmars.co.ke after they have
 * an account here.
 *
 * ── THE JOURNEY THIS SERVES ─────────────────────────────────────────────────
 * The marketing site sends people here before they can request work, so that a
 * client has a dashboard from the first day rather than after the first
 * invoice. That trip is only worth making if it ends where it started, so the
 * site appends `?return=https://genmars.co.ke/request/` and this module brings
 * them home.
 *
 * ── WHY AN ALLOWLIST, AND WHY IT IS NOT NEGOTIABLE ──────────────────────────
 * A redirect parameter on an authentication screen is the classic open-redirect
 * phishing primitive: `app.genmars.co.ke/sign-in?return=https://evil.example`
 * is a link that starts on our real domain, over our real TLS, showing our real
 * brand — and ends on someone else's password form. The visitor did everything
 * right and still lost the credential.
 *
 * So the rule is a fixed allowlist of ORIGINS we own, checked after parsing —
 * never a `startsWith` on the raw string, which `https://genmars.co.ke.evil.example`
 * walks straight through. Anything that does not parse, or parses to an origin
 * not on the list, is dropped silently and the visitor lands on the dashboard.
 * Dropping is always safe; the worst case is one extra click.
 *
 * DO NOT add a wildcard, a subdomain pattern, or a value read from the query
 * string to ALLOWED_ORIGINS.
 */

import { useEffect, useState } from "react";

/** Origins we own and are willing to hand a browser back to. */
const ALLOWED_ORIGINS: readonly string[] = [
  "https://genmars.co.ke",
  "https://www.genmars.co.ke",
];

/**
 * The static site runs on :3000 in development. Gated on NODE_ENV so the
 * plaintext origin cannot exist in a production bundle — Next inlines this at
 * build time and the branch is removed entirely.
 */
const DEV_ORIGINS: readonly string[] =
  process.env.NODE_ENV === "production" ? [] : ["http://localhost:3000"];

/** The query parameter both sides agree on. Changing it breaks gen-website. */
export const RETURN_PARAM = "return";

/**
 * Validate a candidate return URL.
 *
 * Returns the normalised URL when it is one of ours, and null for everything
 * else — absent, malformed, relative, a `javascript:` payload, a lookalike
 * host, or a legitimate site we simply do not send people to.
 */
export function safeReturnTo(raw: string | null | undefined): string | null {
  if (!raw) return null;

  /*
   * An INTERNAL path, e.g. "/order?service=implementation".
   *
   * Added for the ordering flow: someone who clicks "Order Business Setup"
   * while signed out has to be sent to /sign-in and then back to /order, and
   * that destination is this origin rather than the marketing site.
   *
   * The checks are the standard open-redirect defence and each one matters:
   *
   *   · must start with a single "/" — "//evil.com" is protocol-relative and
   *     a browser treats it as an absolute URL to another host, which is the
   *     classic way this exact check gets bypassed
   *   · no backslash — some browsers normalise "/\evil.com" the same way
   *   · no scheme — "/javascript:..." cannot reach here, but a value that
   *     parses as a URL later might, so anything with a colon before the
   *     first slash is refused outright
   */
  if (raw.startsWith("/")) {
    if (raw.startsWith("//") || raw.startsWith("/\\")) return null;
    if (/^\/[^/?#]*:/.test(raw)) return null;
    return raw;
  }

  let url: URL;
  try {
    // No base argument: a relative value has already been handled above, so
    // anything reaching here is meant to be absolute.
    url = new URL(raw);
  } catch {
    return null;
  }

  const allowed = [...ALLOWED_ORIGINS, ...DEV_ORIGINS];
  if (!allowed.includes(url.origin)) return null;

  return url.toString();
}

/** Is this return target on this origin rather than the marketing site? */
export function isInternalReturn(returnTo: string): boolean {
  return returnTo.startsWith("/");
}

/**
 * Read and validate the return target from the current URL.
 *
 * Client-side only — it reads `window.location`. Every caller is inside a
 * "use client" page that has already mounted.
 */
export function readReturnTo(): string | null {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.search);
  return safeReturnTo(params.get(RETURN_PARAM));
}

/**
 * Carry the return target onto the next internal step.
 *
 * The API's `next` is a path, sometimes already carrying a query string
 * (`/verify?email=…`), so this appends rather than assuming it can add a "?".
 */
export function withReturnTo(next: string, returnTo: string | null): string {
  if (!returnTo) return next;
  const separator = next.includes("?") ? "&" : "?";
  return `${next}${separator}${RETURN_PARAM}=${encodeURIComponent(returnTo)}`;
}

/**
 * Is this the end of the road?
 *
 * The API answers "where does this account belong right now" with /verify,
 * /onboarding or /dashboard, in that order. Only /dashboard means the account
 * is finished — verified, with an organisation — and only then is it honest to
 * send someone back to the site saying they have an account.
 *
 * Returning early, at /verify, would drop an unverified visitor back onto
 * genmars.co.ke believing they were set up, with no dashboard to return to.
 */
function isComplete(next: string): boolean {
  return next.split("?")[0] === "/dashboard";
}

/**
 * Where to go after any auth step.
 *
 * `router.push` for internal steps; a full navigation for the return, because
 * the destination is another origin and the Next router cannot route to it.
 */
export function advance(
  router: { push: (href: string) => void },
  next: string,
  returnTo: string | null,
): void {
  if (returnTo && isComplete(next)) {
    // Internal targets go through the router, which keeps the React tree and
    // the session cookie; a full navigation to our own origin would work but
    // costs a reload for no reason. External ones cannot use the router at
    // all — it has no idea what genmars.co.ke is.
    if (isInternalReturn(returnTo)) {
      router.push(returnTo);
    } else {
      window.location.assign(returnTo);
    }
    return;
  }
  router.push(withReturnTo(next, returnTo));
}

/**
 * The return target for the current page, as React state.
 *
 * Read in an effect rather than during render on purpose. These pages are
 * prerendered, so `window` does not exist on the first pass; reading the query
 * string during render would either crash the build or bake in `null` forever.
 * Nothing needs the value before submit, which is long after mount.
 *
 * A hook, so it lives here with the validation rather than being reimplemented
 * — slightly differently, eventually incorrectly — on five screens.
 *
 * NOT for guard effects that redirect on mount: this resolves one render late,
 * so such an effect sees null on its first pass and loses the return target.
 * Call readReturnTo() directly there — see the onboarding guard.
 */
export function useReturnTo(): string | null {
  const [returnTo, setReturnTo] = useState<string | null>(null);
  useEffect(() => setReturnTo(readReturnTo()), []);
  return returnTo;
}
