"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LoadingMark } from "@/components/LoadingMark";
import {
  ApiError,
  portal,
  session,
  type CatalogueService,
  type ServiceTier,
} from "@/lib/api";
import {
  captureOrdering,
  clearOrdering,
  serviceLabel,
  type Ordering,
} from "@/lib/ordering";
import styles from "./page.module.css";

/**
 * Ordering a service.
 *
 * ── THE BUG THIS PAGE EXISTS TO FIX ─────────────────────────────────────────
 *
 * Every tier on genmars.co.ke used to link to /sign-up. That works exactly
 * once per person. Onboarding creates the organisation and refuses a second
 * time with `already_onboarded`, and the API answers that by redirecting to
 * /dashboard — so an EXISTING client who clicked "Order Business Setup" was
 * dumped on their dashboard with the order silently discarded. No error, no
 * enquiry, and nobody at Genmars ever learned they had asked.
 *
 * So order links now come here instead, and this page routes on the account's
 * actual state rather than assuming everyone is new.
 *
 * ── THE SESSION IS REUSED, NOT RE-ASKED ─────────────────────────────────────
 *
 * A signed-in client with a valid session never sees a sign-in form: the
 * session cookie is checked first and they land straight on the order form.
 * Being asked to sign in again while already signed in is the thing that made
 * the old flow feel broken even when it worked.
 *
 * ── TWO ROUTES OUT, AND EACH FILES EXACTLY ONE ENQUIRY ──────────────────────
 *
 * Signed out, the page offers both and does NOT guess:
 *
 *   · Sign in  → return=/order, back here, this form files the enquiry
 *   · Sign up  → onboarding files it, because onboarding has to collect the
 *                organisation anyway and asking twice would be absurd
 *
 * Sending a new account back here after onboarding would show them this form
 * again immediately after they had filled one in, and a second submit would
 * file a duplicate. Hence two routes rather than one clever one.
 *
 * ── THE TIER CARDS, AND WHAT THEY REMOVE ────────────────────────────────────
 *
 * The three sizes are shown here rather than only on genmars.co.ke, so someone
 * who reached this page without picking one does not have to leave, read the
 * public price list and come back.
 *
 * Picking one ANSWERS the budget question rather than pre-selecting a guess at
 * it: a fixed-price tier is a number we have published, so asking the client
 * to also estimate a range would be asking them to tell us something we told
 * them. The question disappears and the price is shown in its place.
 *
 * The top tier is the exception. It is published as "from KES X" — a floor,
 * not a quote — so the budget question stays, because nothing on the card
 * determines it. Pre-filling a band above a floor would be inventing a number
 * on the client's behalf and then billing against it.
 *
 * Timeline is never derived. A tier describes what we deliver; when the client
 * needs it is a fact about them, and no card knows it.
 */
