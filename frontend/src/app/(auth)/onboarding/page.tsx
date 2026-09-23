"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthShell } from "@/components/auth/AuthShell";
import {
  ChoiceField,
  Field,
  Fields,
  FormError,
  MultiChoiceField,
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
 *
 * ── AND WHY THEY ARE NOW MOSTLY TICKED, NOT TYPED ───────────────────────────
 * The questions are unchanged; what changed is the cost of answering them.
 * This screen used to demand a written paragraph before it would finish an
 * account, and that is where people stopped — not for want of a problem, but
 * because writing one up is work, and it sat between them and what they came
 * for. An abandoned account tells us nothing; a ticked box tells us something.
 *
 * The prose box is still here and still reaches the same field. It is now the
 * place for anything the list missed, rather than the gate.
 *
 * ⚠ THE OPTIONS ARE AN OFFER, WHETHER OR NOT THEY ARE WORDED AS ONE. A list of
 *   problems on a Genmars form reads as a list of problems Genmars solves, so
 *   every one of them has to map to something in seed_services.py. Charter 04
 *   §IV. Adding a line we cannot answer would be advertising by checkbox.
 */

/*
 * ── WHY THIS IS A LIST AND NOT A WRITING TASK ───────────────────────────────
 *
 * This screen used to require a written paragraph before it would finish an
 * account, and people stopped at it. Not because they had nothing to say —
 * because putting a business problem into prose is work, and it was work
 * standing between somebody and the thing they actually came for. An account
 * left half-finished tells us nothing at all, which is strictly worse than a
 * ticked box.
 *
 * ⚠ THESE MUST STAY THINGS GENMARS ACTUALLY DOES. Charter 04 §IV — nothing
 *   untrue on a Genmars surface. A list is read as an offer: every line here
 *   is a problem the services in seed_services.py genuinely address, and
 *   adding one we cannot answer would be advertising by checkbox.
 *
 * The free-text box below them is still there for anyone who wants it. It is
 * simply no longer the toll gate.
 */
const PROBLEMS = [
  "Work is tracked in spreadsheets, WhatsApp or on paper",
  "Payments and invoices are reconciled by hand",
  "Our systems do not talk to each other",
  "Stock or inventory counts cannot be trusted",
  "We cannot see what is happening across branches",
  "Reports take days to put together",
  "We have software, but nobody maintains it",
  "We need a website or an app for our customers",
] as const;

/* Ticking this makes the description required — it is the one option that
   carries no information on its own, so it has to be followed by something. */
const SOMETHING_ELSE = "Something else";

/*
 * Bands rather than a text box.
 *
 * "Roughly what does it cost per month" was free text and optional, and it was
 * the second thing on this screen that asked somebody to do arithmetic before
 * they could continue. A band is answerable in a second and is as much as
 * anybody needs from it at this stage — the real number comes up in the
 * conversation, from someone who can ask a follow-up.
 */
const MONTHLY_COSTS = [
  "Under KES 50,000",
  "KES 50,000 – 200,000",
  "KES 200,000 – 500,000",
  "Over KES 500,000",
  "I have not worked it out",
] as const;

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
  const [problems, setProblems] = useState<string[]>([]);
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

  /**
   * The ticks and the writing, as one paragraph.
   *
   * The API takes a single `problem` string and operations reads it as prose,
   * so the shape of the request does not change — only how somebody produces
   * it. Composing here rather than adding a field to the serializer keeps one
   * description of the problem instead of two that can disagree.
   */
  function composeProblem(): string {
    const ticked = problems.filter((p) => p !== SOMETHING_ELSE);
    const written = problem.trim();

    const parts: string[] = [];
    if (ticked.length) parts.push(ticked.join("; ") + ".");
    if (written) parts.push(written);
    return parts.join("\n\n");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();

    const chose = problems.length > 0;
    const wrote = problem.trim().length > 0;

    if (!chose && !wrote) {
      setFieldErrors({
        problems: "Tick whatever is true, or describe it below.",
      });
      return;
    }

    // "Something else" is the one option that says nothing by itself.
    if (problems.includes(SOMETHING_ELSE) && !wrote) {
      setFieldErrors({
        problem: "Tell us what the something else is.",
      });
      return;
    }

    // Mirrors the server's rule so the failure arrives before the round trip,
    // not instead of it — the server still enforces this. Any single ticked
    // option clears twenty characters on its own, so this is only reachable
    // by someone who wrote a very short description and ticked nothing.
    const composed = composeProblem();
    if (composed.length < 20) {
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
        problem: composed,
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
            <MultiChoiceField
              label="What is happening today? Tick whatever is true."
              options={[...PROBLEMS, SOMETHING_ELSE]}
              values={problems}
              onChange={setProblems}
              hint={
                fieldErrors.problems ??
                "As many as apply. None of them exactly right? Tick Something else and say so below."
              }
            />
            <TextareaField
              label="Anything you want to add"
              placeholder="Optional. We reconcile M-Pesa payments against invoices by hand, and it takes two days a week."
              hint="Plain language is fine, and you do not need to know the solution. Leave it blank if the boxes covered it."
              value={problem}
              error={fieldErrors.problem}
              onChange={(e) => setProblem(e.target.value)}
            />
            <ChoiceField
              label="Roughly what is it costing per month?"
              name="monthly-cost"
              options={MONTHLY_COSTS}
              value={monthlyCost}
              onChange={setMonthlyCost}
              hint="Staff time, lost revenue, a rough sense of it. Skip it if you would rather."
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
