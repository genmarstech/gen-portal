"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Wordmark } from "../Brand";
import { LoadingMark } from "../LoadingMark";
import { ThemeToggle } from "../ThemeToggle";
import { Notifications } from "./Notifications";
import { session, signOut } from "@/lib/api";
import styles from "./AppShell.module.css";

/**
 * Signed-in shell.
 *
 * ── THE GUARD HERE IS UX, NOT SECURITY ──────────────────────────────────────
 * The redirect below stops a signed-out visitor staring at an empty dashboard.
 * It is NOT what protects the data — every endpoint requires authentication
 * server-side, and `portal/selectors.py` scopes every read through Membership.
 * A client-side check can always be skipped; the API cannot.
 *
 * Session state is fetched rather than assumed: the cookie is HttpOnly, so the
 * only way to know who is signed in is to ask.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [state, setState] = useState<"loading" | "in" | "out">("loading");
  const [email, setEmail] = useState("");

  useEffect(() => {
    let cancelled = false;
    session()
      .then((s) => {
        if (cancelled) return;
        if (!s.authenticated) {
          setState("out");
          router.replace("/sign-in");
          return;
        }
        // Signup is not finished. Sending them to an empty dashboard would
        // show them the "nothing is underway yet" state, which is true but
        // useless — they have a form left to fill in, not a wait ahead.
        if (s.needs_onboarding) {
          setState("out");
          router.replace("/onboarding");
          return;
        }
        setEmail(s.email ?? "");
        setState("in");
      })
      .catch(() => {
        if (!cancelled) setState("out");
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (state !== "in") {
    return (
      <div className={styles.booting}>
        <LoadingMark size={40} label="Loading your account" />
      </div>
    );
  }

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <div className={`wrap ${styles.headerInner}`}>
          <Link href="/dashboard" className={styles.brand} aria-label="Genmars, dashboard">
            {/* The real lockup from Brand.tsx, not a hand-rolled copy — the +300
                tracking and the barless A are spec, and a second version of the
                geometry is a second version to keep correct. */}
            <Wordmark width={150} withTagline={false} className={styles.lockup} />
          </Link>

          <nav aria-label="Primary" className={styles.nav}>
            <Link
              href="/dashboard"
              className={`${styles.link} ${pathname.startsWith("/dashboard") ? styles.active : ""}`}
            >
              Work
            </Link>
            <Link
              href="/invoices"
              className={`${styles.link} ${pathname.startsWith("/invoices") ? styles.active : ""}`}
            >
              Invoices
            </Link>
            <Link
              href="/services"
              className={`${styles.link} ${pathname.startsWith("/services") ? styles.active : ""}`}
            >
              Services
            </Link>
            <Link
              href="/account"
              className={`${styles.link} ${pathname === "/account" ? styles.active : ""}`}
            >
              Account
            </Link>
          </nav>

          <div className={styles.tools}>
            <Notifications />
            {/* Hidden on phones — see .themeToggle in the stylesheet. The
                control is on the Account page, which is one tap away. */}
            <span className={styles.themeToggle}>
              <ThemeToggle />
            </span>
            <button
              type="button"
              className={styles.signOut}
              onClick={async () => {
                await signOut();
                router.replace("/sign-in");
              }}
            >
              Sign out
            </button>
          </div>
        </div>
        <p className={`wrap ${styles.who}`}>{email}</p>
      </header>

      <main className={styles.main}>{children}</main>
    </div>
  );
}

