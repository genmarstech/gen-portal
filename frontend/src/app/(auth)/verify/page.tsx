"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { AuthShell } from "@/components/auth/AuthShell";
import { CodeInput, Fields, FormError, Submit } from "@/components/auth/Form";
import { ApiError, auth, session } from "@/lib/api";
import styles from "../auth.module.css";

const RESEND_SECONDS = 30;

/**
 * Email verification.
 *
 * Submits automatically once six digits are present — people paste a code and
 * then look for a button, and making them find one is friction for no benefit.
 * The button stays for keyboard users and for a retry after a failure.
 */
function VerifyForm() {
  const router = useRouter();
  const params = useSearchParams();

  /**
   * The address being verified.
   *
   * Normally it arrives in the query string. The session is the fallback, and
   * it matters: sign-in on an unverified account signs you in and THEN sends
   * you here, so the address is known server-side even when the URL is bare.
   * Without this fallback every request on this page posted an empty email,
   * which the API rejected with a 400 the UI could not explain.
   */
  const [email, setEmail] = useState(params.get("email") ?? "");

  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [resending, setResending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(RESEND_SECONDS);

  useEffect(() => {
    if (email) return;
    session()
      .then((s) => { if (s.email) setEmail(s.email); })
      .catch(() => { /* leave it blank; the message below already copes */ });
  }, [email]);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [cooldown]);

  async function submit(value: string) {
    setError(null);
    setPending(true);
    try {
      const { next } = await auth.verify(email, value);
      router.push(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      setCode("");
      setPending(false);
    }
  }

  function onChange(v: string) {
    setCode(v);
    if (v.length === 6 && !pending) void submit(v);
  }

  return (
    <AuthShell
      eyebrow="Verify email"
      title="Enter the code we sent"
      panelHeadline={<>One code,<br />then you are in.</>}
      panelSub="We verify the address once. We do not send marketing to it."
      footer={<Link href="/sign-in" className={styles.link}>Back to sign in</Link>}
    >
      <p className={styles.sent}>
        Six digits, sent to{" "}
        <span className={styles.sentTo}>{email || "your email address"}</span>.
        It expires in 15 minutes.
      </p>

      <form onSubmit={(e) => { e.preventDefault(); void submit(code); }} noValidate>
        <FormError>{error}</FormError>
        {notice ? <p className={styles.notice} role="status">{notice}</p> : null}
        <Fields>
          <CodeInput value={code} onChange={onChange} error={error ? " " : undefined} />

          <div className={styles.resend}>
            <span>Nothing arrived?</span>
            {/*
              Awaited and caught. This was a floating `void auth.requestCode()`:
              a rejection became an unhandled promise rejection — a crash
              overlay in development and a silent nothing in production — and
              the cooldown started even though no code had been sent.
            */}
            <button
              type="button"
              className={styles.resendButton}
              disabled={cooldown > 0 || resending || !email}
              onClick={async () => {
                setResending(true);
                setError(null);
                setNotice(null);
                try {
                  await auth.requestCode(email);
                  setNotice("Sent. Check your inbox again.");
                  setCooldown(RESEND_SECONDS);
                } catch (err) {
                  setError(
                    err instanceof ApiError
                      ? err.message
                      : "We could not send another code just now.",
                  );
                } finally {
                  setResending(false);
                }
              }}
            >
              {cooldown > 0 ? `Resend in ${cooldown}s` : resending ? "Sending" : "Send another"}
            </button>
          </div>

          <Submit pending={pending} disabled={code.length !== 6}>Verify</Submit>

          <Link href="/sign-up" className={styles.inlineLink}>
            Wrong address? Change it
          </Link>
        </Fields>
      </form>
    </AuthShell>
  );
}

export default function VerifyPage() {
  return (
    <Suspense fallback={null}>
      <VerifyForm />
    </Suspense>
  );
}
