"use client";

import { useState } from "react";
import { portal } from "@/lib/api";
import styles from "./page.module.css";

/**
 * What Genmars holds about you, on the page rather than in a file.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * IT RENDERS THE PAYLOAD GENERICALLY, AND THAT IS THE WHOLE DESIGN.
 *
 * Nothing here names a field. It walks whatever the export endpoint returns
 * and displays all of it, so this page CANNOT show less than the download
 * does. The day somebody adds a field to `export_payload`, it appears here
 * too, without anyone remembering to come and add it.
 *
 * Hand-written sections would have looked better and been a slow lie: a
 * curated summary sitting next to a "download everything" button is how a
 * client comes to believe the summary is everything. Charter 05 §VIII is about
 * not holding data back, and a page that quietly omits a field is holding it
 * back more effectively than refusing outright, because nobody knows to ask.
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * ── LOADED ON REQUEST, NOT ON MOUNT ────────────────────────────────────────
 *
 * The payload is every order, every note, every invoice. Fetching that to
 * render an account page somebody opened to change their password would be a
 * lot of somebody's data moved for no reason, on a connection that may be
 * metered.
 */
export function MyData() {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function load() {
    setBusy(true);
    setError("");
    try {
      setData(await portal.myData());
    } catch {
      setError(
        "Could not load it just now. The download below does not depend on " +
          "this and should still work.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return (
      <>
        {error ? (
          <p className={styles.dataError} role="alert">
            {error}
          </p>
        ) : null}
        <button
          type="button"
          className="btn btn--ghost"
          disabled={busy}
          onClick={() => void load()}
        >
          {busy ? "Loading…" : "Show me what you hold"}
        </button>
      </>
    );
  }

  return (
    <div className={styles.data}>
      {/*
        Said plainly. Without it somebody reasonably assumes a page beside a
        download button is a preview of it — and the value of this section is
        that it is not a preview, it is the thing.
      */}
      <p className={styles.dataNote}>
        This is exactly what the download contains — the same data, laid out to
        read.
      </p>
      <Node value={data} />
      <button
        type="button"
        className={styles.dataHide}
        onClick={() => setData(null)}
      >
        Hide
      </button>
    </div>
  );
}

/**
 * One value, whatever it is.
 *
 * Recursion rather than a switch over known shapes, for the reason in the
 * banner: this must handle a field nobody has written yet.
 */
function Node({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") {
    // Shown, not skipped. "We hold nothing here" is information, and hiding
    // empty fields would make the page a summary of the non-empty ones.
    return <span className={styles.dataEmpty}>—</span>;
  }

  if (typeof value === "boolean") {
    return <span className={styles.dataValue}>{value ? "Yes" : "No"}</span>;
  }

  if (typeof value !== "object") {
    return <span className={styles.dataValue}>{String(value)}</span>;
  }

  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span className={styles.dataEmpty}>None</span>;
    }
    return (
      <ol className={styles.dataList}>
        {value.map((item, i) => (
          <li key={i} className={styles.dataItem}>
            <Node value={item} />
          </li>
        ))}
      </ol>
    );
  }

  return (
    <dl className={styles.dataPairs}>
      {Object.entries(value as Record<string, unknown>).map(([key, child]) => (
        <div key={key} className={styles.dataPair}>
          <dt className={styles.dataKey}>{label(key)}</dt>
          <dd className={styles.dataDd}>
            <Node value={child} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * `payment_reference` -> `Payment reference`.
 *
 * Mechanical rather than a lookup table. A table would need an entry for every
 * new field, and a field with no entry would render as a raw key — the same
 * drift this component exists to avoid, arriving through the labels instead.
 */
function label(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
