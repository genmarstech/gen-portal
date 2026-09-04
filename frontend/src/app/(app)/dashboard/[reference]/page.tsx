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

      {/*
        ── WHAT CHANGED SINCE YOU LAST LOOKED ─────────────────────────────────

        Read on the server BEFORE this visit was stamped, so the page that
        clears the marker is also the page that says what it was for. Without
        that, opening the order answers "something changed" with silence.

        Above the scope rather than below it, because "scope changed" is the
        reason that matters most and the reader needs it before they start
        reading the scope as though it were the one they agreed to.
      */}
      {order.unseen ? (
        <p className={styles.changed} role="status">
          <strong>{order.unseen}</strong> since you last opened this.
        </p>
      ) : null}

      {/* ---------- what was agreed ----------
        WHEN A CONTRACT EXISTS, THIS RENDERS THE CONTRACT, NOT THE ORDER.

        The order's scope is what the work has since moved to; the contract is
        what the client agreed to. Rendering the live order under a heading
        reading "what was agreed" would tell them they signed something they
        did not — which is the exact failure the snapshot on the contract
        exists to prevent, reintroduced at the last step.

        With no contract yet the order's scope is shown, and labelled as not
        yet agreed. That is honest: an order at scoping has a proposal, not an
        agreement, and Charter 02 §I is explicit that work begins when a
        statement of work is signed.
      */}
      <section className={styles.section}>
        <h2 className={styles.h2}>What was agreed</h2>

        {order.contract ? (
          <p className={styles.agreedMeta}>
            {order.contract.reference}
            {order.contract.signed_on
              ? ` · signed ${order.contract.signed_on}${
                  order.contract.signed_by_name ? ` by ${order.contract.signed_by_name}` : ""
                }`
              : " · issued, not yet signed"}
          </p>
        ) : (
          <p className={styles.agreedMeta}>
            Nothing signed yet. This is the scope as it stands while we agree
            it — work begins once a statement of work is signed.
          </p>
        )}

        <div className={styles.pair}>
          <div>
            <h3 className={styles.h3}>In scope</h3>
            <Prose text={order.contract ? order.contract.scope : order.scope} />

            {order.contract && order.contract.deliverable_list.length > 0 ? (
              <>
                <h3 className={styles.h3}>You receive</h3>
                <ul className={styles.deliverables}>
                  {order.contract.deliverable_list.map((d) => (
                    <li key={d}>{d}</li>
                  ))}
                </ul>
              </>
            ) : null}
          </div>
          <div className={styles.exclusions}>
            <h3 className={styles.h3}>Not included</h3>
            {(order.contract ? order.contract.exclusions : order.exclusions).trim() ? (
              <Prose text={order.contract ? order.contract.exclusions : order.exclusions} />
            ) : (
              <p className={styles.none}>
                No exclusions were recorded for this engagement.
              </p>
            )}
            <p className={styles.excNote}>
              Anything not in the scope above is a change request: we price it
              and you approve it before it is built.
            </p>
            {/*
              This page has always TOLD them that. Until now it gave them
              nowhere to do it, so the sentence read as a rule rather than as
              an invitation — and a change request arrived as a loose support
              message, or as a phone call nobody wrote down.
            */}
            <ChangeRequest order={order} />
          </div>
        </div>
      </section>

      {/* ---------- progress ---------- */}
      <section className={styles.section}>
        <h2 className={styles.h2}>Progress</h2>
        {order.notes.length === 0 ? (
          /*
            The weekly promise is Charter 05 §III and it is real — but only for
            work being done now. Repeating it against an engagement recorded
            after it finished would promise updates about something already
            delivered, which is a promise we would then not keep.
          */
          <p className={styles.none}>
            {order.recorded_retrospectively
              ? "This work was recorded from our own records after it was done, so it has no weekly notes."
              : "No progress notes have been published yet. You will get a written update every week this engagement is active."}
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

      {/* ---------- invoices ---------- */}
      {order.invoices.length > 0 ? (
        <section className={styles.section}>
          <h2 className={styles.h2}>Invoices</h2>
          <ul className={styles.invoices}>
            {order.invoices.map((i) => (
              <li
                key={i.number}
                className={`${styles.invoice} ${
                  i.status === "void" ? styles.invoiceVoid : ""
                }`}
              >
                {/* Through to the printable document — what an accounts
                    department actually needs. */}
                <Link
                  className={styles.iNumber}
                  href={`/invoices/${i.number}`}
                >
                  {i.number}
                </Link>
                <span className={styles.iDescription}>{i.description}</span>
                <span className={styles.iAmount}>{money(i.amount_kes)}</span>
                <span
                  className={`${styles.iStatus} ${
                    i.overdue ? styles.i_overdue : styles[`i_${i.status}`] ?? ""
                  }`}
                >
                  {i.overdue ? "Overdue" : i.status_label}
                </span>

                <span className={styles.iMeta}>
                  Issued {date(i.issued_on)}
                  {i.due_on ? ` · due ${date(i.due_on)}` : ""}
                  {i.paid_on ? ` · paid ${date(i.paid_on)}` : ""}
                  {/* The reference we matched it against, so they can confirm
                      we credited the payment they actually made rather than
                      taking "paid" on trust. */}
                  {i.payment_reference ? ` · ref ${i.payment_reference}` : ""}
                </span>

                {/* A voided invoice is one we already SENT. Hiding it would
                    leave them holding a document this page says does not
                    exist, and they would find out by paying it. */}
                {i.status === "void" && i.void_reason ? (
                  <span className={styles.iVoidReason}>
                    Withdrawn — {i.void_reason}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
          <p className={styles.mNote}>
            We do not take payments through this portal. Invoices are settled
            the way they always have been, and what you see here is what we
            have recorded against your account.
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


/**
 * Ask for a change to THIS piece of work.
 *
 * ── WHY IT IS HERE AND NOT ON THE SUPPORT PAGE ──────────────────────────────
 *
 * A change request is about a specific scope, and the scope is on this page.
 * Sending somebody to a general support form means they describe the work
 * again from memory, and we get a message that has to be matched back to an
 * order by hand — which is how a request for a change to one project gets
 * quoted against another.
 *
 * It files a support request with this order attached, so the conversation
 * lives against the work rather than beside it.
 *
 * ── IT PROMISES NOTHING ABOUT TIMING ────────────────────────────────────────
 *
 * Charter 03 §IV: no response time is claimed anywhere, because none has been
 * tested. What it says instead is what actually happens next — we price it and
 * they approve it before anything is built.
 */
function ChangeRequest({ order }: { order: OrderDetail }) {
  const [open, setOpen] = useState(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (sent) {
    return (
      <p className={styles.changeDone} role="status">
        Sent, as {sent}. It is filed against this work, and we will come back to
        you with a price before anything is built.
      </p>
    );
  }

  if (!open) {
    return (
      <button type="button" className={styles.changeBtn} onClick={() => setOpen(true)}>
        Ask for a change to this work
      </button>
    );
  }

  return (
    <form
      className={styles.changeForm}
      onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true);
        setError(null);
        try {
          const ticket = await portal.raiseTicket({
            subject,
            body,
            order: order.reference,
          });
          setSent(ticket.reference);
        } catch (err) {
          setError(
            err instanceof ApiError ? err.message : "That did not send. Try again.",
          );
        } finally {
          setBusy(false);
        }
      }}
    >
      <label className={styles.changeField}>
        <span className={styles.changeLabel}>What would you like changed?</span>
        <input
          className={styles.changeInput}
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          placeholder="Add card payments to the booking form"
          required
        />
      </label>

      <label className={styles.changeField}>
        <span className={styles.changeLabel}>Tell us a bit more</span>
        <textarea
          className={styles.changeInput}
          rows={4}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="What it needs to do, and what is prompting it."
          required
        />
      </label>

      {error ? (
        <p className={styles.changeError} role="alert">
          {error}
        </p>
      ) : null}

      <div className={styles.changeActions}>
        <button type="submit" className={styles.changeBtn} disabled={busy}>
          {busy ? "Sending…" : "Send it"}
        </button>
        <button
          type="button"
          className={styles.changeCancel}
          onClick={() => setOpen(false)}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
