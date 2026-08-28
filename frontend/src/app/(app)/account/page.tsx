"use client";

import { useEffect, useState } from "react";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Fields, FormError, PasswordField, Submit } from "@/components/auth/Form";
import { ApiError, auth, portal, session } from "@/lib/api";
import styles from "./page.module.css";

/**
 * Account settings.
 *
 * Two things in v1, and both are obligations rather than features:
 *
 *   change password  — Tier 1 of Charter 03 §IV. An account you cannot rotate
 *                      the credential on is an account you cannot recover.
 *   export my data   — Charter 05 §VIII, "we do not hold data, domains, or
 *                      accounts hostage under any circumstance". Self-serve, in
 *                      v1, not as a later feature and not by emailing us to ask.
 *
 * Deliberately absent: delete my account. It is not a checkbox — an order has
 * financial records attached and a retention period that outlives the account
 * (09-communication retention schedule). Offering a button that cannot honestly
 * erase everything would be worse than telling people to write to us, which is
 * what the link below does.
 */
export default function AccountPage() {
  const [who, setWho] = useState<{ email?: string; full_name?: string }>({});

  useEffect(() => {
    session().then((s) => setWho({ email: s.email, full_name: s.full_name }));
  }, []);

  return (
    <div className={`wrap ${styles.wrap}`}>
      <header className={styles.head}>
        <p className="eyebrow">Account</p>
        <h1 className={styles.title}>{who.full_name || "Your account"}</h1>
        <p className={styles.email}>{who.email}</p>
      </header>

      <ChangePassword />

      <section className={styles.section}>
        <h2 className={styles.h2}>Appearance</h2>
        <p className={styles.body}>
          Light, dark, or whatever this device is already set to.
        </p>
        {/*
          The canonical home for this control. The header carries a copy on
          wider screens, but at 375px it did not fit alongside the wordmark and
          the navigation, so the header drops it and this stays.
        */}
        <ThemeToggle />
      </section>


      <section className={styles.section}>
        <h2 className={styles.h2}>Your data</h2>
        <p className={styles.body}>
          Download everything we hold on this account — your profile, your
          organisation, every order, every progress note and every milestone —
          as a JSON file. No request, no wait, no conditions.
        </p>
        {/*
          A plain link, not a fetch. The endpoint answers with
          Content-Disposition: attachment, so the browser saves the file itself;
          pulling it through JavaScript to rebuild a download would add a blob,
          a memory copy and a bug for nothing.
        */}
        <a className="btn btn--ghost" href={portal.exportUrl} download>
          Download my data
        </a>
        <p className={styles.note}>
          To close this account, write to{" "}
          <a href="mailto:info@genmars.co.ke">info@genmars.co.ke</a>. We do not
          offer a one-click delete because records tied to a signed engagement
          have a retention period we are required to keep — we would rather say
          that than show you a button that quietly does not do what it says.
        </p>
      </section>
    </div>
  );
}

function ChangePassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | undefined>();
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setPending(true);
    setError(null);
    setFieldError(undefined);
    setDone(false);
    try {
      await auth.changePassword(current, next);
      // The server calls update_session_auth_hash, so this tab stays signed in.
      setCurrent("");
      setNext("");
      setDone(true);
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : "Something went wrong. Try again.";
      if (err instanceof ApiError && err.field === "new_password") setFieldError(message);
      else setError(message);
    } finally {
      setPending(false);
    }
  }

  return (
    <section className={styles.section}>
      <h2 className={styles.h2}>Password</h2>
      <form onSubmit={submit} className={styles.form} noValidate>
        {error ? <FormError>{error}</FormError> : null}
        {done ? (
          <p className={styles.done} role="status">
            Your password has been changed. You are still signed in here; other
            devices will need the new one.
          </p>
        ) : null}

        <Fields>
          {/* Hidden username field: password managers need it to know WHICH
              login they are updating. Without it they save an orphan entry. */}
          <input
            type="text"
            name="username"
            autoComplete="username"
            className="visually-hidden"
            tabIndex={-1}
            aria-hidden="true"
            readOnly
            value=""
          />
          <PasswordField
            label="Current password"
            autoComplete="current-password"
            required
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
          <PasswordField
            label="New password"
            autoComplete="new-password"
            hint="At least 10 characters. Longer beats complicated."
            error={fieldError}
            required
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </Fields>

        <Submit pending={pending}>Change password</Submit>
      </form>
    </section>
  );
}
