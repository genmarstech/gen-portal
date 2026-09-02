"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { LoadingMark } from "@/components/LoadingMark";
import { ApiError, portal, session } from "@/lib/api";
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
      heading={heading}
      onFiled={() => {
        clearOrdering();
        setState("done");
      }}
    />
  );
}

function OrderForm({
  ordering,
  heading,
  onFiled,
}: {
  ordering: Ordering;
  heading: string;
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
          budget_range: budget,
          service: ordering.service,
          tier: ordering.tier,
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
    [problem, monthlyCost, timeline, budget, ordering, onFiled, router],
  );

  return (
    <div className="wrap">
      <div className={styles.panel}>
        <p className={styles.eyebrow}>Ordering</p>
        <h1 className={styles.title}>{heading}</h1>
        <p className={styles.body}>
          Four questions &mdash; the ones we would ask on a first call anyway.
          Answering them here means the first reply you get is useful instead
          of a list of questions back.
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
              A range, not a commitment. It tells us whether we are the right
              fit before either of us spends time on it.
            </span>
          </label>

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
  );
}
