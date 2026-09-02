"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { LoadingMark } from "@/components/LoadingMark";
import { ApiError, portal, type InvoiceDocument } from "@/lib/api";
import styles from "./page.module.css";

/**
 * One invoice, as a document.
 *
 * ── WHY THIS EXISTS SEPARATELY FROM THE ROW ON THE ORDER PAGE ───────────────
 *
 * A row answers "what do I owe". This answers "who is billing me, for what,
 * under what agreement, and how do I pay" — which is what somebody's accounts
 * department actually needs, and what they will print or save as a PDF.
 *
 * ── THE PRINT STYLESHEET IS THE FEATURE ─────────────────────────────────────
 *
 * There is no PDF generator here and there does not need to be one. Every
 * browser prints to PDF, and page.module.css has an @media print block that
 * strips the navigation, the theme, and the "back" link, leaving a document on
 * white paper. That is a real deliverable built out of things that already
 * exist, rather than a new dependency (Charter 03 §I — nothing new in the
 * stack without a reason).
 *
 * ── THE PAY BUTTON APPEARS ONLY WHEN IT WORKS ───────────────────────────────
 *
 * `payment.stk_available` is derived on the server from whether real M-Pesa
 * credentials are present — it cannot be switched on by hand. While it is
 * false there is no button at all, because a control that only marked a row
 * would leave the client believing they had paid, which is the worst outcome
 * available here (Charter 04 §IV).
 *
 * When it is true the button is real, and it still does not mean "paid": a
 * successful push means Safaricom sent a prompt. The page then polls until the
 * callback resolves it, and says so rather than implying the money has moved.
 */
