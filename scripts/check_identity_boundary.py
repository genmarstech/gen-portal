#!/usr/bin/env python3
"""
Enforce the identity boundary.

═══════════════════════════════════════════════════════════════════════════════
backend/accounts/identity.py has claimed since it was written that "a
`scripts/check_identity_boundary.py` check enforces it in CI".

It did not exist. The boundary was held by whoever last read the docstring,
which is not enforcement — it is a comment describing a rule nobody was
checking, and the two violations this script found on the day it was written
(operations/services.py creating accounts with User.objects.create_user) are
what that costs. This file makes the sentence true.
═══════════════════════════════════════════════════════════════════════════════

── WHAT THE BOUNDARY IS FOR ────────────────────────────────────────────────────

Every authentication operation goes through one module so that the planned move
to a separate identity service is a day's work rather than a rewrite. When it
lands, identity.py becomes an HTTP client and nothing else changes — but only if
nothing else ever learned to hash a password, mint a code, or make a user.

The failure mode is slow and quiet. One view calls create_user "just this once",
a service copies it, and eighteen months later authentication lives in thirty
files, each with its own idea of what a locked account or an unverified address
means. Nobody decides to do that. It is what happens when nothing objects.

── WHAT IT CHECKS ──────────────────────────────────────────────────────────────

Four things, by parsing the syntax tree rather than grepping text — a grep
cannot tell `EmailCode.Purpose.VERIFY` (a constant, fine) from
`EmailCode.objects.create(...)` (minting a credential, not fine), and a check
that cries wolf on the first is one people learn to route around.

  1. Making users            create_user, create_superuser
  2. Handling passwords      check_password, make_password, set_password,
                             set_unusable_password
  3. Django's authenticate   imported from django.contrib.auth, or called on it
  4. Touching EmailCode      .objects on the model — the ORM, not the enum

── WHAT IT DOES NOT CHECK, AND WHY THAT IS HONEST ──────────────────────────────

It is a syntax check, not a proof. `getattr(User.objects, "create_" + kind)`
would pass, as would anything reached through a variable this script cannot
follow. It stops the accident, which is the failure that actually happens; it
does not stop somebody determined to route around it, and nothing at this level
could.

Nor does it check that identity.py is any *good* — only that it is the one door.

── ADDING TO THE ALLOWLIST ─────────────────────────────────────────────────────

Every entry needs a reason in the table below, and the reason has to be that the
code is NOT user authentication. "It was easier" is how the boundary dies. If
the answer is "this genuinely is an auth operation", the fix is a function in
identity.py — that is what the module is for.

Run it directly:

    python scripts/check_identity_boundary.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"

# Directories with nothing to enforce: generated code, the test suite (which
# must be free to construct users directly — a test that could only build
# fixtures through the boundary would be testing the boundary), and installed
# packages.
SKIP_DIRS = {".venv", "migrations", "tests", "__pycache__", "node_modules", "staticfiles"}

CREATE_USER = {"create_user", "create_superuser"}
PASSWORD_FUNCTIONS = {
    "check_password",
    "make_password",
    "set_password",
    "set_unusable_password",
}

# ── The allowlist ───────────────────────────────────────────────────────────
#
# Path relative to backend/, mapped to why it is not a breach of the boundary.
# Read every one of these before adding the next.
ALLOWED: dict[str, str] = {
    "accounts/identity.py": (
        "The boundary itself. This is the module every other file is being "
        "kept away from these calls in favour of."
    ),
    "accounts/models.py": (
        "Where User, its manager and EmailCode are DEFINED. create_user and "
        "set_password have to exist somewhere, and the definition site is not "
        "a caller — identity.py is still the only thing that invokes them."
    ),
    "portal/system_api.py": (
        "SystemKey tokens, which are not user authentication. A machine "
        "presenting a key is a different question from a person presenting a "
        "password: no account, no session, no lockout, no email, and nothing "
        "for an identity service to take over. It uses the same Argon2 hashers "
        "because hashing a secret is hashing a secret. THE PARENT WATCHES; IT "
        "DOES NOT REACH IN — see the System registry in portal/models.py."
    ),
}


class Visitor(ast.NodeVisitor):
    def __init__(self, relative: str) -> None:
        self.relative = relative
        self.found: list[tuple[int, str, str]] = []

    def _flag(self, node: ast.AST, what: str, rule: str) -> None:
        self.found.append((node.lineno, what, rule))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "django.contrib.auth":
            for alias in node.names:
                if alias.name == "authenticate":
                    self._flag(
                        node,
                        "from django.contrib.auth import authenticate",
                        "Django's authenticate() bypasses the lockout, the "
                        "email-verification state and the uniform failure "
                        "message. Use identity.authenticate().",
                    )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = None
        if isinstance(node.func, ast.Attribute):
            name = node.func.attr
        elif isinstance(node.func, ast.Name):
            name = node.func.id

        if name in CREATE_USER:
            self._flag(
                node,
                f"{name}(...)",
                "Creating an account is an authentication operation. Use "
                "identity.create_account() or identity.create_invited_account().",
            )
        elif name in PASSWORD_FUNCTIONS:
            self._flag(
                node,
                f"{name}(...)",
                "Password hashing belongs to identity.py, which is what "
                "becomes an HTTP call when identity moves out.",
            )
        elif name == "authenticate" and isinstance(node.func, ast.Attribute):
            # `auth.authenticate(...)` — a bare authenticate() is far more
            # likely to be a local function (portal/system_api.py defines one
            # for SystemKey tokens) than Django's.
            base = node.func.value
            if isinstance(base, ast.Name) and base.id in {"auth", "django"}:
                self._flag(
                    node,
                    "auth.authenticate(...)",
                    "Use identity.authenticate().",
                )

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # EmailCode.objects — the ORM. EmailCode.Purpose.VERIFY and
        # EmailCode.LIFETIME are constants and are deliberately not flagged:
        # a caller naming the purpose it wants a code for is the boundary
        # working, not a breach of it.
        if (
            node.attr == "objects"
            and isinstance(node.value, ast.Name)
            and node.value.id == "EmailCode"
        ):
            self._flag(
                node,
                "EmailCode.objects",
                "A verification code IS a credential. Minting, reading or "
                "expiring one goes through identity.issue_code() / "
                "redeem_code().",
            )
        self.generic_visit(node)


def python_files() -> list[Path]:
    return sorted(
        path
        for path in BACKEND.rglob("*.py")
        if not SKIP_DIRS & set(path.relative_to(BACKEND).parts)
    )


def main() -> int:
    if not BACKEND.is_dir():
        print(f"FATAL: no backend/ directory at {BACKEND}", file=sys.stderr)
        return 2

    violations: list[str] = []
    checked = 0

    for path in python_files():
        relative = path.relative_to(BACKEND).as_posix()
        if relative in ALLOWED:
            continue
        checked += 1
        visitor = Visitor(relative)
        visitor.visit(ast.parse(path.read_text(), filename=str(path)))
        for lineno, what, rule in visitor.found:
            violations.append(f"  backend/{relative}:{lineno}  {what}\n      {rule}")

    if violations:
        print("The identity boundary has been crossed.\n")
        print("\n\n".join(violations))
        print(
            "\n"
            f"{len(violations)} violation(s) in {checked} files.\n\n"
            "Every authentication operation goes through backend/accounts/"
            "identity.py.\n"
            "The reason is not tidiness: identity moves out to its own service, "
            "and that\n"
            "migration is a day's work while this holds and a rewrite once it "
            "does not.\n\n"
            "If the code above genuinely is not user authentication, add it to "
            "ALLOWED in\n"
            "this script with the reason. If it is, put it behind a function in "
            "identity.py.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Identity boundary holds — {checked} files checked, "
        f"{len(ALLOWED)} allowlisted."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
