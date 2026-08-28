"use client";

import Link from "next/link";
import { useState } from "react";
import { AuthShell } from "@/components/auth/AuthShell";
import { Field, Fields, FormError, Submit } from "@/components/auth/Form";
import { ApiError, auth } from "@/lib/api";
import styles from "../auth.module.css";

/**
 * Forgot password.
 *
 * On success this screen says the same thing whether or not the address is
 * registered. That is the entire point: a "no such account" message here turns
 * the form into a free check of who banks with us.
 */
export default function ForgotPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      await auth.forgot(email);
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setPending(false);
    }
  }

  if (sent) {
    return (
      <AuthShell
        eyebrow="Check your email"
        title="Check your email"
        lede="If that address has an account, a reset code is on its way. It expires in 15 minutes."
        footer={<Link href="/sign-in" className={styles.link}>Back to sign in</Link>}
      >
        <p className={styles.note}>
          Nothing arrived? Check spam, then try again — repeated requests replace
          the previous code rather than stacking up.
        </p>
        <Fields>
          <Link href={`/reset?email=${encodeURIComponent(email)}`} className={styles.link}>
            I have a code
          </Link>
        </Fields>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      eyebrow="Reset password"
      title="Reset your password"
      lede="Give us the address on your account and we will send a code."
      panelHeadline={<>Locked out<br />is temporary.</>}
      panelSub="Account locks clear on their own. You will never need to raise a ticket to get back in."
      footer={<Link href="/sign-in" className={styles.link}>Back to sign in</Link>}
    >
      <form onSubmit={onSubmit} noValidate>
        <FormError>{error}</FormError>
        <Fields>
          <Field
            label="Email address" type="email" name="email" autoComplete="email"
            inputMode="email" required
            value={email} onChange={(e) => setEmail(e.target.value)}
          />
          <Submit pending={pending}>Send code</Submit>
        </Fields>
      </form>
    </AuthShell>
  );
}
