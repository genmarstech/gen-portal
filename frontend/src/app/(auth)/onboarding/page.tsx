"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthShell } from "@/components/auth/AuthShell";
import {
  ChoiceField,
  Field,
  Fields,
  FormError,
  Submit,
  TextareaField,
} from "@/components/auth/Form";
import { LoadingMark } from "@/components/LoadingMark";
import { ApiError, portal, session } from "@/lib/api";
import {
  captureOrdering,
  clearOrdering,
  serviceLabel,
  type Ordering,
} from "@/lib/ordering";
import { advance, readReturnTo, useReturnTo, withReturnTo } from "@/lib/returnTo";
import styles from "./page.module.css";

/**
 * Onboarding.
 *
 * ── WHAT THIS SCREEN IS, AND IS NOT ─────────────────────────────────────────
 * It finishes an ACCOUNT. It does not start work. Charter 02 §I gives
 * qualification to the commercial partners and a capacity veto to the founder,
 * so nothing here can bring an engagement into existence — the API produces an
 * Enquiry at status NEW and stops.
 *
 * That constraint decides the copy as much as the code. This screen must not
 * imply that submitting it has bought anything: no "we'll be in touch within
 * 24 hours" (Charter 03 §IV forbids advertising a commitment we have not had
 * to meet), no "your project has started", no queue position. It says what is
 * true — someone reads this, and then there is a conversation.
 *
 * ── WHY THE QUESTIONS ARE THESE QUESTIONS ───────────────────────────────────
 * They are the Playbook's qualification questions, asked of the client rather
 * than about them. Answering "what is this costing you per month" is useful to
 * the person answering it, and it means the first human reply can be about
 * their problem instead of a list of questions back.
 *
 * Only the problem is required. Someone who does not know their budget still
 * has a real problem, and a required field with no honest answer produces a
 * dishonest one.
 */

const TIMELINES = [
  "As soon as possible",
  "Within three months",
  "This year",
  "Just exploring",
] as const;

const BUDGETS = [
  "Under KES 250,000",
  "KES 250,000 – 500,000",
  "KES 500,000 – 1,000,000",
  "Over KES 1,000,000",
  "Not sure yet",
] as const;

type Step = 0 | 1;

