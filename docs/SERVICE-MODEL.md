# Service model v2.0 — what it means for the portal

Two internal documents were added on **2026-09-04**: the Service System Model
v2.0 and the Product, Infrastructure & Portfolio Strategy v2.0. Both are marked
confidential and stay in the company folder.

**This repository is public.** What follows is only the structure the portal has
to model. Pricing rules, qualification criteria, revenue design, guardrails and
capacity constraints are deliberately excluded and must not be added.

> Written against the portal as it actually stands, not as it was planned. The
> portal is no longer a read-only client dashboard — `Service`, `ServiceTier`,
> `Offer`, `Contract`, `Invoice`, `Task`, `Incident`, `System` and an entire
> `operations` app now exist. Most of v2.0 is therefore already built. What is
> left is a short list.

---

## 1. The open question is closed

An earlier draft of this document asked whether the portal was a thin
client-facing window onto a system of record the Business OS would own, or the
seed of that system growing into it.

**It grew into it.** `operations/` holds approvals, exports, permissions,
search, selectors and services; `portal/models.py` carries contracts, invoices,
M-Pesa payments, tasks, incidents and activity logs. That is the Business OS
Work, Customers and Finance modules under a different name.

The consequence is not academic: the portal now carries **staff-facing
features**, so the rule "keep it thin and read-only" no longer applies and
should not be quoted at it. What does apply is the strategy's extraction rule —
build a capability when it is genuinely needed, and extract it to a platform
only after a second consumer has needed it too.

---

## 2. The nine-stage lifecycle

The portal covers stages 5 to 9. Stages 1 to 4 happen before an account has
anything to show, which is why signing up creates an **account and not an
order**.

| Stage | Output and exit condition | In the portal |
|---|---|---|
| 1 Lead | Qualified opportunity | `Enquiry` |
| 2 Discovery | Discovery brief — paid, delivered, reviewed | Not modelled |
| 3 Solution design | Technical proposal, exclusions explicit | `Offer`, `Service.default_scope` / `default_exclusions` |
| 4 Proposal | Signed agreement, payment schedule, change process | `Contract` — a snapshot, correctly |
| 5 Build | Working software against real data | `Order.Status`, `ProgressNote`, `Task` |
| 6 Acceptance | Written acceptance, item by item | Not modelled |
| 7 Launch | Production release; client holds credentials and docs | `Order.Status`, `System` |
| 8 Support | Active support plan, or a recorded decision that there is none | `Incident`, managed-service tiers |
| 9 Expansion | Scoped as its own engagement | A new `Order` |

`Contract` deserves the credit it already documents itself for: issuing
**copies** the wording and the money as they stood. A view that re-renders the
order would mean the document a client signed says something they never agreed
to. That is exactly what stage 4 requires.

**Handover is a deliverable.** Stage 7 is not complete when the system is live —
it is complete when the client holds their own credentials, documentation and
deployment records, even where Genmars continues to operate it under a support
plan. `System` and `SystemKey` are close to this; whether the client can *see*
the handover state is worth checking.

---

## 3. Change request classification — BUILT 2026-09-05

`ChangeRequest` now exists, with the four classifications below enforced in
`operations/services.py` rather than in a view. What follows is why it is
shaped the way it is; `portal/tests/test_change_requests.py` is the proof.

It also closed a live defect rather than only filling a gap. The client-facing
"ask for a change to this work" form already existed on the order page and
raised a **support ticket** — so a scope change landed in the queue beside "my
password does not work", unclassified, unpriced and unapproved.

Every new request is classified **before any work is done on it**:

| Classification | Definition | Handling |
|---|---|---|
| **Included scope** | Already covered by the signed proposal | Proceed, no commercial change |
| **Clarification** | The same requirement, better understood | Proceed; record in writing so it is not later remembered as an addition |
| **Defect** | Agreed scope not working as specified | Fix at no charge; record the cause |
| **Change request** | New or materially altered requirement | Document impact on cost, timeline and risk; approval before work proceeds |

Most disputes are not about whether a change costs money. They are about **when
it was raised** — before the work or after. Classifying at the point of request
removes the argument entirely.

The dangerous ones are small. A large request is obviously a change request;
twenty small ones get absorbed silently until the project is weeks over and
nobody can point to why.

A client can raise one themselves, which is the point: Charter 05 §I protects
both sides, and a change process only Genmars can start lets a request be heard
informally, absorbed, and disputed later with no record of when it was asked
for.

`raised_at` is set once and no code path edits it. `Milestone` anticipated that
a change request may move a target date; `decide_change_request` is what
actually moves it, at approval — Charter 05 §II, the revised date stated at
approval rather than discovered later.

---

## 4. Still not modelled

Listed so the shape is known — **not** as a backlog. The extraction rule applies
to features too: build it when it is genuinely needed.

| Concept | Note |
|---|---|
| Discovery brief | A paid deliverable the client owns whether or not they proceed |
| Written acceptance | Against the proposal, item by item — stage 6 |
| Recorded "no support plan" decision | Where no plan is bought, that decision is written down, so expectations after an incident are unambiguous. An order with no plan should say so, not show nothing |

---

## 5. Support plans and response times

Two constraints that bind the code:

- **Response times are stated in business hours, with the hours defined and the
  timezone named**, and response is distinguished from resolution. If the portal
  displays a commitment it must display all three, or it reads as a resolution
  guarantee — which is rarely possible and rarely necessary.
- Coverage is capped against **reserved capacity**, not sold freely. Selling more
  than can be staffed converts recurring revenue into recurring failure.

---

## 6. Prices live on the website, and this is a copy

`ServiceTier` already documents this and it is worth restating, because it is
the kind of thing that drifts: **genmars.co.ke/services is the price list.**
These rows exist so a signed-in client can pick a tier without leaving the
portal — not so the portal can have prices of its own.

Two price lists is how a client is quoted one number and billed another. When a
price changes on the website, `seed_services --force` is re-run.

The naming conflict is **settled as of 2026-09-05: the shipping per-offer names
stand** (`Essential Setup`, `Basic`, `Care`, …), and v2.0 §03 is amended to
match rather than departed from silently. `Foundation / Growth / Scale` appears
in no client-facing copy. Reasoning is in `gen-website/docs/SERVICE-MODEL.md`
§3.

The standing rule survives the decision and matters more than it did: **do not
rename tiers here first.** The website is authoritative and the portal copies
it. A rename that started in the portal would give a client one number on the
site and a different one at checkout.

---

## 7. Delivery standards

v2.0 §08's engineering floor is the same as Charter 03 §IV Tier 1: source
control, separated environments, testing, deliberate auth and secrets handling,
documentation, repeatable deployment with a rollback procedure, observability,
and backups with restoration **actually tested, not merely configured**.

A Foundation-tier build applies these proportionately — smaller test surface,
simpler environments — but never omits a category. "We skipped backups to hit
the price" is not a smaller version of the standard; it is a different one.

Current status against that floor lives in `docs/PRE-LAUNCH.md`, which is
reconciled against what actually shipped. Read it there rather than trusting a
second copy here that will go stale.

---

**Related:** `docs/DEPLOYMENT.md` · `docs/PRE-LAUNCH.md` ·
`../gen-website/docs/SERVICE-MODEL.md` · `../gen-website/docs/PORTAL-INTEGRATION.md`
