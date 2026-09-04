"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  portal,
  type OrderSummary,
  type WaitingOnYou,
  type YourHosting,
  type YourSystem,
} from "@/lib/api";
import styles from "./DashboardApps.module.css";

/**
 * What is on this dashboard, and why each thing earns its place.
 *
 * ── WHY ONLY TWO ────────────────────────────────────────────────────────────
 *
 * A dashboard that shows everything is one nobody reads twice. Invoices,
 * offers, progress notes and support all have their own pages and are better
 * there. These earn a place because they change what somebody DOES:
 *
 *   · what we are waiting on them for — the commonest way a project quietly
 *     stops moving is that we are blocked on the client and the client does
 *     not know it, because the fact only ever lived on an internal board;
 *   · whether the thing we run for them is up — otherwise they find out by
 *     emailing to ask;
 *   · what is STILL RUNNING — a retainer has no delivery date and nothing
 *     about it changes week to week, so it scrolls past the order list as one
 *     more finished-looking row. That is how somebody pays for something for a
 *     year without a clear place to look at it;
 *   · which domains and accounts are in OUR name — Charter 05 §VIII says we do
 *     not hold anything hostage, and that is worth little if they cannot see
 *     which accounts it applies to. It is also the fact people discover at the
 *     worst possible moment.
 *
 * ── IT RENDERS NOTHING WHEN THERE IS NOTHING ────────────────────────────────
 *
 * No empty states, no "all clear" panels. A dashboard whose panels are usually
 * empty teaches people to skim past them, and then the one week something IS
 * there, they skim past that too.
 */
