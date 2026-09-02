"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { portal, type Notification } from "@/lib/api";
import styles from "./Notifications.module.css";

/**
 * The notification bell in the header.
 *
 * ── WHAT A NOTIFICATION IS HERE ─────────────────────────────────────────────
 *
 * A pointer at something already true and already visible somewhere else — an
 * invoice that exists, an order that moved. It is never the only place a fact
 * lives, and never how a client is told something that matters; that is email,
 * or a person. If the whole feed were lost, nothing would be.
 *
 * That is why this is allowed to fail quietly. A feed that cannot load renders
 * as no badge rather than as an error, because an error here would be alarming
 * about something that is, by construction, not important.
 *
 * ── WHY IT DOES NOT POLL ────────────────────────────────────────────────────
 *
 * Fetched once when the shell mounts, and again when the panel is opened. A
 * timer would put a request on every open tab every few seconds for the rest
 * of the working day to make a number change slightly sooner. Opening the
 * panel is the moment the number matters, so that is when it is refreshed.
 */
export function Notifications() {
  const [items, setItems] = useState<Notification[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const panel = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  const load = useCallback(async () => {
    try {
      const body = await portal.notifications();
      setItems(body.notifications);
      setUnread(body.unread);
    } catch {
      // Deliberately silent — see the note above.
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Click-outside and Escape. Without these the panel is a trap on a phone,
  // where there is no obvious place to click that is "not the panel".
  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: MouseEvent | TouchEvent) {
      const target = event.target as Node;
      if (panel.current?.contains(target) || button.current?.contains(target)) {
        return;
      }
      setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next) await load();
  }

  async function markAll() {
    // Optimistic: the count is cosmetic, and waiting for a round-trip to grey
    // out a badge is worse than being briefly wrong about one.
    setUnread(0);
    setItems((current) => current.map((n) => ({ ...n, read: true })));
    try {
      const body = await portal.markRead();
      setUnread(body.unread);
    } catch {
      void load();
    }
  }

  async function open_(item: Notification) {
    setOpen(false);
    if (item.read) return;
    setUnread((n) => Math.max(0, n - 1));
    setItems((current) =>
      current.map((n) => (n.id === item.id ? { ...n, read: true } : n)),
    );
    try {
      await portal.markRead(item.id);
    } catch {
      void load();
    }
  }

  return (
    <div className={styles.wrap}>
      <button
        ref={button}
        type="button"
        className={styles.bell}
        onClick={toggle}
        aria-expanded={open}
        aria-haspopup="true"
        aria-label={
          unread > 0 ? `Notifications, ${unread} unread` : "Notifications"
        }
      >
        <BellMark />
        {unread > 0 && (
          <span className={styles.badge} aria-hidden="true">
            {/* Past a point the exact number stops being useful and starts
                being a wide badge. */}
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div ref={panel} className={styles.panel} role="dialog" aria-label="Notifications">
          <div className={styles.panelHead}>
            <h2 className={styles.panelTitle}>Notifications</h2>
            {unread > 0 && (
              <button type="button" className={styles.markAll} onClick={markAll}>
                Mark all read
              </button>
            )}
          </div>

          {items.length === 0 ? (
            <p className={styles.empty}>Nothing yet.</p>
          ) : (
            <ul className={styles.list}>
              {items.map((item) => (
                <li key={item.id}>
                  <Link
                    href={item.url || "/dashboard"}
                    className={`${styles.item} ${item.read ? "" : styles.unreadItem}`}
                    onClick={() => void open_(item)}
                  >
                    <span className={styles.itemTitle}>
                      {!item.read && <span className={styles.dot} aria-hidden="true" />}
                      {item.title}
                    </span>
                    {item.body && <span className={styles.itemBody}>{item.body}</span>}
                    <time className={styles.itemWhen} dateTime={item.created_at}>
                      {when(item.created_at)}
                    </time>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * "3 days ago", and a real date past a week.
 *
 * Relative time is easier to read for something recent and actively worse for
 * something old: "47 days ago" makes nobody's day clearer than the date does.
 */
function when(iso: string): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";

  const seconds = Math.round((Date.now() - then.getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days <= 7) return `${days}d ago`;

  return then.toLocaleDateString("en-KE", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/** Inline, because one icon is not worth a dependency or a network request. */
function BellMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path
        d="M10 2.5a4.5 4.5 0 0 0-4.5 4.5v2.6L4.2 12.4a.6.6 0 0 0 .55.85h10.5a.6.6 0 0 0 .55-.85L14.5 9.6V7A4.5 4.5 0 0 0 10 2.5Z"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path
        d="M8.1 15.5a2 2 0 0 0 3.8 0"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}
