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

/**
 * An invoice, as the client sees it.
 *
 * `amount_kes` is a STRING. It arrives as one and is parsed only at the last
 * moment for display (see `money`) — never used in arithmetic, never sent
 * anywhere. This is a number somebody pays from a bank account.
 */
export type ClientPayment = {
  method: "mpesa" | "bank" | "cash" | "other";
  method_label: string;
  reference: string;
  amount_kes: string;
  paid_on: string;
};

export type ClientInvoice = {
  number: string;
  description: string;
  amount_kes: string;
  status: "issued" | "paid" | "void";
  status_label: string;
  issued_on: string;
  due_on: string | null;
  overdue: boolean;
  paid_on: string | null;
  /** The reference we matched the payment against, so it can be checked. */
  payment_reference: string;
  void_reason: string;
  /**
   * What has arrived and what is left. An invoice can be settled by several
   * payments — M-Pesa caps a single transfer — so a part-paid invoice shows a
   * real balance rather than looking wholly unpaid.
   *
   * Optional because /orders/<ref> predates them and does not send them.
   */
  amount_paid?: string;
  balance?: string;
  payments?: ClientPayment[];
  /** Null when the invoice was raised straight to the client, with no project. */
  order_reference?: string | null;
};

/**
 * A price Genmars has put to this client.
 *
 * `list_price_kes` is sent on purpose: if we discounted, the client should see
 * what from. A price with no reference point is one they cannot judge, and
 * hiding it would make the discount a sales tactic rather than a fact.
 */
export type ClientOffer = {
  reference: string;
  title: string;
  detail: string;
  tier_name: string;
  amount_kes: string;
  list_price_kes: string | null;
  discount_kes: string | null;
  status: "sent" | "accepted" | "declined" | "withdrawn" | "expired";
  status_label: string;
  expires_on: string;
  expired: boolean;
  sent_at: string | null;
  decided_at: string | null;
};

export type TicketMessage = {
  id: number;
  author_label: string;
  from_staff: boolean;
  mine: boolean;
  body: string;
  created_at: string;
};

/**
 * A support request.
 *
 * Deliberately carries no priority, no assignee and no response-time anything.
 * Priority is Genmars' triage judgement and showing it invites an argument
 * about the label rather than the problem; a visible target would be a
 * commitment however it is worded (Charter 03 §IV).
 */
export type Ticket = {
  reference: string;
  subject: string;
  status: "open" | "waiting" | "answered" | "resolved";
  status_label: string;
  order_reference: string | null;
  created_at: string;
  resolved_at: string | null;
  messages: TicketMessage[];
};

/** Something Genmars is waiting on this client for. */
export type WaitingOnYou = {
  id: number;
  summary: string;
  detail: string;
  order_reference: string;
  order_title: string;
  raised_at: string;
  /** How long it has been sitting. The reason the list is worth reading. */
  waiting_days: number;
};

/**
 * A system Genmars runs for this client.
 *
 * `health` and `checked_at` travel together on purpose: "up" with no timestamp
 * is a claim, "up, checked four minutes ago" is an observation.
 */
export type YourSystem = {
  name: string;
  slug: string;
  purpose: string;
  url: string;
  status: string;
  status_label: string;
  health: "unknown" | "up" | "down" | "degraded";
  health_label: string;
  checked_at: string | null;
};

export type Notification = {
  id: number;
  kind: string;
  title: string;
  body: string;
  url: string;
  created_at: string;
  read: boolean;
};

/**
 * One of the three sizes a service is sold in.
 *
 * `price_kes` is a decimal STRING, like every money value crossing this API,
 * and `is_from` travels with it: the top tier is published as a floor, so
 * rendering the number without "from" states a quote we have not given.
 */
export type ServiceTier = {
  slug: string;
  name: string;
  price_kes: string | null;
  is_from: boolean;
  lead: string;
  includes: string[];
};

export type CatalogueService = {
  slug: string;
  name: string;
  summary: string;
  /** "per month", "one-time", "per session" — how the tier prices are charged. */
  price_unit: string;
  tiers: ServiceTier[];
};

/**
 * An invoice as a printable document.
 *
 * Every field under `biller` and `payment` may be null: settings.py leaves
 * billing identity unconfigured by default and the document OMITS what it has
 * not been told rather than rendering a blank or a guess. An invoice carrying
 * an invented KRA PIN or paybill is not a cosmetic bug.
 */