export function DashboardApps() {
  const [waiting, setWaiting] = useState<WaitingOnYou[]>([]);
  const [systems, setSystems] = useState<YourSystem[]>([]);
  const [ongoing, setOngoing] = useState<OrderSummary[]>([]);
  const [hosting, setHosting] = useState<YourHosting[]>([]);

  useEffect(() => {
    let cancelled = false;
    portal
      .dashboard()
      .then((body) => {
        if (cancelled) return;
        setWaiting(body.waiting_on_you);
        setSystems(body.systems);
        setOngoing(body.ongoing);
        setHosting(body.hosting);
      })
      .catch(() => {
        // Silent. These sit above the order list, which is the page's actual
        // job — an error banner here for a supplementary panel would be
        // alarming about the wrong thing.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Only ongoing work that is NOT an ordinary open project: a project in
  // progress is already the main list on this page, and repeating it here
  // would be the same row twice. What is missing from that list is the
  // arrangement that just keeps running.
  const subscriptions = ongoing.filter((o) => o.kind !== "project");

  if (
    waiting.length === 0 &&
    systems.length === 0 &&
    subscriptions.length === 0 &&
    hosting.length === 0
  ) {
    return null;
  }

  return (
    <div className={styles.apps}>
      {waiting.length > 0 && (
        <section className={styles.panel}>
          <h2 className={styles.title}>
            Waiting on you
            <span className={styles.count}>{waiting.length}</span>
          </h2>
          <p className={styles.lede}>
            We cannot move these along without something from your side.
          </p>

          <ul className={styles.list}>
            {waiting.map((item) => (
              <li key={item.id} className={styles.blocker}>
                <span className={styles.blockerTop}>
                  <span className={styles.summary}>{item.summary}</span>
                  {/* The number is the point. Two days is a note; three weeks
                      is a conversation somebody should have had. */}
                  <span
                    className={`${styles.days} ${item.waiting_days >= 7 ? styles.daysLong : ""}`}
                  >
                    {item.waiting_days === 0
                      ? "today"
                      : `${item.waiting_days}d`}
                  </span>
                </span>
                {item.detail && <span className={styles.detail}>{item.detail}</span>}
                <Link
                  className={styles.order}
                  href={`/dashboard/${item.order_reference}`}
                >
                  {item.order_title}
                </Link>
              </li>
            ))}
          </ul>

          <p className={styles.hint}>
            Not sure what is needed? <Link href="/support">Ask us</Link> — it is
            attached to your account, so we will know what this is about.
          </p>
        </section>
      )}

      {systems.length > 0 && (
        <section className={styles.panel}>
          <h2 className={styles.title}>Your systems</h2>
          <p className={styles.lede}>
            What we run for you, and whether it is answering. Checked
            automatically, not on request.
          </p>

          <ul className={styles.list}>
            {systems.map((system) => (
              <li key={system.slug} className={styles.system}>
                <span className={styles.systemTop}>
                  <span
                    className={`${styles.dot} ${styles[`dot_${system.health}`]}`}
                    aria-hidden="true"
                  />
                  <span className={styles.systemName}>{system.name}</span>
                  <span className={styles.health}>{system.health_label}</span>
                </span>
                <span className={styles.purpose}>{system.purpose}</span>
                <span className={styles.checked}>
                  {/* "Up" alone is a claim. With a timestamp it is an
                      observation, and a stale one says so. */}
                  {system.checked_at
                    ? `Checked ${ago(system.checked_at)}`
                    : "Not checked yet"}
                  {system.url && (
                    <>
                      {" · "}
                      <a href={system.url} target="_blank" rel="noreferrer">
                        Open
                      </a>
                    </>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/*
        ── WHAT KEEPS RUNNING, AND WHAT IT COSTS ──────────────────────────────

        A retainer, upkeep or hosting arrangement does not appear in the order
        list the way a project does — it has no delivery date and nothing about
        it changes week to week, so it scrolls past as one more finished-looking
        row. That is how somebody ends up paying for something for a year
        without a clear place to look at it.
      */}
      {subscriptions.length > 0 && (
        <section className={styles.panel}>
          <h2 className={styles.title}>Still running</h2>
          <p className={styles.lede}>
            Ongoing arrangements, as opposed to projects with an end date.
          </p>

          <ul className={styles.list}>
            {subscriptions.map((item) => (
              <li key={item.reference} className={styles.running}>
                <Link className={styles.order} href={`/dashboard/${item.reference}`}>
                  {item.title}
                </Link>
                <span className={styles.runningMeta}>
                  {item.kind_label}
                  {item.started_on ? ` · since ${month(item.started_on)}` : ""}
                </span>
              </li>
            ))}
          </ul>

          <p className={styles.hint}>
            Anything outside what one of these covers is a change request. Open
            it and tell us &mdash; we price it before anything is built.
          </p>
        </section>
      )}

      {hosting.length > 0 && (
        <section className={styles.panel}>
          <h2 className={styles.title}>Domains and hosting</h2>
          <p className={styles.lede}>
            What we run or renew on your behalf, and whose name each account is
            in.
          </p>

          <ul className={styles.list}>
            {hosting.map((item) => (
              <li key={item.identifier} className={styles.hosting}>
                <span className={styles.hostingName}>{item.identifier}</span>
                <span className={styles.hostingMeta}>
                  {item.kind_label}
                  {item.provider ? ` · ${item.provider}` : ""}
                  {item.annual_charge_kes
                    ? ` · KES ${Number(item.annual_charge_kes).toLocaleString("en-KE")} a year`
                    : ""}
                </span>

                {/*
                  Charter 05 §VIII — we do not hold domains or accounts
                  hostage. Saying so is worth little if you cannot see which
                  ones we hold, so it is stated plainly either way, and the
                  case where it is OURS is the one that gets the emphasis.
                */}
                <span
                  className={item.in_our_name ? styles.heldByUs : styles.heldByYou}
                >
                  {item.in_our_name
                    ? "Registered in Genmars' name — ask us any time and we will transfer it to you."
                    : "Registered in your name."}
                </span>

                {item.renews_on && (
                  <span className={styles.renews}>
                    {(item.days_until_renewal ?? 0) < 0
                      ? `Expired ${Math.abs(item.days_until_renewal ?? 0)} days ago`
                      : `Renews ${month(item.renews_on)}`}
                    {item.auto_renew ? " · renews automatically" : ""}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/** "June 2025" — a day number on a renewal date reads as a deadline. */
function month(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("en-KE", { month: "long", year: "numeric" });
}

function ago(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "recently";
  const minutes = Math.round((Date.now() - parsed.getTime()) / 60000);
  if (minutes < 2) return "just now";
  if (minutes < 60) return `${minutes} minutes ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hours ago`;
  return `${Math.round(hours / 24)} days ago`;
}
