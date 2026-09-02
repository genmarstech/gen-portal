"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { AuthShell } from "@/components/auth/AuthShell";
import { CodeInput, Field, Fields, FormError, PasswordField, Submit } from "@/components/auth/Form";
import { ApiError, auth } from "@/lib/api";
import { advance, useReturnTo } from "@/lib/returnTo";
import styles from "../auth.module.css";

/**
 * Accept an invitation.
 *
 * ── WHAT IS ACTUALLY HAPPENING HERE ─────────────────────────────────────────
 * Genmars created this account. It exists so a membership can point at it, and
 * it has NO USABLE PASSWORD — nobody can sign into it, including us, until the
 * person on this screen chooses one. This form is the only thing that makes it
 * a real account.
 *
 * ── WHY THE COPY WORKS HARDER THAN THE RESET SCREEN'S ───────────────────────
 * Someone arriving here did not ask for an account and may never have heard of
 * us. A page on an unfamiliar domain asking for a new password is the shape of
 * a phishing attack, and telling them "enter your code" as though they should
 * already know why is how a legitimate invitation gets deleted. So it says who
 * is asking and what the account is for before it asks for anything.
 *
 * The address is editable rather than fixed. An invite is forwarded more often
 * than a reset — one person at a client passes it to the colleague who should
 * actually have it — and a locked field turns that into a dead end.
 */
function InviteForm() {
  const router = useRouter();
  const params = useSearchParams();
  const returnTo = useReturnTo();

  const [email, setEmail] = useState(params.get("email") ?? "");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const { next } = await auth.acceptInvite(email, code, password);
      advance(router, next, returnTo);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      setPending(false);
    }
  }

  return (
    <AuthShell
      eyebrow="Invitation"
      title="Set your password"
      lede="Genmars has given you access to your organisation's project. Choose a password and the account is yours."
      panelHeadline={<>Your project,<br />not a mailbox<br />full of threads.</>}
      panelSub="Scope and exclusions, a written progress note every week, milestones and what has been paid — in one place."
      footer={
        <>
          Already set it up?{" "}
          <Link href="/sign-in" className={styles.link}>Sign in</Link>
        </>
      }
    >
      <p className={styles.sent}>
        Enter the six-digit code from the invitation email. It expires 15
        minutes after it was sent — if yours has, ask whoever invited you to
        send another.
      </p>

      <form onSubmit={onSubmit} noValidate>
        <FormError>{error}</FormError>

        <Fields>
          <Field
            label="Email address"
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            required
            hint="The address the invitation was sent to."
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />

          <CodeInput value={code} onChange={setCode} error={error ? " " : undefined} />

          <PasswordField
            autoComplete="new-password"
            required
            minLength={10}
            hint="At least 10 characters. Longer beats complicated."
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />

          <Submit pending={pending} disabled={code.length !== 6}>
            Set password and continue
          </Submit>

          <p className={styles.terms}>
            By continuing you accept the{" "}
            <Link href="https://genmars.co.ke/terms/" className={styles.link}>
              terms of service
            </Link>{" "}
            and the{" "}
            <Link href="https://genmars.co.ke/privacy/" className={styles.link}>
              privacy policy
            </Link>
            .
          </p>
        </Fields>
      </form>
    </AuthShell>
  );
}

export default function InvitePage() {
  return (
    <Suspense fallback={null}>
      <InviteForm />
    </Suspense>
  );
}
