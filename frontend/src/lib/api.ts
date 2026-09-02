/**
 * API client.
 *
 * The backend is same-origin in production — the host Caddy routes /api/* to
 * Django — so requests are relative, cookies ride along, and there is no CORS.
 *
 * Session cookies, not tokens. `credentials: "same-origin"` is what carries the
 * session; nothing here reads or stores a credential, because a credential
 * JavaScript can read is one an XSS can take.
 *
 * ── STATUS ──────────────────────────────────────────────────────────────────
 * Every endpoint below is implemented in the Django app: `accounts/urls.py`
 * for /auth/*, `portal/urls.py` for /orders and /account/export. This file and
 * those two URL modules are one contract — a path changed in one is a 404 in
 * the other, so change them together.
 *
 * `NEXT_PUBLIC_AUTH_MOCK=1` still short-circuits the auth POSTs so the screens
 * can be worked on without a running backend. It does NOT mock the dashboard:
 * a fake order list is a fake set of promises to a client, and it would be too
 * easy to screenshot one and believe it.
 */

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly field?: string,
  ) {
    super(message);
  }
}

const MOCK = process.env.NEXT_PUBLIC_AUTH_MOCK === "1";

/**
 * Django wants the CSRF token echoed from its cookie on unsafe methods.
 *
 * Read FRESH on every request, never cached. Django rotates the CSRF token
 * whenever a session starts, so a token captured once goes stale the moment
 * someone signs in and every subsequent POST fails with a 403. Reading
 * document.cookie each time picks the new one up automatically.
 */
function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)gm_csrftoken=([^;]+)/);
  return match?.[1] ?? "";
}

/**
 * Fetch the session and, importantly, get Django to set the CSRF cookie.
 * Call this once on load: without the cookie the first POST cannot be made.
 */
export async function session(): Promise<{
  authenticated: boolean;
  email?: string;
  full_name?: string;
  email_verified?: boolean;
  /**
   * Verified, but no organisation yet — signup is not finished.
   *
   * Distinct from "has no orders". Both render an empty dashboard, but one
   * needs a form and the other needs patience, and the server is the only
   * place that can tell them apart.
   */
  needs_onboarding?: boolean;
}> {
  if (MOCK) return { authenticated: false };
  const res = await fetch("/api/auth/session", { credentials: "same-origin" });
  return res.json();
}

export async function signOut(): Promise<void> {
  if (MOCK) return;
  await fetch("/api/auth/session", {
    method: "DELETE",
    credentials: "same-origin",
    headers: { "X-CSRFToken": csrfToken() },
  });
}

async function post<T>(path: string, body: unknown): Promise<T> {
  if (MOCK) return mock<T>(path, body);

  const res = await fetch(`/api${path}`, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": csrfToken(),
    },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    // The API returns one safe message. The UI must not try to be more helpful
    // than that: distinguishing "no such account" from "wrong password" hands
    // an attacker a free account-enumeration oracle.
    const data = await res.json().catch(() => ({}));
    throw new ApiError(
      data.detail ?? "Something went wrong. Try again.",
      res.status,
      data.field,
    );
  }
  return res.json();
}

/* ── dashboard types ──────────────────────────────────────────────────────── */

export type OrderSummary = {
  reference: string;
  title: string;
  status: string;
  status_label: string;
  target_date: string | null;
};

export type ProgressNote = {
  week_of: string;
  body: string;
  author: string;
  published_at: string | null;
};

export type Milestone = {
  name: string;
  /** A STRING, not a number. Money through a float is money you cannot reconcile. */
  amount_kes: string;
  due_on: string | null;
  status: "pending" | "invoiced" | "paid";
  status_label: string;
};

/**
 * The statement of work in force, as the client sees it.
 *
 * Every value is a SNAPSHOT taken when the contract was issued — deliberately
 * NOT the order's live scope. If the two disagree, the contract is what was
 * agreed and the order is where the work has since moved to; showing the live
 * scope inside a document panel would tell the client they signed something
 * they did not.
 */
