"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { LoadingMark } from "@/components/LoadingMark";
import { portal, type ClientInvoice } from "@/lib/api";
import styles from "./page.module.css";

/**
 * Every invoice addressed to this client, in one place.
 *
 * ── WHY THIS PAGE HAD TO EXIST ──────────────────────────────────────────────
 *
 * Invoices used to be reachable only inside their order. That was fine while
 * every invoice had one, but an invoice can now be raised straight to a client
 * — a renewal, an afternoon's work, something with no project behind it — and
 * those have no order to be nested under. Without this page a client could be
 * sent a bill their own portal insisted did not exist.
 *
 * ── VOIDED INVOICES ARE LISTED ──────────────────────────────────────────────
 *
 * Same reasoning as the serializer that sends them: a voided invoice is one we
 * already SENT. It is in their inbox and possibly in their accounts system.
 * Hiding it here means the only way they discover it was withdrawn is by
 * paying it.
 */
export default function InvoicesPage() {
  const [invoices, setInvoices] = useState<ClientInvoice[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    portal
      .invoices()
      .then((body) => {
        if (!cancelled) setInvoices(body.invoices);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (failed) {
    return (
      <div className="wrap">
        <p className={styles.failed}>
          We could not load your invoices just now. Refresh, and if it happens
          again tell us at{" "}
          <a href="mailto:info@genmars.co.ke">info@genmars.co.ke</a>.
        </p>
      </div>
    );
  }

  if (invoices === null) {
    return (
      <div className={styles.booting}>
        <LoadingMark size={34} label="Loading your invoices" />
      </div>
    );
  }

  const outstanding = invoices.filter((i) => i.status === "issued");
  const settled = invoices.filter((i) => i.status !== "issued");

  return (
    <div className="wrap">
      <header className={styles.head}>
        <h1 className={styles.title}>Invoices</h1>
        <p className={styles.lede}>
          Everything we have billed you for, and what we have recorded against
          each one.
        </p>
      </header>

      {invoices.length === 0 && (
        <p className={styles.empty}>
          Nothing has been invoiced yet. When it is, it appears here and you
          will get a notification.
        </p>
      )}

      {outstanding.length > 0 && (
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>Outstanding</h2>
          <ul className={styles.list}>
            {outstanding.map((invoice) => (
              <InvoiceCard key={invoice.number} invoice={invoice} />
            ))}
          </ul>
        </section>
      )}

      {settled.length > 0 && (
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>Settled and withdrawn</h2>
          <ul className={styles.list}>
            {settled.map((invoice) => (
              <InvoiceCard key={invoice.number} invoice={invoice} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function InvoiceCard({ invoice }: { invoice: ClientInvoice }) {
  const partPaid =
    invoice.status === "issued" &&
    invoice.amount_paid !== undefined &&
    Number(invoice.amount_paid) > 0;

  return (
    <li className={`${styles.card} ${invoice.status === "void" ? styles.voided : ""}`}>
      <div className={styles.cardHead}>
        <span className={styles.number}>{invoice.number}</span>
        <StatusPill invoice={invoice} />
      </div>

      <p className={styles.description}>{invoice.description}</p>

      <dl className={styles.figures}>
        <div>
          <dt>Amount</dt>
          <dd className={styles.amount}>{money(invoice.amount_kes)}</dd>
        </div>
        {partPaid && (
          <>
            <div>
              <dt>Received</dt>
              <dd>{money(invoice.amount_paid!)}</dd>
            </div>
            <div>
              <dt>Still owing</dt>
              <dd className={styles.amount}>{money(invoice.balance!)}</dd>
            </div>
          </>
        )}
        <div>
          <dt>Issued</dt>
          <dd>{day(invoice.issued_on)}</dd>
        </div>
        {invoice.due_on && (
          <div>
            <dt>Due</dt>
            <dd>{day(invoice.due_on)}</dd>
          </div>
        )}
      </dl>

      {/* The references we matched, so they can confirm we credited the
          payment they actually made rather than taking "paid" on trust. */}
      {invoice.payments && invoice.payments.length > 0 && (
        <div className={styles.payments}>
          <h3 className={styles.paymentsTitle}>
            {invoice.payments.length === 1 ? "Payment received" : "Payments received"}
          </h3>
          <ul className={styles.paymentList}>
            {invoice.payments.map((payment, index) => (
              <li key={`${payment.reference}-${index}`}>
                <span className={styles.paymentAmount}>{money(payment.amount_kes)}</span>
                <span className={styles.paymentMeta}>
                  {payment.method_label}
                  {payment.reference ? ` · ${payment.reference}` : ""} ·{" "}
                  {day(payment.paid_on)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {invoice.status === "void" && invoice.void_reason && (
        <p className={styles.voidReason}>
          <strong>Withdrawn.</strong> {invoice.void_reason}
        </p>
      )}

      {/* EVERY invoice, not just order-backed ones. The document route is
          addressed by number now, so a renewal or an afternoon's work opens
          the same way a project bill does — previously those listed here with
          nothing to open, which is a bill the client cannot read. */}
      <Link className={styles.open} href={`/invoices/${invoice.number}`}>
        {invoice.status === "issued" ? "View and pay" : "View invoice"}
      </Link>
    </li>
  );
}

function StatusPill({ invoice }: { invoice: ClientInvoice }) {
  if (invoice.status === "paid") {
    return <span className={`${styles.pill} ${styles.paid}`}>Paid</span>;
  }
  if (invoice.status === "void") {
    return <span className={`${styles.pill} ${styles.void}`}>Withdrawn</span>;
  }
  if (invoice.overdue) {
    return <span className={`${styles.pill} ${styles.overdue}`}>Overdue</span>;
  }
  return <span className={styles.pill}>Outstanding</span>;
}

/**
 * The amount arrives as a decimal STRING and is formatted, never parsed into a
 * number for display. Money through a float is money that cannot be reconciled
 * against a statement, and this is the figure someone pays.
 */
function money(value: string): string {
  const [whole = "0", cents = "00"] = value.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `KES ${grouped}.${cents}`;
}

function day(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("en-KE", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}
