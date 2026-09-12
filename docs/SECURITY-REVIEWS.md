# Where the security reviews live

**Not in this repository.** `gen-portal` is public (GPL-3.0), and a security
review is an itemised list of this system's weaknesses with severities, plus
which of them are not fixed yet. That is a useful document for us and a map for
anybody else.

They live in **`internals-tm/docs/`**, which is a private repository.

---

## Why this file exists rather than nothing

The audit of 2026-09-09 was written straight into `gen-portal/docs/` and pushed,
without anybody checking that this repo is public. It was readable at
`raw.githubusercontent.com` for three days, including the entries still marked
open.

A silent deletion would leave the next person to run a review making the same
choice for the same reason — the obvious place to put a document about this
codebase is next to this codebase.

## What is safe to keep here

The distinction is not "security" versus "not security". It is whether the
document tells a stranger something they could not already work out:

| Safe here | Not safe here |
|---|---|
| How deployment works, what the gates are | A list of controls that are missing or unenforced |
| That backups are GPG-encrypted, and the key ID | Anything about where a control is weak *today* |
| The sign-on protocol and its invariants | Severities, exploitability, "still open" |
| Configuration with the reasoning behind it | Findings before they are fixed |

`docs/DEPLOYMENT.md`, `docs/SIBLING-SIGN-ON.md` and the rest stay. They describe
how the system is built, which is exactly what an open repository is for, and
they were written knowing they are public.

## If a review has already been pushed here

Removing the file stops it being found. It does **not** unpublish it: the blob
stays in this repository's history, in every clone, and in anything that
mirrored it. Treat everything it named as known, prioritise closing whatever was
still open, and do not rely on the deletion.