export type ClientContract = {
  reference: string;
  version: number;
  title: string;
  scope: string;
  exclusions: string;
  deliverables: string;
  deliverable_list: string[];
  /** A STRING. This is the figure on a document they signed. */
  total_kes: string;
  payment_terms: string;
  target_date: string | null;
  issued_at: string | null;
  signed_on: string | null;
  signed_by_name: string;
};

export type OrderDetail = OrderSummary & {
  organisation: string;
  scope: string;
  exclusions: string;
  contact: { full_name: string; email: string };
  started_on: string | null;
  notes: ProgressNote[];
  milestones: Milestone[];
  /** Null while an order is still being scoped — an ordinary state, not an error. */
  contract: ClientContract | null;
};

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`, { credentials: "same-origin" });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new ApiError(data.detail ?? "Could not load that.", res.status);
  }
  return res.json();
}

export type OnboardingPayload = {
  full_name: string;
  organisation_name: string;
  problem: string;
  monthly_cost: string;
  timeline: string;
  budget_range: string;
};

export const portal = {
  /**
   * Finishes signing up: organisation, contact name, and an enquiry for the
   * commercial partners to qualify. It does NOT create an order — see the
   * note on OnboardingView in portal/views.py.
   */
  onboarding: (payload: OnboardingPayload) =>
    post<{ next: string }>("/onboarding", payload),

  orders: () =>
    get<{ orders: OrderSummary[]; has_orders: boolean; has_enquiry: boolean }>(
      "/orders",
    ),
  order: (reference: string) => get<OrderDetail>(`/orders/${reference}`),
  /** Charter 05 §VIII — a plain link, so the browser downloads it. */
  exportUrl: "/api/account/export",
};

export const auth = {
  signIn: (email: string, password: string) =>
    post<{ next: string }>("/auth/sign-in", { email, password }),

  signUp: (email: string, password: string, full_name: string) =>
    post<{ next: string }>("/auth/sign-up", { email, password, full_name }),

  /** Always resolves — whether the address exists is not public. */
  requestCode: (email: string) =>
    post<{ ok: true }>("/auth/request-code", { email }),

  verify: (email: string, code: string) =>
    post<{ next: string }>("/auth/verify", { email, code }),

  forgot: (email: string) => post<{ ok: true }>("/auth/forgot", { email }),

  reset: (email: string, code: string, password: string) =>
    post<{ next: string }>("/auth/reset", { email, code, password }),

  /**
   * Set the password on an account Genmars created. The account exists but is
   * unusable until this succeeds — staff never hold a client credential.
   */
  acceptInvite: (email: string, code: string, password: string) =>
    post<{ next: string }>("/auth/accept-invite", { email, code, password }),

  changePassword: (current_password: string, new_password: string) =>
    post<{ ok: true }>("/auth/change-password", { current_password, new_password }),
};

/* ── mock ─────────────────────────────────────────────────────────────────── */

async function mock<T>(path: string, body: unknown): Promise<T> {
  await new Promise((r) => setTimeout(r, 700));
  // noUncheckedIndexedAccess is on, so index reads are string | undefined.
  const b = (body ?? {}) as Record<string, string | undefined>;

  if (path === "/auth/sign-in") {
    if (b.password === "locked")
      throw new ApiError(
        "Too many failed attempts. Try again in a few minutes — the lock clears on its own.",
        423,
      );
    if (b.password !== "correct-horse-battery")
      throw new ApiError("That email address and password do not match.", 401);
    return { next: "/dashboard" } as T;
  }

  if (path === "/auth/verify" || path === "/auth/reset") {
    if (b.code !== "123456")
      throw new ApiError("That code is not right, or it has expired.", 400, "code");
    return { next: "/dashboard" } as T;
  }

  if (path === "/auth/sign-up") {
    return { next: `/verify?email=${encodeURIComponent(b.email ?? "")}` } as T;
  }

  return { ok: true } as T;
}
