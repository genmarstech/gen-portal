"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, portal, type OfferDocument } from "@/lib/api";
import styles from "./page.module.css";

/**
 * One quote or proposal, as a document.
 *
 * ── WHY THIS PAGE EXISTS SEPARATELY FROM THE OFFERS LIST ────────────────────
 *
 * Because the person reading it is usually not the person who decides. A quote
 * lives or dies on being forwardable — printed, attached to an email, taken
 * into a meeting with whoever signs off. A price that exists only as a row in
 * a list inside a portal is a price the decision-maker never sees, and "they
 * went quiet" is what that looks like from our side.
 *
 * ── THE ORDER OF THE SECTIONS IS THE ARGUMENT ───────────────────────────────
 *
 * What we understood, then what we would do, then what is and is not covered,
 * then how long — and the price after all of it. A number before its reasoning
 * is a number somebody argues with; the same number after it is a number they
 * evaluate.
 *
 * ── PRINTING, NOT A PDF LIBRARY ─────────────────────────────────────────────
 *
 * Charter 03 §I. The print stylesheet already does this for invoices and does
 * it well, and `document.title` gives the saved file a name worth filing under.
 */
export default function OfferDocumentPage() {
  const params = useParams<{ reference: string }>();
  const reference = params?.reference;

  const [doc, setDoc] = useState<OfferDocument | null>(null);
  const [error, setError] = useState<"missing" | "failed" | null>(null);

  useEffect(() => {
    if (!reference) return;
    portal
      .offer(reference)
      .then(setDoc)
      .catch((e) =>
        setError(e instanceof ApiError && e.status === 404 ? "missing" : "failed"),
      );
  }, [reference]);

  // The saved file is named after the quote. Without this a client's folder
  // fills with "Genmars.pdf", "Genmars (1).pdf" — several quotes nobody can
  // tell apart without opening them.
  useEffect(() => {
    if (!reference) return;
    const previous = document.title;
    document.title = reference;
    return () => {
      document.title = previous;
    };
  }, [reference]);

  if (error) {
    return (
      <div className="wrap">
        <p className={styles.notFound} role="alert">
          {error === "missing"
            ? "We could not find that quote on your account."
            : "We could not load that quote just now."}
        </p>
        <p>
          <Link href="/offers">Back to your quotes</Link>
        </p>
      </div>
    );
  }

  if (!doc) {
    return (
      <div className="wrap">
        <p className={styles.loading}>Loading…</p>
      </div>
    );
  }

  const { offer, proposal, terms, biller, offered_to } = doc;
  const open = offer.status === "sent" && !offer.expired;

  return (
    <div className={styles.page}>
      <div className={styles.controls}>
        <Link href="/offers" className={styles.back}>
          &larr; Your quotes
        </Link>
        <div className={styles.controlActions}>
          <button type="button" className={styles.print} onClick={() => window.print()}>
            Download or print
          </button>
          <span className={styles.printNote}>
            Choose &ldquo;Save as PDF&rdquo; to keep a copy.
          </span>
        </div>
      </div>

      <article className={styles.doc}>
        <header className={styles.head}>
          <div>
            <p className={styles.kind}>Quotation</p>
            <h1 className={styles.title}>{offer.title}</h1>
            <p className={styles.reference}>{offer.reference}</p>
          </div>
          <div className={styles.parties}>
            <p className={styles.partyLabel}>From</p>
            <p className={styles.party}>{biller.legal_name}</p>
            {biller.kra_pin && <p className={styles.partyLine}>KRA PIN {biller.kra_pin}</p>}
            {biller.postal_address && (
              <p className={styles.partyLine}>{biller.postal_address}</p>
            )}
            {biller.email && <p className={styles.partyLine}>{biller.email}</p>}

            <p className={styles.partyLabel}>For</p>
            <p className={styles.party}>{offered_to.organisation}</p>
          </div>
        </header>

        {/*
          A withdrawn, declined or expired quote still opens — somebody may be
          holding the link — and it says so at the top rather than presenting a
          price that is no longer ours to honour as though it were.
        */}
        {!open && (
          <p className={styles.state} role="status">
            {offer.expired && offer.status === "sent"
              ? `This quote expired on ${formatDate(offer.expires_on)} and is no longer ours to honour. Ask us and we will re-quote.`
              : `This quote was ${offer.status_label.toLowerCase()}.`}
          </p>
        )}

        <Section heading="What we understood">{proposal.context}</Section>
        <Section heading="How we would do it">{proposal.approach}</Section>
        <Section heading="What the price covers">
          {proposal.inclusions || offer.detail}
        </Section>
        <Section heading="What it does not cover">{proposal.exclusions}</Section>
        <Section heading="How long">{proposal.timeline}</Section>

        <section className={styles.price}>
          <p className={styles.priceLabel}>Price</p>
          <p className={styles.priceValue}>KES {money(offer.amount_kes)}</p>
          {offer.list_price_kes && offer.list_price_kes !== offer.amount_kes && (
            /* What we discounted from. A price without its reference point is
               one they cannot judge, and hiding it would make the discount a
               sales tactic rather than a fact. */
            <p className={styles.listPrice}>
              List price KES {money(offer.list_price_kes)}
            </p>
          )}
          <p className={styles.validity}>Valid until {formatDate(offer.expires_on)}.</p>
        </section>

        <Section heading="Payment">{terms.payment_terms}</Section>
        <Section heading="If you want to go ahead">{terms.next_step}</Section>
        <Section heading="Terms">{terms.standing_terms}</Section>

        {open && (
          <p className={styles.accept}>
            You can accept or decline this on{" "}
            <Link href="/offers">your quotes page</Link>. Accepting does not
            start work &mdash; we will send a statement of work to agree first.
          </p>
        )}
      </article>
    </div>
  );
}

/** Renders nothing at all when the section was not written. */
function Section({ heading, children }: { heading: string; children?: string | null }) {
  if (!children || !children.trim()) return null;
  return (
    <section className={styles.section}>
      <h2 className={styles.sectionHeading}>{heading}</h2>
      <p className={styles.sectionBody}>{children}</p>
    </section>
  );
}

function money(value: string): string {
  return Number(value).toLocaleString("en-KE", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatDate(value: string): string {
  return new Date(value).toLocaleDateString("en-KE", {
    day: "numeric",
    month: "long",
    year: "numeric",
  });
}
