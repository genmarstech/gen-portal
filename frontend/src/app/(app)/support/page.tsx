"use client";

import { useCallback, useEffect, useState } from "react";
import { LoadingMark } from "@/components/LoadingMark";
import { ApiError, portal, type Ticket } from "@/lib/api";
import styles from "./page.module.css";

/**
 * Where a client asks us for something.
 *
 * ── WHAT THIS PAGE DOES NOT SAY ─────────────────────────────────────────────
 *
 * How long a reply will take. Charter 03 §IV forbids putting a commitment in
 * front of a client that has not been tested under real conditions, and
 * "usually within a few hours" is a commitment however gently it is phrased.
 * The honest version is what is written below the form: it reaches a person,
 * and it is not lost.
 *
 * There is also no priority selector. Every one of those ends up with
 * everything marked urgent, which is the same as nothing being urgent — so the
 * client describes what is happening and we decide.
 *
 * ── THE WHOLE THREAD IS HERE ────────────────────────────────────────────────
 *
 * Not a list of requests that must be clicked into one at a time. Support
 * conversations are short and there are rarely many; hiding three replies
 * behind a navigation is friction with nothing on the other side of it.
 */
export default function SupportPage() {
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const body = await portal.support();
      setTickets(body.tickets);
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
          We could not load this just now. You can always email us directly at{" "}
          <a href="mailto:support@genmars.co.ke">support@genmars.co.ke</a>.
        </p>
      </div>
    );
  }

  if (tickets === null) {
    return (
      <div className={styles.booting}>
        <LoadingMark size={34} label="Loading" />
      </div>
    );
  }

  const open = tickets.filter((t) => t.status !== "resolved");
  const done = tickets.filter((t) => t.status === "resolved");

  return (
    <div className="wrap">
      <header className={styles.head}>
        <h1 className={styles.title}>Support</h1>
        <p className={styles.lede}>
          Something not working, or a question about your account. It reaches a
          person here, and everything you have asked stays on this page.
        </p>
      </header>

      {error && <p className={styles.error}>{error}</p>}

      <NewTicket onDone={() => void load()} onError={setError} />

      {tickets.length === 0 && (
        <p className={styles.empty}>
          You have not asked us anything yet. When you do, the conversation
          lives here.
        </p>
      )}

      {open.length > 0 && (
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>Open</h2>
          <ul className={styles.list}>
            {open.map((ticket) => (
              <Thread
                key={ticket.reference}
                ticket={ticket}
                onReplied={() => void load()}
                onError={setError}
              />
            ))}
          </ul>
        </section>
      )}

      {done.length > 0 && (
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>Resolved</h2>
          <ul className={styles.list}>
            {done.map((ticket) => (
              <Thread
                key={ticket.reference}
                ticket={ticket}
                onReplied={() => void load()}
                onError={setError}
              />
            ))}
          </ul>
        </section>
      )}

      <p className={styles.footnote}>
        You can also email{" "}
        <a href="mailto:support@genmars.co.ke">support@genmars.co.ke</a>. Asking
        here keeps it attached to your account, which usually means less
        back-and-forth establishing who you are and what you have.
      </p>
    </div>
  );
}

function NewTicket({
  onDone,
  onError,
}: {
  onDone: () => void;
  onError: (message: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    onError(null);
    try {
      await portal.raiseTicket({ subject, body });
      setSubject("");
      setBody("");
      setOpen(false);
      onDone();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className={styles.primary} onClick={() => setOpen(true)}>
        Ask us something
      </button>
    );
  }

  return (
    <form className={styles.form} onSubmit={submit}>
      <label className={styles.label}>
        What is it about?
        <input
          className={styles.input}
          placeholder="The export is returning an empty file"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          required
        />
      </label>

      <label className={styles.label}>
        What is happening?
        <textarea
          className={styles.textarea}
          rows={5}
          placeholder="What you did, what you expected, and what happened instead. Anything you can tell us about when it started helps."
          value={body}
          onChange={(e) => setBody(e.target.value)}
          required
        />
      </label>

      <div className={styles.actions}>
        <button className={styles.primary} type="submit" disabled={busy}>
          {busy ? "Sending" : "Send it"}
        </button>
        <button
          type="button"
          className={styles.linkButton}
          onClick={() => setOpen(false)}
        >
          Cancel
        </button>
      </div>

      {/*
        No response-time promise. What it says instead is true and is the thing
        somebody actually wants to know: a person will read it, and it will not
        disappear.
      */}
      <p className={styles.assurance}>
        This reaches a person, not a queue that empties itself. You will get an
        email when we reply, and the whole conversation stays on this page.
      </p>
    </form>
  );
}

function Thread({
  ticket,
  onReplied,
  onError,
}: {
  ticket: Ticket;
  onReplied: () => void;
  onError: (message: string | null) => void;
}) {
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(event: React.FormEvent) {
    event.preventDefault();
    if (!reply.trim()) return;
    setBusy(true);
    onError(null);
    try {
      await portal.replyToTicket(ticket.reference, reply);
      setReply("");
      onReplied();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className={styles.ticket}>
      <div className={styles.ticketHead}>
        <span className={styles.reference}>{ticket.reference}</span>
        <span className={styles.pill}>{ticket.status_label}</span>
      </div>

      <h3 className={styles.subject}>{ticket.subject}</h3>
      <p className={styles.asked}>Asked {day(ticket.created_at)}</p>

      <ol className={styles.messages}>
        {ticket.messages.map((message) => (
          <li
            key={message.id}
            className={`${styles.message} ${message.from_staff ? styles.fromUs : ""}`}
          >
            <span className={styles.author}>
              {message.from_staff ? "Genmars" : message.author_label}
              <time className={styles.when} dateTime={message.created_at}>
                {day(message.created_at)}
              </time>
            </span>
            <span className={styles.body}>{message.body}</span>
          </li>
        ))}
      </ol>

      <form className={styles.replyForm} onSubmit={send}>
        <textarea
          className={styles.textarea}
          rows={3}
          placeholder={
            ticket.status === "resolved"
              ? "Still happening? Replying reopens this."
              : "Add anything else"
          }
          value={reply}
          onChange={(e) => setReply(e.target.value)}
          aria-label={`Reply on ${ticket.reference}`}
        />
        <button
          className={styles.secondary}
          type="submit"
          disabled={busy || !reply.trim()}
        >
          {busy ? "Sending" : "Reply"}
        </button>
      </form>
    </li>
  );
}

function day(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("en-KE", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });
}
