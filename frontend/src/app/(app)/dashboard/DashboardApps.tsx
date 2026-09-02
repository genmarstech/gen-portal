"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { portal, type WaitingOnYou, type YourSystem } from "@/lib/api";
import styles from "./DashboardApps.module.css";

/**
 * The two things on this dashboard a client can act on.
 *
 * ── WHY ONLY TWO ────────────────────────────────────────────────────────────
 *
 * A dashboard that shows everything is one nobody reads twice. Invoices,
 * offers, progress notes and support all have their own pages and are better
 * there. These two earn a place because they change what somebody DOES:
 *
 *   · what we are waiting on them for — the commonest way a project quietly
 *     stops moving is that we are blocked on the client and the client does
 *     not know it, because the fact only ever lived on an internal board;
 *   · whether the thing we run for them is up — otherwise they find out by
 *     emailing to ask.
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

  useEffect(() => {
    let cancelled = false;
    portal
      .dashboard()
      .then((body) => {
        if (cancelled) return;
        setWaiting(body.waiting_on_you);
        setSystems(body.systems);
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

  if (waiting.length === 0 && systems.length === 0) return null;

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
    </div>
  );
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
