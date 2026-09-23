"use client";

import { useId, useRef, useState } from "react";
import { LoadingMark } from "../LoadingMark";
import styles from "./Form.module.css";

/**
 * Auth form primitives.
 *
 * Measurements come from the design artboards: 52px control height, 2px radius,
 * 11px uppercase labels at +180 tracking, 18px between fields.
 *
 * Everything here is a real form control. The designs draw inputs as static
 * divs — that is fine for an artboard and unacceptable in an application: a
 * screen reader must announce a label, a password manager must find a field,
 * and a keyboard must reach every control.
 */

export function Fields({ children }: { children: React.ReactNode }) {
  return <div className={styles.fields}>{children}</div>;
}

export function Field({
  label,
  hint,
  error,
  action,
  ...props
}: {
  label: string;
  hint?: string;
  error?: string;
  action?: React.ReactNode;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;

  return (
    <div className={styles.field}>
      <div className={styles.labelRow}>
        <label className={styles.label} htmlFor={id}>
          {label}
        </label>
        {action}
      </div>

      <input
        id={id}
        className={`${styles.input} ${error ? styles.inputError : ""}`}
        aria-invalid={error ? true : undefined}
        aria-describedby={[errorId, hintId].filter(Boolean).join(" ") || undefined}
        {...props}
      />

      {error ? (
        <p id={errorId} className={styles.error} role="alert">
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className={styles.hint}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Password field with a reveal toggle.
 *
 * The toggle is a real button, not the design's static "Show" text — it must be
 * reachable by keyboard and announce its state. `aria-pressed` carries that.
 */
/**
 * Multi-line text.
 *
 * Shares Field's label/hint/error structure rather than reimplementing it —
 * the aria wiring between a control, its hint and its error is exactly the
 * thing that rots when it exists in two places.
 */
export function TextareaField({
  label,
  hint,
  error,
  rows = 5,
  ...props
}: {
  label: string;
  hint?: string;
  error?: string;
} & React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;

  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={id}>
        {label}
      </label>

      <textarea
        id={id}
        rows={rows}
        className={`${styles.input} ${styles.textarea} ${error ? styles.inputError : ""}`}
        aria-invalid={error ? true : undefined}
        aria-describedby={[errorId, hintId].filter(Boolean).join(" ") || undefined}
        {...props}
      />

      {error ? (
        <p id={errorId} className={styles.error} role="alert">
          {error}
        </p>
      ) : hint ? (
        <p id={hintId} className={styles.hint}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}

/**
 * A short list of options, one choice.
 *
 * Radios rather than a <select>: with five or fewer options a select hides the
 * choices behind a tap and gives back a control every platform styles its own
 * way. These are real radio inputs — one tab stop, arrow keys between options —
 * with the input visually hidden and the label carrying the appearance.
 *
 * Every set here includes an out ("Not sure yet"). A required question with no
 * honest answer produces a dishonest one, and these answers feed qualification.
 */
export function ChoiceField({
  label,
  name,
  options,
  value,
  onChange,
  hint,
}: {
  label: string;
  name: string;
  options: readonly string[];
  value: string;
  onChange: (value: string) => void;
  hint?: string;
}) {
  const id = useId();
  return (
    <fieldset className={styles.choiceSet}>
      <legend className={styles.label}>{label}</legend>
      <div className={styles.choices}>
        {options.map((option) => (
          <label key={option} className={styles.choice}>
            <input
              type="radio"
              name={`${name}-${id}`}
              value={option}
              checked={value === option}
              onChange={() => onChange(option)}
              className={styles.choiceInput}
            />
            <span className={styles.choiceLabel}>{option}</span>
          </label>
        ))}
      </div>
      {hint ? <p className={styles.hint}>{hint}</p> : null}
    </fieldset>
  );
}


/**
 * Several answers, not one.
 *
 * Deliberately the same markup and the same classes as ChoiceField — the
 * hidden input with a styled label — so a question that takes many answers
 * looks like a question that takes one, and nobody has to learn a second
 * control. The only real difference is `type` and that the state is a set.
 *
 * ── WHY THIS EXISTS AT ALL ────────────────────────────────────────────────
 * Onboarding asked for a written paragraph before it would finish an account,
 * and people stopped there — not because they had no problem, but because
 * describing it in prose is work, and it was work standing between them and
 * the thing they came for. Ticking is not.
 *
 * The free-text box is still there for anyone who wants it. It is no longer
 * the toll gate.
 */
export function MultiChoiceField({
  label,
  options,
  values,
  onChange,
  hint,
}: {
  label: string;
  options: readonly string[];
  values: readonly string[];
  onChange: (values: string[]) => void;
  hint?: string;
}) {
  function toggle(option: string) {
    onChange(
      values.includes(option)
        ? values.filter((v) => v !== option)
        : [...values, option],
    );
  }

  return (
    <fieldset className={styles.choiceSet}>
      <legend className={styles.label}>{label}</legend>
      <div className={styles.choices}>
        {options.map((option) => (
          <label key={option} className={styles.choice}>
            <input
              type="checkbox"
              value={option}
              checked={values.includes(option)}
              onChange={() => toggle(option)}
              className={styles.choiceInput}
            />
            <span className={styles.choiceLabel}>{option}</span>
          </label>
        ))}
      </div>
      {hint ? <p className={styles.hint}>{hint}</p> : null}
    </fieldset>
  );
}


export function PasswordField({
  label = "Password",
  hint,
  error,
  autoComplete = "current-password",
  ...props
}: {
  label?: string;
  hint?: string;
  error?: string;
} & React.InputHTMLAttributes<HTMLInputElement>) {
  const [shown, setShown] = useState(false);

  return (
    <Field
      label={label}
      hint={hint}
      error={error}
      type={shown ? "text" : "password"}
      autoComplete={autoComplete}
      action={
        <button
          type="button"
          className={styles.reveal}
          aria-pressed={shown}
          onClick={() => setShown((v) => !v)}
        >
          {shown ? "Hide" : "Show"}
        </button>
      }
      {...props}
    />
  );
}

/**
 * Six-digit code entry.
 *
 * One box per digit, but backed by a single value. Paste is handled explicitly
 * because pasting a code out of an email is how most people will use this, and
 * per-box inputs break paste by default — the most common way this pattern is
 * got wrong.
 *
 * inputMode="numeric" brings up the number pad; autoComplete="one-time-code"
 * lets the browser offer the code it saw arrive.
 */
export function CodeInput({
  length = 6,
  value,
  onChange,
  error,
}: {
  length?: number;
  value: string;
  onChange: (v: string) => void;
  error?: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  const id = useId();
  const digits = value.padEnd(length).slice(0, length).split("");

  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={id}>
        Verification code
      </label>

      <div
        className={styles.code}
        onClick={() => ref.current?.focus()}
        data-error={error ? "" : undefined}
      >
        {digits.map((d, i) => (
          <span
            key={i}
            className={`${styles.codeCell} ${
              i === value.length ? styles.codeCellActive : ""
            }`}
            aria-hidden="true"
          >
            {d.trim()}
          </span>
        ))}

        {/* The real input: one field, visually hidden, carrying the whole value. */}
        <input
          ref={ref}
          id={id}
          className={styles.codeInput}
          value={value}
          onChange={(e) =>
            onChange(e.target.value.replace(/\D/g, "").slice(0, length))
          }
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={length}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : undefined}
        />
      </div>

      {error ? (
        <p id={`${id}-error`} className={styles.error} role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

export function Submit({
  children,
  pending,
  ...props
}: { pending?: boolean } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="submit"
      className={styles.submit}
      disabled={pending || props.disabled}
      {...props}
    >
      {pending ? (
        <>
          <LoadingMark size={16} label={null} />
          Working
        </>
      ) : (
        children
      )}
    </button>
  );
}

export function Secondary({
  children,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button type="button" className={styles.secondary} {...props}>
      {children}
    </button>
  );
}

/**
 * A navigation dressed as a secondary button.
 *
 * An anchor and not a button, deliberately. Starting an OAuth flow IS a
 * top-level navigation — the person leaves for Google and comes back — and a
 * button with an onClick that assigns location would break middle-click, open
 * in new tab, and the status bar that tells somebody where they are about to
 * be sent, all to look the same.
 */
export function SecondaryLink({
  children,
  icon,
  ...props
}: React.AnchorHTMLAttributes<HTMLAnchorElement> & {
  icon?: React.ReactNode;
}) {
  return (
    <a className={styles.secondaryLink} {...props}>
      {icon ? (
        <span className={styles.linkIcon} aria-hidden="true">
          {icon}
        </span>
      ) : null}
      <span>{children}</span>
    </a>
  );
}

/**
 * Google's G, inline.
 *
 * ── INLINE, NOT A URL, AND NOT A FONT ─────────────────────────────────────
 * Four paths cost less than a request, and the mark then renders before first
 * paint rather than popping in after it. It also keeps img-src and script-src
 * exactly as they are: a hosted asset or Google's own button script would
 * mean widening the CSP on the one origin that serves an authenticated
 * application, to draw a logo.
 *
 * ⚠ THE COLOURS ARE GOOGLE'S AND ARE NOT THEME TOKENS. That is deliberate and
 *   it is why check-theme-tokens does not see them — a brand mark recoloured
 *   to fit our palette is no longer their brand mark, and their guidelines do
 *   not allow it. It is drawn identically in light and dark, which is what
 *   they ask for, and the white field it needs comes from the button rather
 *   than from the icon.
 */
export function GoogleMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 48 48" role="presentation">
      <path
        fill="#EA4335"
        d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"
      />
      <path
        fill="#4285F4"
        d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"
      />
      <path
        fill="#FBBC05"
        d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"
      />
      <path
        fill="#34A853"
        d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"
      />
    </svg>
  );
}

export function Divider({ children = "or" }: { children?: string }) {
  return (
    <div className={styles.divider}>
      <span className={styles.rule} />
      <span className={styles.dividerLabel}>{children}</span>
      <span className={styles.rule} />
    </div>
  );
}

/**
 * Form-level failure.
 *
 * `role="alert"` so it is announced. The message must never distinguish an
 * unknown address from a wrong password — the API returns one message for both
 * and the UI must not reconstruct the difference.
 */
export function FormError({ children }: { children?: React.ReactNode }) {
  if (!children) return null;
  return (
    <p className={styles.formError} role="alert">
      {children}
    </p>
  );
}
