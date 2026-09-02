"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { LoadingMark } from "@/components/LoadingMark";
import { portal, type CatalogueService } from "@/lib/api";
import styles from "./page.module.css";

/**
 * The catalogue, inside the portal.
 *
 * ── WHY IT IS HERE AND NOT ONLY ON THE WEBSITE ──────────────────────────────
 *
 * An existing client who wants a second thing had to leave the portal, find
 * the public site, pick a tier and come back through the order flow. They are
 * already signed in; the round trip achieves nothing except giving them
 * several chances to give up.
 *
 * ── WHAT THIS DELIBERATELY DOES NOT DO ──────────────────────────────────────
 *
 * It does not restate prices. The public catalogue on genmars.co.ke carries
 * the tiers and what each one costs, and a second price list is how a client
 * is quoted one number and billed another. So each service links to /order,
 * which is the same door the website's tier buttons go through — one ordering
 * path, not two that can drift.
 */
export default function ServicesPage() {
  const [services, setServices] = useState<CatalogueService[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    portal
      .catalogue()
      .then((body) => {
        if (!cancelled) setServices(body.services);
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
          We could not load the catalogue just now. You can still tell us what
          you need — <Link href="/order">start an order</Link> and describe it.
        </p>
      </div>
    );
  }

  if (services === null) {
    return (
      <div className={styles.booting}>
        <LoadingMark size={34} label="Loading services" />
      </div>
    );
  }

  return (
    <div className="wrap">
      <header className={styles.head}>
        <h1 className={styles.title}>Services</h1>
        <p className={styles.lede}>
          What we build. Pick one to start an order — you are already signed in,
          so we will only ask about the work itself.
        </p>
      </header>

      {services.length === 0 ? (
        <p className={styles.empty}>
          The catalogue is not published yet.{" "}
          <Link href="/order">Tell us what you need</Link> and we will come back
          to you.
        </p>
      ) : (
        <ul className={styles.grid}>
          {services.map((service) => (
            <li key={service.slug} className={styles.card}>
              <h2 className={styles.name}>{service.name}</h2>
              <p className={styles.summary}>{service.summary}</p>
              <Link
                className={styles.order}
                href={`/order?service=${encodeURIComponent(service.slug)}`}
              >
                Order this
                <span aria-hidden="true"> →</span>
              </Link>
            </li>
          ))}
        </ul>
      )}

      <p className={styles.footnote}>
        Prices and what each tier includes are on{" "}
        <a href="https://genmars.co.ke/services/" target="_blank" rel="noreferrer">
          genmars.co.ke/services
        </a>
        . We keep one price list rather than two.
      </p>
    </div>
  );
}
