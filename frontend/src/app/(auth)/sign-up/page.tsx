"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthShell } from "@/components/auth/AuthShell";
import { Field, Fields, FormError, PasswordField, Submit } from "@/components/auth/Form";
import { ApiError, auth } from "@/lib/api";
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
      router.push(next);
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
          <Link href="/sign-in" className={styles.link}>Sign in</Link>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate>
        <FormError>{error}</FormError>

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

          <p className={styles.terms}>
            By continuing you accept the{" "}
            <Link href="/terms" className={styles.link}>terms of service</Link>{" "}
            and the{" "}
            <Link href="/privacy" className={styles.link}>privacy policy</Link>.
          </p>
        </Fields>
      </form>
    </AuthShell>
  );
}