export default function OnboardingPage() {
  const router = useRouter();
  const returnTo = useReturnTo();

  const [ready, setReady] = useState(false);
  const [step, setStep] = useState<Step>(0);

  /* What they clicked on genmars.co.ke, captured on arrival and read back
     here. See lib/ordering.ts — the query string is long gone by now. */
  const [ordering, setOrdering] = useState<Ordering>({ service: "", tier: "" });

  const [fullName, setFullName] = useState("");
  const [organisation, setOrganisation] = useState("");
  const [problem, setProblem] = useState("");
  const [monthlyCost, setMonthlyCost] = useState("");
  const [timeline, setTimeline] = useState("");
  const [budget, setBudget] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);

  /**
   * Guard, and pre-fill.
   *
   * Someone who has already onboarded must not sit here filling in a form only
   * to be told at the end that it did nothing. The name is pre-filled because
   * we already asked for it at sign-up — asking twice suggests we were not
   * listening the first time.
   *
   * Reads the return target with readReturnTo() rather than using the hook's
   * state. The hook resolves in its own effect, so on the first pass its value
   * is still null — and this guard redirects. That ordering dropped the return
   * target on exactly the visitors it exists for: anyone arriving from the
   * marketing site already signed in. Inside an effect `window` is present, so
   * reading it directly is both safe and correct.
   */
  useEffect(() => {
    setOrdering(captureOrdering());
  }, []);

  useEffect(() => {
    let cancelled = false;
    const returning = readReturnTo();
    session()
      .then((s) => {
        if (cancelled) return;
        if (!s.authenticated) {
          router.replace(withReturnTo("/sign-in", returning));
          return;
        }
        if (!s.email_verified) {
          router.replace(withReturnTo("/verify", returning));
          return;
        }
        // Already onboarded. If they arrived mid-journey from the marketing
        // site, that journey is complete — send them back rather than parking
        // them on a dashboard they did not ask for.
        if (!s.needs_onboarding) {
          advance(router, "/dashboard", returning);
          return;
        }
        setFullName(s.full_name ?? "");
        setReady(true);
      })
      .catch(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  function goToDetails(e: React.FormEvent) {
    e.preventDefault();
    const errors: Record<string, string> = {};
    if (!fullName.trim()) errors.full_name = "We need a name to address you by.";
    if (!organisation.trim())
      errors.organisation_name = "Please give your organisation a name.";
    setFieldErrors(errors);
    if (Object.keys(errors).length === 0) {
      setStep(1);
      setError(null);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();

    // Mirrors the server's rule so the failure arrives before the round trip,
    // not instead of it — the server still enforces this.
    if (problem.trim().length < 20) {
      setFieldErrors({
        problem:
          "Tell us a little more — a sentence or two about what is going wrong.",
      });
      return;
    }

    setPending(true);
    setError(null);
    setFieldErrors({});
    try {
      const { next } = await portal.onboarding({
        full_name: fullName.trim(),
        organisation_name: organisation.trim(),
        problem: problem.trim(),
        monthly_cost: monthlyCost.trim(),
        timeline,
        budget_range: budget,
        service: ordering.service,
        tier: ordering.tier,
      });
      // Only after it is filed. Leaving it would attach the same selection to
      // a second enquiry from the same tab.
      clearOrdering();
      advance(router, next, returnTo);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Something went wrong. Try again.",
      );
      setPending(false);
    }
  }

  if (!ready) {
    return (
      <div className={styles.booting}>
        <LoadingMark size={40} label="Loading" />
      </div>
    );
  }

  return (
    <AuthShell
      eyebrow={step === 0 ? "Set up · 1 of 2" : "Set up · 2 of 2"}
      title={step === 0 ? "Who are we working with?" : "What is going wrong?"}
      lede={
        step === 0
          ? "Two short steps. This sets up your account — it does not commit you to anything."
          : "The more specific you are, the more useful our first reply can be."
      }
      panelHeadline={
        <>
          Tell us the
          <br />
          problem, not
          <br />
          the solution.
        </>
      }
      panelSub="We would rather understand what is breaking than be handed a spec."
      footer={
        step === 1 ? (
          <button
            type="button"
            className={styles.back}
            onClick={() => {
              setStep(0);
              setError(null);
              setFieldErrors({});
            }}
          >
            &larr; Back
          </button>
        ) : null
      }
    >
      <Progress step={step} />

      {step === 0 ? (
        <form onSubmit={goToDetails} noValidate>
          <Fields>
            <Field
              label="Your name"
              autoComplete="name"
              value={fullName}
              error={fieldErrors.full_name}
              onChange={(e) => setFullName(e.target.value)}
            />
            <Field
              label="Organisation"
              placeholder="The company this work is for"
              autoComplete="organization"
              value={organisation}
              error={fieldErrors.organisation_name}
              onChange={(e) => setOrganisation(e.target.value)}
            />
            <Submit>Continue</Submit>
          </Fields>
        </form>
      ) : (
        <form onSubmit={submit} noValidate>
          <FormError>{error}</FormError>

          {/* Shown back so they can see we picked it up — and so that if it is
              wrong, they say so in the box below rather than discovering it on
              a call. It is attribution, not an order: nothing here commits
              either side, which the closing paragraph says plainly. */}
          {ordering.service || ordering.tier ? (
            <p className={styles.ordering}>
              You came from{" "}
              <strong>
                {[serviceLabel(ordering.service), ordering.tier]
                  .filter(Boolean)
                  .join(" · ")}
              </strong>
              . Tell us below if that is not quite what you need.
            </p>
          ) : null}

          <Fields>
            <TextareaField
              label="What is happening today that prompted this?"
              placeholder="We reconcile M-Pesa payments against invoices by hand, and it takes two days a week."
              hint="Plain language is fine. You do not need to know the solution."
              value={problem}
              error={fieldErrors.problem}
              onChange={(e) => setProblem(e.target.value)}
            />
            <Field
              label="Roughly what does it cost per month?"
              placeholder="Staff time, lost revenue, a rough figure — or leave it blank"
              value={monthlyCost}
              onChange={(e) => setMonthlyCost(e.target.value)}
            />
            <ChoiceField
              label="When would you want this working?"
              name="timeline"
              options={TIMELINES}
              value={timeline}
              onChange={setTimeline}
            />
            <ChoiceField
              label="Budget range"
              name="budget"
              options={BUDGETS}
              value={budget}
              onChange={setBudget}
              hint="A range, not a commitment. It tells us whether we are the right fit before either of us spends time on it."
            />
            <Submit pending={pending}>Finish setting up</Submit>
          </Fields>

          {/*
            No response-time promise here. Charter 03 §IV standing rule: never
            put a commitment in front of a client that has not been tested under
            real conditions.
          */}
          <p className={styles.after}>
            This does not start any work. Someone reads it, and if it looks like
            something we can genuinely help with, we will get in touch to talk it
            through. Work only begins once scope is agreed and a statement of
            work is signed.
          </p>
        </form>
      )}
    </AuthShell>
  );
}

function Progress({ step }: { step: Step }) {
  return (
    <div className={styles.progress} aria-hidden="true">
      <span className={styles.barDone} />
      <span className={step === 1 ? styles.barDone : styles.bar} />
    </div>
  );
}