export default function InvoicePage() {
  const params = useParams<{ reference: string; number: string }>();
  const [doc, setDoc] = useState<InvoiceDocument | null>(null);
  const [error, setError] = useState<"missing" | "failed" | null>(null);

  const reference = params.reference;
  const number = params.number;

  useEffect(() => {
    if (!reference || !number) return;
    portal
      .invoice(reference, number)
      .then(setDoc)
      .catch((e) =>
        setError(e instanceof ApiError && e.status === 404 ? "missing" : "failed"),
      );
  }, [reference, number]);

  if (error) {
    return (
      <div className="wrap">
        <p className={styles.notFound} role="alert">
          {error === "missing"
            ? "We could not find that invoice on your account."
            : "We could not load that invoice just now."}
        </p>
        <p>
          <Link href={`/dashboard/${reference}`}>Back to this engagement</Link>
        </p>
      </div>
    );
  }

  if (!doc) {
    return (
      <div className={styles.loading}>
        <LoadingMark size={32} label="Loading this invoice" />
      </div>
    );
  }

  const { invoice, biller, billed_to, payment, order } = doc;
  const settled = invoice.status === "paid";
  const withdrawn = invoice.status === "void";

  return (
    <div className="wrap">
      <p className={styles.back}>
        <Link href={`/dashboard/${reference}`}>&larr; {order.title}</Link>
      </p>

      <article
        className={`${styles.doc} ${withdrawn ? styles.docVoid : ""}`}
        aria-label={`Invoice ${invoice.number}`}
      >
        {/* A withdrawn invoice is still reachable — they were sent it, and a
            link that 404s after we void it looks like something being hidden.
            It says so at the top, before the amount. */}
        {withdrawn ? (
          <p className={styles.voidBanner}>
            <strong>This invoice has been withdrawn.</strong> Nothing is owed
            against it.{invoice.void_reason ? ` ${invoice.void_reason}` : ""}
          </p>
        ) : null}

        <header className={styles.head}>
          <div>
            <h1 className={styles.number}>{invoice.number}</h1>
            <p className={styles.title}>Invoice</p>
          </div>
          <div className={styles.status}>
            {settled ? (
              <span className={styles.paid}>Paid</span>
            ) : withdrawn ? (
              <span className={styles.void}>Void</span>
            ) : invoice.overdue ? (
              <span className={styles.overdue}>Overdue</span>
            ) : (
              <span className={styles.due}>Due</span>
            )}
          </div>
        </header>

        <div className={styles.parties}>
          <section>
            <h2 className={styles.label}>From</h2>
            <p className={styles.partyName}>{biller.legal_name}</p>
            {biller.postal_address ? (
              <p className={styles.partyLine}>{biller.postal_address}</p>
            ) : null}
            <p className={styles.partyLine}>{biller.email}</p>
            {/* Omitted entirely until configured, never rendered blank. */}
            {biller.kra_pin ? (
              <p className={styles.partyLine}>KRA PIN {biller.kra_pin}</p>
            ) : null}
          </section>

          <section>
            <h2 className={styles.label}>To</h2>
            <p className={styles.partyName}>{billed_to.organisation}</p>
            {billed_to.contact ? (
              <p className={styles.partyLine}>{billed_to.contact}</p>
            ) : null}
          </section>

          <section>
            <h2 className={styles.label}>Dates</h2>
            <p className={styles.partyLine}>Issued {date(invoice.issued_on)}</p>
            <p className={styles.partyLine}>
              {invoice.due_on ? `Due ${date(invoice.due_on)}` : "No due date"}
            </p>
            {invoice.paid_on ? (
              <p className={styles.partyLine}>Paid {date(invoice.paid_on)}</p>
            ) : null}
          </section>
        </div>

        <table className={styles.lines}>
          <thead>
            <tr>
              <th scope="col">Description</th>
              <th scope="col" className={styles.numeric}>
                Amount
              </th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>
                {invoice.description}
                {/* The agreement this arises from. "What is this for" should
                    not require a phone call. */}
                {order.contract_reference ? (
                  <span className={styles.against}>
                    Under {order.contract_reference}
                    {order.contract_signed_on
                      ? `, signed ${date(order.contract_signed_on)}`
                      : ""}{" "}
                    &middot; {order.reference}
                  </span>
                ) : null}
              </td>
              <td className={styles.numeric}>{money(invoice.amount_kes)}</td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <th scope="row">Total</th>
              <td className={styles.numeric}>{money(invoice.amount_kes)}</td>
            </tr>
          </tfoot>
        </table>

        {settled ? (
          <section className={styles.settled}>
            <h2 className={styles.label}>Payment received</h2>
            <p className={styles.partyLine}>
              {date(invoice.paid_on!)}
              {/* Shown so they can confirm we credited the payment they
                  actually made, rather than taking "paid" on trust. */}
              {invoice.payment_reference
                ? ` · reference ${invoice.payment_reference}`
                : ""}
            </p>
          </section>
        ) : withdrawn ? null : (
          <section className={styles.pay}>
            <h2 className={styles.label}>How to pay</h2>

            {payment.mpesa_paybill ? (
              <dl className={styles.payDetails}>
                <dt>M-Pesa paybill</dt>
                <dd>{payment.mpesa_paybill}</dd>
                <dt>Account</dt>
                <dd>{payment.mpesa_account}</dd>
              </dl>
            ) : null}

            {payment.bank_details ? (
              <p className={styles.bank}>{payment.bank_details}</p>
            ) : null}

            {/* Nothing configured yet. Saying so is better than an empty
                heading, and better than inventing a number. */}
            {!payment.mpesa_paybill && !payment.bank_details ? (
              <p className={styles.partyLine}>
                Payment details will come from your point of contact. If you
                need them now, reply to the email this invoice arrived with.
              </p>
            ) : null}

            {payment.stk_available ? (
              <MpesaPay
                reference={order.reference}
                number={invoice.number}
                onPaid={() => {
                  // Re-fetch rather than patching state by hand: paid_on and
                  // the receipt come from the server, and inventing them here
                  // would show a client a payment record we had not stored.
                  void portal.invoice(reference, number).then(setDoc);
                }}
              />
            ) : null}

            <p className={styles.terms}>{payment.terms}</p>
          </section>
        )}

        <footer className={styles.foot}>
          <p>
            Questions about this invoice go to your point of contact on{" "}
            {order.reference}. If our records disagree with yours, tell us
            &mdash; ours is the copy that is wrong until we have checked.
          </p>
        </footer>
      </article>

      <p className={styles.printHint}>
        <button
          type="button"
          className={styles.printButton}
          onClick={() => window.print()}
        >
          Print or save as PDF
        </button>
      </p>
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
 * Parsed once, at the last possible moment, for display only. On a document
 * somebody pays against, the decimals are shown rather than rounded away.
 */
function money(amountKes: string): string {
  const n = Number(amountKes);
  if (!Number.isFinite(n)) return `KES ${amountKes}`;
  return `KES ${n.toLocaleString("en-KE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}


/**
 * Pay this invoice by M-Pesa.
 *
 * ── SENDING A PROMPT IS NOT BEING PAID ──────────────────────────────────────
 *
 * The push returns 202 the moment Safaricom accepts the prompt for delivery.
 * The customer has not seen it, let alone entered a PIN. So the copy says to
 * check the phone, and the page polls until the server — which only learns the
 * truth from Safaricom's callback — says the invoice is settled.
 *
 * Polling stops on its own. A prompt that nobody answers times out at
 * Safaricom's end, and a page that polled forever would keep a tab busy all
 * night for a payment that already failed.
 */
function MpesaPay({
  reference,
  number,
  onPaid,
}: {
  reference: string;
  number: string;
  onPaid: () => void;
}) {
  const [phone, setPhone] = useState("");
  const [sending, setSending] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!waiting) return;

    // Safaricom's prompt expires in about a minute; a little over two minutes
    // of polling covers it with room for a slow callback, and then stops.
    let elapsed = 0;
    const every = 4000;
    const limit = 140_000;

    const id = window.setInterval(async () => {
      elapsed += every;
      try {
        const status = await portal.paymentStatus(reference, number);
        if (status.invoice_status === "paid") {
          window.clearInterval(id);
          setWaiting(false);
          onPaid();
          return;
        }
        if (status.attempt && status.attempt.status === "failed") {
          window.clearInterval(id);
          setWaiting(false);
          setNotice(null);
          setError(
            status.attempt.result_desc ||
              "That prompt was not completed. You can try again.",
          );
          return;
        }
      } catch {
        // A dropped poll is not a failed payment. Keep waiting; the timeout
        // below is what ends it.
      }
      if (elapsed >= limit) {
        window.clearInterval(id);
        setWaiting(false);
        setNotice(null);
        setError(
          "We have not heard back about that prompt. If you completed it, the " +
            "invoice will update shortly — refresh in a minute before trying again.",
        );
      }
    }, every);

    return () => window.clearInterval(id);
  }, [waiting, reference, number, onPaid]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSending(true);
    setError(null);
    setNotice(null);
    try {
      const result = await portal.payInvoice(reference, number, phone);
      setNotice(
        `Check the phone ending ${result.phone_tail} for the M-Pesa prompt and ` +
          "enter your PIN. This page updates once it goes through.",
      );
      setWaiting(true);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not start that payment.",
      );
    } finally {
      setSending(false);
    }
  }

  return (
    <div className={styles.mpesa}>
      <h3 className={styles.mpesaTitle}>Pay by M-Pesa</h3>
      <form className={styles.mpesaForm} onSubmit={submit}>
        <input
          className={styles.mpesaInput}
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          placeholder="07XX XXX XXX"
          aria-label="Phone number to prompt"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          disabled={waiting}
          required
        />
        <button className={styles.mpesaButton} type="submit" disabled={sending || waiting}>
          {waiting ? "Waiting for the prompt" : sending ? "Sending" : "Send prompt"}
        </button>
      </form>
      {notice ? <p className={styles.mpesaNotice}>{notice}</p> : null}
      {error ? <p className={styles.mpesaError}>{error}</p> : null}
    </div>
  );
}
