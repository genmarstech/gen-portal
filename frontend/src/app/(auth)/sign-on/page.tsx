"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { AuthShell } from "@/components/auth/AuthShell";
import { FormError, Submit } from "@/components/auth/Form";
import { LoadingMark } from "@/components/LoadingMark";
import { ApiError, signOn, type SignOnApp } from "@/lib/api";
import styles from "./sign-on.module.css";

/**
 * "Business OS would like to sign you in as you@genmars.co.ke."
 *
 * ══════════════════════════════════════════════════════════════════════════
 * THE ONE RULE THIS SCREEN EXISTS TO ENFORCE: AN ACCOUNT HERE COMES FIRST.
 *
 * A sibling application does not ask anyone for a password. It sends them
 * here, and if they have no Genmars account they make one HERE — not a second
 * account over there. That is what makes deactivating somebody a single act
 * instead of a checklist, and a checklist is how a leaver keeps a login.
 * ══════════════════════════════════════════════════════════════════════════
 *
 * ── WHY THE BUTTON IS A BUTTON AND NOT AN AUTOMATIC REDIRECT ────────────────
 *
 * It would be smoother to mint the code on load and bounce the browser
 * onward. It would also mean any page on the internet could link here and
 * silently hand a signed-in person's identity to an application they never
 * agreed to join. The press is the consent, and it is the only place a person
 * is told which application is asking and which address they are being sent
 * to before it happens.
 *
 * ── WHAT IS SHOWN AND WHAT IS NOT ───────────────────────────────────────────
 *
 * The application's name, what it is for, and the address the browser will be
 * sent to — all three, because "an app wants to sign you in" tells nobody
 * anything. The code is never rendered; it exists only inside the address the
 * browser is navigated to, and the navigation happens immediately.
 */
export default function SignOnPage() {
  const [params, setParams] = useState<{
    clientId: string;
    redirectUri: string;
    state: string;
  } | null>(null);
  const [app, setApp] = useState<SignOnApp | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  /*
   * Read the query in an effect, not during render: these pages are
   * prerendered and `window` does not exist on the first pass.
   */
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    setParams({
      clientId: query.get("client_id") ?? "",
      redirectUri: query.get("redirect_uri") ?? "",
      state: query.get("state") ?? "",
    });
  }, []);

  useEffect(() => {
    if (params === null) return;
    if (!params.clientId || !params.redirectUri) {
      setProblem("That sign-in link is incomplete. Go back to the application and try again.");
      return;
    }
    let cancelled = false;
    signOn
      .describe(params.clientId, params.redirectUri)
      .then((found) => {
        if (!cancelled) setApp(found);
      })
      .catch((err) => {
        if (cancelled) return;
        setProblem(
          err instanceof ApiError
            ? err.message
            : "That sign-in link could not be checked. Try again.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [params]);

  const onContinue = useCallback(async () => {
    if (params === null) return;
    setError(null);
    setPending(true);
    try {
      const { redirect_to } = await signOn.authorize(
        params.clientId,
        params.redirectUri,
        params.state,
      );
      /*
       * A full navigation, not the Next router. The destination is another
       * origin entirely, and the router has no idea what business-os is.
       * `replace` rather than `assign` so the code does not sit in this tab's
       * back history — it is single-use and already spent by the time anyone
       * presses back, but a spent code in a history entry is still a spent
       * code in a history entry.
       */
      window.location.replace(redirect_to);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Something went wrong. Try again.",
      );
      setPending(false);
    }
  }, [params]);

  if (problem !== null) {
    return (
      <AuthShell
        eyebrow="Sign in"
        title="That link does not work"
        lede="Nothing has been shared, and you are still signed in here."
      >
        <FormError>{problem}</FormError>
        <p className={styles.escape}>
          <Link href="/dashboard" className={styles.link}>
            Go to your dashboard
          </Link>
        </p>
      </AuthShell>
    );
  }

  if (app === null) {
    return (
      <div className={styles.booting}>
        <LoadingMark size={40} label="Checking that link" />
      </div>
    );
  }

  // Signed out. Sign-in and sign-up both come back here afterwards, carrying
  // the whole query along — safeReturnTo() accepts an internal path, and this
  // is one.
  const here = `/sign-on${window.location.search}`;
  if (!app.you.authenticated) {
    return (
      <AuthShell
        eyebrow="Sign in"
        title={`Continue to ${app.name}`}
        lede="You need a Genmars account. It is the same account you use for everything else here."
      >
        <AppCard app={app} />
        <div className={styles.actions}>
          <Link
            href={`/sign-in?return=${encodeURIComponent(here)}`}
            className={styles.primary}
          >
            Sign in
          </Link>
          <Link
            href={`/sign-up?return=${encodeURIComponent(here)}`}
            className={styles.link}
          >
            Create an account
          </Link>
        </div>
      </AuthShell>
    );
  }

  // Signed in, but this application is not for them. Said plainly, with the
  // reason, because "no" on its own becomes a support ticket.
  if (!app.you.admitted) {
    return (
      <AuthShell
        eyebrow="Sign in"
        title={`${app.name} is not open to this account`}
        lede={`You are signed in as ${app.you.email}.`}
      >
        <AppCard app={app} />
        <FormError>{app.you.blocked_because}</FormError>
        <p className={styles.escape}>
          <Link href="/dashboard" className={styles.link}>
            Go to your dashboard
          </Link>
        </p>
      </AuthShell>
    );
  }

  return (
    <AuthShell
      eyebrow="Sign in"
      title={`Continue to ${app.name}`}
      lede={`You are signed in as ${app.you.email}.`}
    >
      <AppCard app={app} />

      <FormError>{error}</FormError>

      <p className={styles.shared}>
        {app.name} will be told your name, your email address and whether you
        are Genmars staff. It is not given your password, and it cannot act as
        you here.
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void onContinue();
        }}
      >
        <Submit pending={pending}>Continue to {app.name}</Submit>
      </form>

      <p className={styles.escape}>
        <Link href="/dashboard" className={styles.link}>
          Cancel and go to your dashboard
        </Link>
      </p>
    </AuthShell>
  );
}

/**
 * Which application, what for, and where the browser is about to go.
 *
 * The address is shown in full rather than as the app's name. A person
 * agreeing to be sent somewhere is entitled to see where — and a registered
 * address that looks wrong is the one thing a reader can catch that the
 * server's exact-match check cannot.
 */
function AppCard({ app }: { app: SignOnApp }) {
  return (
    <div className={styles.card}>
      <p className={styles.cardName}>{app.name}</p>
      {app.purpose ? <p className={styles.cardPurpose}>{app.purpose}</p> : null}
      <dl className={styles.meta}>
        <div>
          <dt>Sends you to</dt>
          <dd className={styles.uri}>{app.redirect_uri}</dd>
        </div>
        <div>
          <dt>Open to</dt>
          <dd>{app.audience_label}</dd>
        </div>
      </dl>
    </div>
  );
}