export type InvoiceDocument = {
  invoice: ClientInvoice;
  billed_to: { organisation: string; contact: string };
  biller: {
    legal_name: string;
    email: string;
    kra_pin: string | null;
    postal_address: string | null;
  };
  payment: {
    mpesa_paybill: string | null;
    mpesa_account: string | null;
    bank_details: string | null;
    terms: string;
    /** False until M-Pesa credentials are configured. Nothing may imply
        otherwise while it is false — see settings.py. */
    stk_available: boolean;
  };
  /**
   * NULL for a direct invoice — a renewal, an afternoon's work, something
   * billed to a past client with no project behind it. The absence is the
   * honest representation and the page renders it as one, rather than showing
   * a project that does not exist in the system.
   */
  order: {
    reference: string;
    title: string;
    contract_reference: string | null;
    contract_signed_on: string | null;
  } | null;
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
  /** Everything ever billed on this order, voids included — see the API. */
  invoices: ClientInvoice[];
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
  /** What they clicked on genmars.co.ke. Slug, and the tier LABEL. Both
      optional — the open "describe your problem" route sends neither. */
  service?: string;
  tier?: string;
};

export const portal = {
  /**
   * Finishes signing up: organisation, contact name, and an enquiry for the
   * commercial partners to qualify. It does NOT create an order — see the
   * note on OnboardingView in portal/views.py.
   */
  /**
   * A NEW enquiry from an account that already exists.
   *
   * Separate from onboarding, which runs once and refuses a second time. See
   * the portal's EnquiryCreateView — without this, a returning client's order
   * was silently discarded.
   */
  enquiry: (payload: {
    problem: string;
    monthly_cost: string;
    timeline: string;
    budget_range: string;
    service?: string;
    tier?: string;
  }) => post<{ id: number; organisation: string }>("/enquiries", payload),

  onboarding: (payload: OnboardingPayload) =>
    post<{ next: string }>("/onboarding", payload),

  orders: () =>
    get<{ orders: OrderSummary[]; has_orders: boolean; has_enquiry: boolean }>(
      "/orders",
    ),
  order: (reference: string) => get<OrderDetail>(`/orders/${reference}`),
  /**
   * One invoice as a document, addressed by number alone.
   *
   * FLAT, not nested under the order. Not every invoice has one — a renewal
   * or an afternoon's work is billed straight to a past client — and those
   * could not be addressed under /orders/<reference>/ at all, which is how a
   * client ended up with a bill their own portal could not open.
   */
  invoice: (number: string) => get<InvoiceDocument>(`/invoices/${number}`),
  /** Starts an M-Pesa prompt. 202 means the prompt was SENT, never that it
      was paid — only the callback decides that, so the page polls. */
  payInvoice: (number: string, phone: string) =>
    post<{ status: string; phone_tail: string; detail: string }>(
      `/invoices/${number}/pay`,
      { phone },
    ),
  paymentStatus: (number: string) =>
    get<{
      invoice_status: "issued" | "paid" | "void";
      paid_on: string | null;
      payment_reference: string;
      attempt: { status: string; result_desc: string; receipt: string } | null;
    }>(`/invoices/${number}/payment-status`),
  /**
   * Every invoice addressed to this client, including ones with no order.
   *
   * The nested /orders/<ref> route cannot see a direct invoice, which is how a
   * client ends up holding a bill their portal says does not exist.
   */
  invoices: () => get<{ invoices: ClientInvoice[] }>("/invoices"),

  notifications: () =>
    get<{ notifications: Notification[]; unread: number }>("/notifications"),

  /** Omit `id` to mark everything read. */
  markRead: (id?: number) =>
    post<{ unread: number }>("/notifications", id === undefined ? {} : { id }),

  catalogue: () => get<{ services: CatalogueService[] }>("/services"),

  dashboard: () =>
    get<{ waiting_on_you: WaitingOnYou[]; systems: YourSystem[] }>("/dashboard"),

  support: () => get<{ tickets: Ticket[] }>("/support"),
  raiseTicket: (body: { subject: string; body: string; order?: string }) =>
    post<Ticket>("/support", body),
  replyToTicket: (reference: string, body: string) =>
    post<Ticket>(`/support/${reference}/reply`, { body }),

  offers: () => get<{ offers: ClientOffer[] }>("/offers"),
  /** Accepting files an enquiry. It does not start work — a signed SOW does. */
  decideOffer: (reference: string, decision: "accept" | "decline", reason?: string) =>
    post<ClientOffer>(`/offers/${reference}/decision`, { decision, reason }),

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
