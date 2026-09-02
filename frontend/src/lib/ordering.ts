/**
 * What the visitor clicked on genmars.co.ke, carried across the sign-in trip.
 *
 * ── WHY THIS NEEDS TO PERSIST AT ALL ────────────────────────────────────────
 *
 * Ordering a service sends someone from genmars.co.ke/services to this portal
 * with ?service=<slug>&tier=<label>. Between arriving and submitting they go
 * through sign-up or sign-in, an emailed verification code, and possibly a
 * different browser tab. Query parameters do not survive that; by the time the
 * onboarding form is submitted the URL is long since rewritten.
 *
 * So the selection is captured the moment it arrives and read back at submit.
 * Losing it is not fatal — the enquiry still gets filed, and the backend drops
 * an unrecognised service rather than refusing — but the commercial partners
 * then open a request with no idea which of the seven offerings it is about.
 *
 * ── WHY sessionStorage ──────────────────────────────────────────────────────
 *
 * It is scoped to the tab and cleared when it closes, which matches the
 * lifetime of one sign-up. localStorage would leave a stale "they wanted
 * SecureCare Plus" lying around to attach itself to an unrelated enquiry
 * months later, which is worse than no attribution.
 */

const KEY = "gm-ordering";

/** The tier LABEL, not a slug — it is shown back to the person and stored as
    a historical fact. Bounded because it arrives from a query string. */
const MAX_TIER = 120;
const MAX_SERVICE = 120;

export type Ordering = { service: string; tier: string };

const EMPTY: Ordering = { service: "", tier: "" };

/**
 * Capture ?service= and ?tier= if present, and remember them.
 *
 * Called on every auth screen rather than only the first, because a visitor
 * may land on sign-in or sign-up depending on whether they already have an
 * account, and either can be the entry point from the website.
 */
export function captureOrdering(): Ordering {
  if (typeof window === "undefined") return EMPTY;

  const params = new URLSearchParams(window.location.search);
  const service = (params.get("service") ?? "").slice(0, MAX_SERVICE);
  const tier = (params.get("tier") ?? "").slice(0, MAX_TIER);

  if (service || tier) {
    const value: Ordering = { service, tier };
    try {
      window.sessionStorage.setItem(KEY, JSON.stringify(value));
    } catch {
      // Private browsing, or storage disabled. The enquiry is still filed;
      // it simply arrives without the service label.
    }
    return value;
  }

  return readOrdering();
}

export function readOrdering(): Ordering {
  if (typeof window === "undefined") return EMPTY;
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return EMPTY;
    const parsed = JSON.parse(raw) as Partial<Ordering>;
    return {
      service: String(parsed.service ?? "").slice(0, MAX_SERVICE),
      tier: String(parsed.tier ?? "").slice(0, MAX_TIER),
    };
  } catch {
    // Storage unavailable, or somebody hand-edited it into nonsense. Either
    // way this is attribution, not authorisation — degrade to none.
    return EMPTY;
  }
}

/** After the enquiry is filed. Leaving it would attach the same selection to
    a second enquiry from the same tab. */
export function clearOrdering(): void {
  try {
    window.sessionStorage.removeItem(KEY);
  } catch {
    // Nothing to do.
  }
}


/**
 * A readable name for the slug, for one confirmation line.
 *
 * Deliberately derived rather than looked up. The alternative is a map of
 * slug -> display name kept here, which would be a second copy of the
 * catalogue in a codebase that does not own it — and the two would disagree
 * the first time a service was renamed on the website.
 *
 * "custom-development" -> "Custom development". Close enough for "you came
 * from X"; the authoritative name is on the enquiry once it is filed, because
 * the backend resolves the slug against the real Service row.
 */
export function serviceLabel(slug: string): string {
  if (!slug) return "";
  const words = slug.replace(/-/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
