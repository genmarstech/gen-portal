"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { AuthShell } from "@/components/auth/AuthShell";
import { CodeInput, Fields, FormError, PasswordField, Submit } from "@/components/auth/Form";
import { ApiError, auth } from "@/lib/api";
import styles from "../auth.module.css";

/**
 * Set a new password.
 *
 * Code and password on one screen rather than two: splitting them means holding
 * a verified-but-unused code in client state, which is a credential sitting
 * around for no reason.
 */
function ResetForm() {
  const router = useRouter();
  const params = useSearchParams();
  const email = params.get("email") ?? "";

  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const { next } = await auth.reset(email, code, password);
      router.push(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      setPending(false);
    }
  }

  return (
    <AuthShell
      eyebrow="New password"
      title="Set a new password"
      lede="Enter the code we sent, then choose something new."
      panelHeadline={<>Back in,<br />in one step.</>}
      panelSub="Setting a new password also clears any lock on the account."
      footer={<Link href="/sign-in" className={styles.link}>Back to sign in</Link>}
    >
      <form onSubmit={onSubmit} noValidate>
        <FormError>{error}</FormError>
        <Fields>
          <CodeInput value={code} onChange={setCode} />
          <PasswordField
            label="New password" autoComplete="new-password" required minLength={10}
            hint="At least 10 characters. Longer beats complicated."
            value={password} onChange={(e) => setPassword(e.target.value)}
          />
          <Submit pending={pending} disabled={code.length !== 6}>
            Set password
          </Submit>
        </Fields>
      </form>
    </AuthShell>
  );
}

export default function ResetPage() {
  return (
    <Suspense fallback={null}>
      <ResetForm />
    </Suspense>
  );
}
