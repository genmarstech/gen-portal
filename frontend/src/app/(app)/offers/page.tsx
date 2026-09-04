"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { LoadingMark } from "@/components/LoadingMark";
import { ApiError, portal, type ClientOffer } from "@/lib/api";
import styles from "./page.module.css";

/**
 * Offers Genmars has put to this client.
 *
 * ── ACCEPTING IS A DECISION, SO IT IS PRESENTED AS ONE ──────────────────────
 *
 * The price, what it includes and the date it expires are all on screen before
 * the button. No countdown, no "only today" — Charter 04 §III is specific over
 * impressive, and manufactured urgency is the opposite of a price we are
 * simply willing to honour until a stated date.
 *
 * ── AND IT DOES NOT START WORK ──────────────────────────────────────────────
 *
 * Said plainly next to the button, because "accept" reading as "begin" is the
 * misunderstanding worth preventing. Accepting files a request; work starts
 * when scope is agreed and a statement of work is signed.
 */
export default function OffersPage() {
  const [offers, setOffers] = useState<ClientOffer[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const body = await portal.offers();
      setOffers(body.offers);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (failed) {
    return (
      <div className="wrap">
        <p className={styles.failed}>
          We could not load your offers just now. Refresh, and if it happens
          again tell us at{" "}
          <a href="mailto:info@genmars.co.ke">info@genmars.co.ke</a>.
        </p>
      </div>
    );
  }

  if (offers === null) {
    return (
      <div className={styles.booting}>
        <LoadingMark size={34} label="Loading your offers" />
      </div>
    );
  }

  const open = offers.filter((o) => o.status === "sent" && !o.expired);
  const closed = offers.filter((o) => o.status !== "sent" || o.expired);

  return (
    <div className="wrap">
      <header className={styles.head}>
        <h1 className={styles.title}>Offers</h1>
        <p className={styles.lede}>
          Prices we have put to you, and what came of them. Nothing here charges
          you or starts work.
        </p>
      </header>

      {error && <p className={styles.error}>{error}</p>}

      {offers.length === 0 && (
        <p className={styles.empty}>
          Nothing right now. If you want us to quote for something,{" "}
          <Link href="/order">tell us what you need</Link>.
        </p>
      )}

      {open.length > 0 && (
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>Waiting on you</h2>
          <ul className={styles.list}>
            {open.map((offer) => (
              <Card
                key={offer.reference}
                offer={offer}
                onDecided={() => void load()}
                onError={setError}
              />
            ))}
          </ul>
        </section>
      )}

      {closed.length > 0 && (
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>Settled</h2>
          <ul className={styles.list}>
            {closed.map((offer) => (
              <Card key={offer.reference} offer={offer} onDecided={() => {}} onError={() => {}} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function Card({
  offer,
  onDecided,
  onError,
}: {
  offer: ClientOffer;
  onDecided: () => void;
  onError: (message: string | null) => void;
}) {
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const live = offer.status === "sent" && !offer.expired;

  async function decide(decision: "accept" | "decline") {
    setBusy(true);
    onError(null);
    try {
      await portal.decideOffer(offer.reference, decision, reason);
      setDeclining(false);
      onDecided();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className={`${styles.card} ${live ? "" : styles.cardSettled}`}>
      <div className={styles.cardHead}>
        {/* The reference opens the full document — the version that gets
            printed and forwarded to whoever actually signs off. */}
        <Link href={`/offers/${offer.reference}`} className={styles.reference}>
          {offer.reference}
        </Link>
        <span className={styles.pill}>
          {offer.expired && offer.status === "sent" ? "Expired" : offer.status_label}
        </span>
      </div>

      <h3 className={styles.offerTitle}>{offer.title}</h3>

      <p className={styles.openDoc}>
        <Link href={`/offers/${offer.reference}`}>Read it in full, or print it</Link>
      </p>

      <p className={styles.amount}>
        KES {group(offer.amount_kes)}
        {/* What it would otherwise be. Shown so the discount is checkable
            rather than asserted. */}
        {offer.discount_kes && offer.list_price_kes && (
          <span className={styles.wasPrice}>
            was KES {group(offer.list_price_kes)}
          </span>
        )}
      </p>

      <div className={styles.detail}>{offer.detail}</div>

      <p className={styles.validity}>
        {live
          ? `Valid until ${day(offer.expires_on)}.`
          : `Expired ${day(offer.expires_on)}.`}
      </p>

      {live && (
        <>
          {declining ? (
            <form
              className={styles.declineForm}
              onSubmit={(e) => {
                e.preventDefault();
                void decide("decline");
              }}
            >
              <label className={styles.label}>
                Anything you can tell us? Optional, and it helps us quote better
                next time.
                <input
                  className={styles.input}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Too much for now, wrong timing, went elsewhere…"
                />
              </label>
              <div className={styles.actions}>
                <button className={styles.secondary} type="submit" disabled={busy}>
                  Decline it
                </button>
                <button
                  type="button"
                  className={styles.linkButton}
                  onClick={() => setDeclining(false)}
                >
                  Cancel
                </button>
              </div>
            </form>
          ) : (
            <>
              <div className={styles.actions}>
                <button
                  type="button"
                  className={styles.primary}
                  disabled={busy}
                  onClick={() => void decide("accept")}
                >
                  {busy ? "Sending" : "Accept this offer"}
                </button>
                <button
                  type="button"
                  className={styles.linkButton}
                  onClick={() => setDeclining(true)}
                >
                  No thanks
                </button>
              </div>
              <p className={styles.note}>
                Accepting sends this back to us as a request. It does not start
                work and nothing is charged &mdash; work begins once scope is
                agreed and a statement of work is signed.
              </p>
            </>
          )}
        </>
      )}
    </li>
  );
}

function group(value: string): string {
  const [whole = "0", cents = "00"] = value.split(".");
  return `${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${cents}`;
}

function day(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("en-KE", {
    day: "numeric", month: "long", year: "numeric",
  });
}
