"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { LoadingMark } from "@/components/LoadingMark";
import { ApiError, portal, type OrderDetail } from "@/lib/api";
import styles from "./page.module.css";

/**
 * One engagement.
 *
 * The layout follows Charter 05 §I clause by clause, and the order is
 * deliberate: what was agreed, then what is happening, then what is owed.
 *
 *   scope + EXCLUSIONS   — stated as prominently as scope. The charter promises
 *                          "anything not in the written scope is a change
 *                          request", and a client can only hold us to that if
 *                          the exclusions are in front of them, not buried.
 *   progress notes       — §III, the weekly written update, newest first
 *   milestones           — §VI, with payment status
 *   named contact        — §I
 *
 * A 404 here means the reference does not belong to this client's organisation.
 * The API returns 404 rather than 403 on purpose (portal/views.py): a 403 would
 * confirm the order exists, which is itself a leak.
 */
export default function OrderPage() {
  const params = useParams<{ reference: string }>();
  const reference = params.reference;
  const [order, setOrder] = useState<OrderDetail | null>(null);
  const [error, setError] = useState<"missing" | "failed" | null>(null);

  useEffect(() => {
    if (!reference) return;
    portal
      .order(reference)
      .then(setOrder)
      .catch((e) => setError(e instanceof ApiError && e.status === 404 ? "missing" : "failed"));
  }, [reference]);

  if (error) {
    return (
      <div className="wrap">
        <p className={styles.notFound} role="alert">
          {error === "missing"
            ? "We could not find that engagement on your account."
            : "We could not load that engagement just now."}
        </p>
        <p>
          <Link href="/dashboard">Back to your work</Link>
        </p>
      </div>
    );
  }

  if (!order) {
    return (
      <div className={styles.loading}>
        <LoadingMark size={32} label="Loading this engagement" />
      </div>
    );
  }

  return (
    <article className="wrap">
      <header className={styles.head}>
        <Link href="/dashboard" className={styles.back}>
          &larr; Your work
        </Link>
        <p className={styles.ref}>{order.reference}</p>
        <h1 className={styles.title}>{order.title}</h1>
        <div className={styles.headMeta}>
          <span className={`${styles.status} ${styles[order.status] ?? ""}`}>
            {order.status_label}
          </span>
          <span>{order.organisation}</span>
          {order.started_on ? <span>Started {date(order.started_on)}</span> : null}
          {order.target_date ? <span>Target {date(order.target_date)}</span> : null}
        </div>
      </header>

      {/* ---------- what was agreed ---------- */}
      <section className={styles.section}>
        <h2 className={styles.h2}>What was agreed</h2>
        <div className={styles.pair}>
          <div>
            <h3 className={styles.h3}>In scope</h3>
            <Prose text={order.scope} />
          </div>
          <div className={styles.exclusions}>
            <h3 className={styles.h3}>Not included</h3>
            {order.exclusions.trim() ? (
              <Prose text={order.exclusions} />
            ) : (
              <p className={styles.none}>
                No exclusions were recorded for this engagement.
              </p>
            )}
            <p className={styles.excNote}>
              Anything not in the scope above is a change request: we price it
              and you approve it before it is built.
            </p>
          </div>
        </div>
      </section>

      {/* ---------- progress ---------- */}
      <section className={styles.section}>
        <h2 className={styles.h2}>Progress</h2>
        {order.notes.length === 0 ? (
          <p className={styles.none}>
            No progress notes have been published yet. You will get a written
            update every week this engagement is active.
          </p>
        ) : (
          <ol className={styles.notes}>
            {order.notes.map((note) => (
              <li key={note.week_of} className={styles.note}>
                <div className={styles.noteMeta}>
                  <span className={styles.noteWeek}>Week of {date(note.week_of)}</span>
                  <span className={styles.noteAuthor}>{note.author}</span>
                </div>
                <Prose text={note.body} className={styles.noteBody} />
              </li>
            ))}
          </ol>
        )}
      </section>

      {/* ---------- milestones ---------- */}
      {order.milestones.length > 0 ? (
        <section className={styles.section}>
          <h2 className={styles.h2}>Milestones</h2>
          <ul className={styles.milestones}>
            {order.milestones.map((m) => (
              <li key={m.name} className={styles.milestone}>
                <span className={styles.mName}>{m.name}</span>
                <span className={styles.mAmount}>{money(m.amount_kes)}</span>
                <span className={styles.mDue}>
                  {m.due_on ? date(m.due_on) : "—"}
                </span>
                <span className={`${styles.mStatus} ${styles[`m_${m.status}`] ?? ""}`}>
                  {m.status_label}
                </span>
              </li>
            ))}
          </ul>
          <p className={styles.mNote}>
            Payment status shown here reflects what we have recorded. If it does
            not match your records, tell your contact — ours is the copy that is
            wrong until we have checked.
          </p>
        </section>
      ) : null}

      {/* ---------- contact ---------- */}
      <section className={styles.section}>
        <h2 className={styles.h2}>Your point of contact</h2>
        <p className={styles.contact}>
          <span className={styles.contactName}>{order.contact.full_name}</span>
          <a href={`mailto:${order.contact.email}`}>{order.contact.email}</a>
        </p>
      </section>
    </article>
  );
}

/**
 * Plain text into paragraphs.
 *
 * Deliberately NOT markdown or HTML. Scope text is written by staff in the
 * Django admin and rendered into a client's browser; the safe thing to do with
 * it is treat it as text, and React escapes it. A rich-text pipeline here would
 * be an injection surface bought for nothing.
 */
function Prose({ text, className }: { text: string; className?: string }) {
  const paragraphs = text.split(/\n{2,}/).filter((p) => p.trim());
  return (
    <div className={`${styles.prose} ${className ?? ""}`}>
      {paragraphs.map((p, i) => (
        <p key={i}>{p}</p>
      ))}
    </div>
  );
}

function date(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/**
 * The API sends amounts as decimal STRINGS so they survive the trip intact.
 * Formatting parses once, at the last possible moment, for display only —
 * the parsed number is never sent anywhere or used in arithmetic.
 */
function money(amountKes: string): string {
  const n = Number(amountKes);
  if (!Number.isFinite(n)) return `KES ${amountKes}`;
  return `KES ${n.toLocaleString("en-KE", { maximumFractionDigits: 0 })}`;
}
