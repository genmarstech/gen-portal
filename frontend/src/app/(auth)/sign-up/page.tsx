"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthShell } from "@/components/auth/AuthShell";
import { Field, Fields, FormError, PasswordField, Submit } from "@/components/auth/Form";
import { ReturnNotice } from "@/components/auth/ReturnNotice";
import { ApiError, auth, session } from "@/lib/api";
import { advance, readReturnTo, useReturnTo, withReturnTo } from "@/lib/returnTo";
import styles from "../auth.module.css";

/**
 * Sign up.
 *
 * Creates an ACCOUNT ONLY. It does not create an order and must not: Charter 02
 * §I gives qualification to the commercial partners and the capacity veto to the
 * founder. The copy says so plainly rather than implying work has been booked —
 * a signup that felt like placing an order would set an expectation nobody
 * agreed to.
 */
export default function SignUpPage() {
  const router = useRouter();
  const returnTo = useReturnTo();

  /**
   * Already signed in, and sent here anyway? Turn them straight around.
   *
   * genmars.co.ke sends people here before it lets them request work, and it
   * decides who to send from a flag in its own local storage — which is all a
   * static site can know. Cleared storage, a second browser, or a private
   * window and it sends someone who has had an account for months. Showing
   * them a sign-up form would be the site telling them to create the account
   * they are already signed in to.
   *
   * The server is the only thing that actually knows, so ask it. Only when a
   * return target is present: a bare visit to /sign-up is someone choosing to
   * be here, and bouncing them off it would be wrong.
   *
   * Only a COMPLETE account bounces. Unverified or not yet onboarded means the
   * journey they were sent on is genuinely unfinished, and they belong on the
   * form.
   *
   * readReturnTo() rather than the hook, because this runs on mount and the
   * hook resolves a render later — the same ordering that cost the onboarding
   * guard its return target.
   */
  useEffect(() => {
    const returning = readReturnTo();
    if (!returning) return;

    let cancelled = false;
    session()
      .then((s) => {
        if (cancelled) return;
        if (s.authenticated && s.email_verified && !s.needs_onboarding) {
          window.location.assign(returning);
        }
      })
      .catch(() => {
        // Offline, or the API is down. Leave the form up: it is the more
        // useful of the two failures, and submitting will surface the real
        // error rather than this one.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const { next } = await auth.signUp(email, password, fullName);
      advance(router, next, returnTo);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      setPending(false);
    }
  }

  return (
    <AuthShell
      eyebrow="Create account"
      title="Create your account"
      lede="An account lets you follow work we are doing for you. It does not place an order."
      panelHeadline={<>A written update,<br />every week.</>}
      panelSub="Scope and exclusions, the weekly progress note, milestones and what has been paid — in one place instead of scattered through email."
      footer={
        <>
          Already have an account?{" "}
          <Link href={withReturnTo("/sign-in", returnTo)} className={styles.link}>Sign in</Link>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate>
        <FormError>{error}</FormError>

        <ReturnNotice returnTo={returnTo} />

        <Fields>
          <Field
            label="Your name" name="name" autoComplete="name" required
            value={fullName} onChange={(e) => setFullName(e.target.value)}
          />

          <Field
            label="Email address" type="email" name="email" autoComplete="email"
            inputMode="email" placeholder="you@company.co.ke" required
            hint="We send one email to verify it. No marketing."
            value={email} onChange={(e) => setEmail(e.target.value)}
          />

          <PasswordField
            autoComplete="new-password" required minLength={10}
            hint="At least 10 characters. Longer beats complicated."
            value={password} onChange={(e) => setPassword(e.target.value)}
          />

          <Submit pending={pending}>Create account</Submit>

          {/*
            ABSOLUTE, and plain <a> rather than next/link.

            Both documents live on the MARKETING site, not here. `/terms` and
            `/privacy` were relative, which resolved to app.genmars.co.ke and
            404'd — on the one paragraph a client has to read before agreeing to
            it. next/link would also try to client-side navigate to a route this
            app does not have.

            target="_blank" because losing a half-filled signup form to read the
            terms is how people stop reading the terms.
          */}
          <p className={styles.terms}>
            By continuing you accept the{" "}
            <a
              href="https://genmars.co.ke/terms/"
              className={styles.link}
              target="_blank"
              rel="noopener noreferrer"
            >
              terms of service
            </a>{" "}
            and the{" "}
            <a
              href="https://genmars.co.ke/privacy/"
              className={styles.link}
              target="_blank"
              rel="noopener noreferrer"
            >
              privacy policy
            </a>
            .
          </p>
        </Fields>
      </form>
    </AuthShell>
  );
}
