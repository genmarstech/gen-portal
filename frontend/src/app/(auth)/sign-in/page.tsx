"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { AuthShell } from "@/components/auth/AuthShell";
import {
  Divider,
  Field,
  Fields,
  FormError,
  PasswordField,
  Secondary,
  Submit,
} from "@/components/auth/Form";
import { ApiError, auth } from "@/lib/api";
import { ReturnNotice } from "@/components/auth/ReturnNotice";
import { advance, useReturnTo, withReturnTo } from "@/lib/returnTo";
import styles from "../auth.module.css";

/**
 * Sign in.
 *
 * Email is the identifier. The design's first pass was phone-first — right for
 * a consumer product in this market, wrong for a client portal where every user
 * is a business contact who already corresponds with us by email.
 *
 * The failure message is whatever the API returns and is NOT elaborated on
 * here. One message covers unknown-address and wrong-password; reconstructing
 * the difference in the UI would undo the enumeration defence in the backend.
 *
 * `?return=` arrives when the marketing site sent someone here before letting
 * them request work. It is honoured only once the account is complete, and only
 * for an origin we own — see lib/returnTo.ts.
 */
export default function SignInPage() {
  const router = useRouter();
  const returnTo = useReturnTo();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const { next } = await auth.signIn(email, password);
      advance(router, next, returnTo);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Something went wrong. Try again.",
      );
      setPending(false);
    }
  }

  return (
    <AuthShell
      eyebrow="Sign in"
      title="Sign in to Genmars"
      lede="Use the email address on your Genmars account."
      footer={
        <>
          No account?{" "}
          <Link href={withReturnTo("/sign-up", returnTo)} className={styles.link}>
            Create one
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate>
        <FormError>{error}</FormError>

        <ReturnNotice returnTo={returnTo} />

        <Fields>
          <Field
            label="Email address"
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            placeholder="you@company.co.ke"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />

          <PasswordField
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />

          <Link href="/forgot" className={styles.inlineLink}>
            Forgot password?
          </Link>

          <Submit pending={pending}>Continue</Submit>

          <Divider />

          {/* Phone sign-in is not built. Saying so plainly beats a control that
              looks live and does nothing — Charter 04 §III, admit limits early. */}
          <Secondary disabled title="Not available yet">
            Use phone instead
          </Secondary>
        </Fields>
      </form>
    </AuthShell>
  );
}