export default function OrderPage() {
  const router = useRouter();
  const [state, setState] = useState<
    "loading" | "anonymous" | "unverified" | "form" | "done"
  >("loading");
  const [ordering, setOrdering] = useState<Ordering>({ service: "", tier: "" });

  useEffect(() => {
    // Capture first: the query string is gone by the time they come back from
    // signing in, and sessionStorage is what carries it across.
    setOrdering(captureOrdering());

    let cancelled = false;
    session()
      .then((s) => {
        if (cancelled) return;
        if (!s.authenticated) return setState("anonymous");
        if (!s.email_verified) {
          router.push("/verify");
          return;
        }
        // Signed in but never finished signing up: onboarding needs the
        // organisation, and it files the enquiry itself.
        if (s.needs_onboarding) {
          router.push("/onboarding");
          return;
        }
        setState("form");
      })
      .catch(() => {
        if (!cancelled) setState("anonymous");
      });

    return () => {
      cancelled = true;
    };
  }, [router]);

  const heading = ordering.service
    ? `${serviceLabel(ordering.service)}${ordering.tier ? ` · ${ordering.tier}` : ""}`
    : "Tell us what you need";

  if (state === "loading") {
    return (
      <div className={styles.loading}>
        <LoadingMark size={32} label="Loading" />
      </div>
    );
  }

  if (state === "done") {
    return (
      <div className="wrap">
        <div className={styles.panel}>
          <p className={styles.eyebrow}>Received</p>
          <h1 className={styles.title}>That is with us.</h1>
          <p className={styles.body}>
            {/* No response-time promise. Charter 03 §IV — never put a
                commitment in front of a client that has not been tested under
                real conditions. */}
            Someone reads it, and if it is something we can genuinely help
            with, we will get in touch to talk it through. Nothing has been
            charged and no work has started &mdash; that begins once scope is
            agreed and a statement of work is signed.
          </p>
          <p className={styles.actions}>
            <Link href="/dashboard" className={styles.primary}>
              Go to your dashboard
            </Link>
          </p>
        </div>
      </div>
    );
  }

  if (state === "anonymous") {
    return (
      <div className="wrap">
        <div className={styles.panel}>
          <p className={styles.eyebrow}>Ordering</p>
          <h1 className={styles.title}>{heading}</h1>
          <p className={styles.body}>
            Requests live against a client account, so the work, the scope and
            the invoices are all in one place rather than scattered through an
            email thread. Ordering does not charge you and does not start work.
          </p>
          <p className={styles.actions}>
            {/* Internal return. safeReturnTo accepts a same-origin path, and
                refuses "//evil.com" — see returnTo.ts. */}
            <Link href="/sign-in?return=%2Forder" className={styles.primary}>
              Sign in
            </Link>
            <Link href="/sign-up" className={styles.secondary}>
              Create an account
            </Link>
          </p>
          <p className={styles.hint}>
            Either way, {ordering.tier ? "the tier you picked" : "what you were looking at"} comes with you.
          </p>
        </div>
      </div>
    );
  }

  return (
    <OrderForm
      ordering={ordering}
      onPick={setOrdering}
      onFiled={() => {
        clearOrdering();
        setState("done");
      }}
    />
  );
}

/* ── choosing a size ──────────────────────────────────────────────────────── */

/**
 * The tier the visitor arrived with, matched against the real catalogue.
 *
 * The website sends ?tier= as a LABEL ("Business Setup"), not a slug, because
 * that is what it shows on the button. So both are accepted here, and matched
 * case-insensitively — a tier that fails to match simply leaves nothing
 * selected, which is the same state as arriving without one.
 */
function findTier(
  service: CatalogueService | null,
  wanted: string,
): ServiceTier | null {
  if (!service || !wanted) return null;
  const needle = wanted.trim().toLowerCase();
  return (
    service.tiers.find(
      (t) => t.slug.toLowerCase() === needle || t.name.toLowerCase() === needle,
    ) ?? null
  );
}

/**
 * "KES 25,000 one-time", or "From KES 150,000 per month".
 *
 * The unit is never dropped. A tier of a monthly service rendered as a bare
 * number reads as the whole cost of the engagement, which is the kind of error
 * a client discovers on the second invoice.
 */
function priceLine(tier: ServiceTier, unit: string): string {
  if (!tier.price_kes) return "Quoted individually";
  const whole = tier.price_kes.split(".")[0] ?? "0";
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const prefix = tier.is_from ? "From " : "";
  return `${prefix}KES ${grouped}${unit ? ` ${unit}` : ""}`;
}

