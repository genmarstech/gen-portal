"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { LoadingMark } from "@/components/LoadingMark";
import { DashboardApps } from "./DashboardApps";
import { EmptyState } from "@/components/app/EmptyState";
import { portal, type OrderSummary } from "@/lib/api";
import styles from "./page.module.css";

/**
 * The work list.
 *
 * An account with NO order is the common case early on — someone signed up and
 * nothing has been agreed yet. That is an ordinary state, not an error, and it
 * gets a real empty state rather than a blank page or a spinner that never
 * resolves.
 */
export default function DashboardPage() {
  const [orders, setOrders] = useState<OrderSummary[] | null>(null);
  const [hasEnquiry, setHasEnquiry] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    portal
      .orders()
      .then((r) => {
        setOrders(r.orders);
        setHasEnquiry(r.has_enquiry);
      })
      .catch(() => setError("We could not load your work just now."));
  }, []);

  if (error) {
    return (
      <div className="wrap">
        <p className={styles.error} role="alert">
          {error} Refresh, and if it keeps happening tell us — that is a fault
          on our side, not yours.
        </p>
      </div>
    );
  }

  if (orders === null) {
    return (
      <div className={styles.loading}>
        <LoadingMark size={32} label="Loading your work" />
      </div>
    );
  }

  if (orders.length === 0) return <EmptyState hasEnquiry={hasEnquiry} />;

  return (
    <div className="wrap">
      <header className={styles.head}>
        <p className="eyebrow">Your work</p>
        <h1 className={styles.title}>
          {orders.length === 1 ? "One engagement" : `${orders.length} engagements`}
        </h1>
      </header>

      {/* Above the engagement list, because these are the things that need
          somebody to do something. They render nothing when there is nothing —
          see DashboardApps. */}
      <DashboardApps />

      <ul className={styles.list}>
        {orders.map((order) => (
          <li key={order.reference}>
            <Link
              href={`/dashboard/${order.reference}`}
              className={`${styles.card} ${order.unseen ? styles.cardUnseen : ""}`}
            >
              <span className={styles.cardRef}>
                {order.reference}
                {/*
                  What changed, in words, not a bare dot. A dot says there is
                  something to find without saying whether it is worth finding,
                  and half of them will not go looking — which for a scope
                  change is the promise in Charter 05 §I quietly breaking.
                */}
                {order.unseen ? (
                  <span className={styles.unseen}>
                    <span className={styles.unseenDot} aria-hidden="true" />
                    {order.unseen}
                  </span>
                ) : null}
              </span>
              <span className={styles.cardTitle}>{order.title}</span>
              <span className={styles.cardMeta}>
                <span className={`${styles.status} ${styles[order.status] ?? ""}`}>
                  {order.status_label}
                </span>
                {order.target_date ? (
                  <span className={styles.date}>
                    Target {formatDate(order.target_date)}
                  </span>
                ) : null}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* Local, not exported: a Next.js page module may only export `default` and the
   framework's own reserved keys — anything else fails the build. */
function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}
