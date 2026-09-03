"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { LoadingMark } from "@/components/LoadingMark";

/**
 * The old address for an invoice document, kept as a redirect.
 *
 * The document moved to /invoices/<number> because an invoice number
 * identifies an invoice on its own, and nesting it under an order left every
 * DIRECT invoice — a renewal, an afternoon's work — with no address at all.
 *
 * This stays because links do not stop existing when a route does. This one is
 * in browser histories and bookmarks, and it is the shape of link that ends up
 * pasted into an email thread with a client's accounts department. A dead URL
 * where an invoice used to be reads as a bill being withdrawn, which is a
 * specific and alarming thing to imply by accident.
 *
 * `replace` rather than `push`: the old address should not sit in the history
 * for Back to land on and bounce off again.
 */
export default function LegacyInvoiceRedirect() {
  const params = useParams<{ number: string }>();
  const router = useRouter();

  useEffect(() => {
    if (params.number) router.replace(`/invoices/${params.number}`);
  }, [params.number, router]);

  return (
    <div style={{ display: "grid", placeItems: "center", minHeight: "40vh" }}>
      <LoadingMark size={32} label="Opening this invoice" />
    </div>
  );
}