function TierCards({
  service,
  selected,
  onPick,
}: {
  service: CatalogueService;
  selected: ServiceTier | null;
  onPick: (tier: ServiceTier) => void;
}) {
  return (
    <fieldset className={styles.tiers}>
      <legend className={styles.tiersLegend}>
        Which size? You can change this later &mdash; it tells us where to
        start, not what you are committed to.
      </legend>

      <div className={styles.tierGrid}>
        {service.tiers.map((tier) => {
          const active = selected?.slug === tier.slug;
          return (
            <button
              key={tier.slug}
              type="button"
              className={`${styles.tierCard} ${active ? styles.tierCardOn : ""}`}
              onClick={() => onPick(tier)}
              aria-pressed={active}
            >
              <span className={styles.tierName}>{tier.name}</span>
              <span className={styles.tierPrice}>
                {priceLine(tier, service.price_unit)}
              </span>
              <span className={styles.tierLead}>{tier.lead}</span>
              <ul className={styles.tierIncludes}>
                {tier.includes.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              {tier.is_from && (
                <span className={styles.tierNote}>
                  A starting point. The final figure comes from scope, and we
                  agree it in writing before anything begins.
                </span>
              )}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

function OrderForm({
  ordering,
  onPick,
  onFiled,
}: {
  ordering: Ordering;
  onPick: (next: Ordering) => void;
  onFiled: () => void;
}) {
  const router = useRouter();
  const [problem, setProblem] = useState("");
  const [monthlyCost, setMonthlyCost] = useState("");
  const [timeline, setTimeline] = useState("");
  const [budget, setBudget] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const [catalogue, setCatalogue] = useState<CatalogueService[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    portal
      .catalogue()
      .then((body) => {
        if (!cancelled) setCatalogue(body.services);
      })
      .catch(() => {
        // The form still works without it — the client describes what they
        // need in their own words, which is how this page worked before the
        // cards existed. Degrading to that beats blocking an order on a
        // catalogue request.
        if (!cancelled) setCatalogue([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const service =
    catalogue?.find((s) => s.slug === ordering.service) ?? null;
  const tier = findTier(service, ordering.tier);

  // A published, fixed price answers the budget question outright. A "from"
  // price does not — see the note at the top of this file.
  const budgetIsSettled = tier !== null && !tier.is_from && tier.price_kes !== null;
  const settledBudget =
    service && tier && budgetIsSettled ? priceLine(tier, service.price_unit) : "";

  const heading = service
    ? `${service.name}${tier ? ` · ${tier.name}` : ""}`
    : ordering.service
      ? `${serviceLabel(ordering.service)}${ordering.tier ? ` · ${ordering.tier}` : ""}`
      : "Tell us what you need";

  const submit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      setPending(true);
      setError(null);
      setFieldError(null);
      try {
        await portal.enquiry({
          problem: problem.trim(),
          monthly_cost: monthlyCost.trim(),
          timeline,
          // When a tier settles it, send the published price rather than a
          // band the client never chose. It is the more precise answer and it
          // is one we already stand behind.
          budget_range: budgetIsSettled ? settledBudget : budget,
          service: ordering.service,
          tier: tier ? tier.name : ordering.tier,
        });
        onFiled();
      } catch (err) {
        if (err instanceof ApiError) {
          // 409: signed in, verified, but no organisation. Route rather than
          // showing an error nobody can act on.
          if (err.status === 409) {
            router.push("/onboarding");
            return;
          }
          if (err.field === "problem") setFieldError(err.message);
          else setError(err.message);
        } else {
          setError("Something went wrong. Try again.");
        }
      } finally {
        setPending(false);
      }
    },
    [
      problem,
      monthlyCost,
      timeline,
      budget,
      budgetIsSettled,
      settledBudget,
      tier,
      ordering,
      onFiled,
      router,
    ],
  );

  // Only rendered once there is something to put in it. If the catalogue could
  // not be fetched the page falls back to a single column and the client
  // describes what they need in words, which is how this worked before.
  const hasChoice = catalogue !== null && catalogue.length > 0;

  return (
    <div className="wrap">
      {/*
        ── WHY THE CHOICE COMES FIRST IN THE MARKUP ──────────────────────────
        On a wide screen the rail is placed in the second column by the grid,
        so it reads on the right. On a narrow one it stays where it is in the
        source: ABOVE the questions.

        That is the order that matters. Picking a size removes a question from
        the form below it, so being asked to choose after answering would mean
        answering something that was about to become unnecessary. Placement is
        done with grid columns rather than `order` so the tab sequence and a
        screen reader follow the same sequence a sighted user does.
      */}
      <div className={`${styles.layout} ${hasChoice ? styles.layoutWithRail : ""}`}>
        {hasChoice && (
          <aside className={styles.choice} aria-label="What you are ordering">
            {service ? (
              <>
                <TierCards
                  service={service}
                  selected={tier}
                  onPick={(picked) =>
                    onPick({ service: service.slug, tier: picked.slug })
                  }
                />
                <p className={styles.changeService}>
                  <button
                    type="button"
                    className={styles.linkButton}
                    onClick={() => onPick({ service: "", tier: "" })}
                  >
                    Something else
                  </button>
                </p>
              </>
            ) : (
              <fieldset className={styles.tiers}>
                <legend className={styles.tiersLegend}>
                  What is this about? Skip it if none of these fit &mdash;
                  describe it in your own words instead.
                </legend>
                <div className={styles.serviceGrid}>
                  {catalogue.map((option) => (
                    <button
                      key={option.slug}
                      type="button"
                      className={styles.serviceChip}
                      onClick={() => onPick({ service: option.slug, tier: "" })}
                    >
                      {option.name}
                    </button>
                  ))}
                </div>
              </fieldset>
            )}
          </aside>
        )}

        <div className={styles.panel}>
        <p className={styles.eyebrow}>Ordering</p>
        <h1 className={styles.title}>{heading}</h1>
        <p className={styles.body}>
          {budgetIsSettled
            ? "Two questions, and only the first really matters — the ones we would ask on a first call anyway. The size you picked answers the rest."
            : "A few questions — the ones we would ask on a first call anyway. Answering them here means the first reply you get is useful instead of a list of questions back."}
        </p>

        <form onSubmit={submit} noValidate>
          <label className={styles.label}>
            What is happening today that prompted this?
            <textarea
              className={styles.textarea}
              rows={4}
              placeholder="We reconcile M-Pesa payments against invoices by hand, and it takes two days a week."
              value={problem}
              onChange={(e) => setProblem(e.target.value)}
            />
          </label>
          {fieldError ? <p className={styles.error}>{fieldError}</p> : null}

          <label className={styles.label}>
            Roughly what does it cost per month?
            <input
              className={styles.input}
              placeholder="Staff time, lost revenue, a rough figure — or leave it blank"
              value={monthlyCost}
              onChange={(e) => setMonthlyCost(e.target.value)}
            />
          </label>

          <label className={styles.label}>
            When would you want this working?
            <select
              className={styles.input}
              value={timeline}
              onChange={(e) => setTimeline(e.target.value)}
            >
              <option value="">No particular deadline</option>
              <option value="Within a month">Within a month</option>
              <option value="Within three months">Within three months</option>
              <option value="This year">This year</option>
            </select>
          </label>

          {/*
            Answered by the tier, so the question is not asked. Showing the
            published price back is not a formality — it is the number we are
            standing behind, and the client should see it before they send.
          */}
          {budgetIsSettled ? (
            <div className={styles.settled}>
              <span className={styles.settledLabel}>Budget</span>
              <span className={styles.settledValue}>{settledBudget}</span>
              <span className={styles.hint}>
                The published price for {tier?.name}. Final scope is agreed in
                writing before anything starts, and nothing is charged today.
              </span>
            </div>
          ) : (
            <label className={styles.label}>
              Budget range
              <select
                className={styles.input}
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
              >
                <option value="">Not sure yet</option>
                <option value="Under KES 100,000">Under KES 100,000</option>
                <option value="KES 100,000 - 500,000">KES 100,000 &ndash; 500,000</option>
                <option value="KES 500,000 - 1,000,000">KES 500,000 &ndash; 1,000,000</option>
                <option value="Over KES 1,000,000">Over KES 1,000,000</option>
              </select>
              <span className={styles.hint}>
                {tier?.is_from
                  ? `${tier.name} starts at the figure on the card. Tell us the ceiling you have in mind so we can size the scope to it.`
                  : "A range, not a commitment. It tells us whether we are the right fit before either of us spends time on it."}
              </span>
            </label>
          )}

          {error ? <p className={styles.error}>{error}</p> : null}

          <button className={styles.primary} type="submit" disabled={pending}>
            {pending ? "Sending" : "Send this request"}
          </button>
        </form>

        <p className={styles.hint}>
          This does not start any work and nothing is charged. Work begins once
          scope is agreed and a statement of work is signed.
        </p>
        </div>
      </div>
    </div>
  );
}
