#!/usr/bin/env python3
"""
The lockfile still says what requirements.txt asks for.

═══════════════════════════════════════════════════════════════════════════════
requirements.lock.txt is what the image installs. requirements.txt is what a
human edits. Nothing connects them at build time, so bumping a version in one
and forgetting the other is silent: the build succeeds, the tests pass, and the
change simply does not reach production.

That is the same shape as the three controls this project has already found
documented-but-absent — the identity boundary check, the restore drill, and the
lockfile itself. A generated file with no check that it was regenerated is a
control nobody is enforcing.
═══════════════════════════════════════════════════════════════════════════════

── WHAT IT CHECKS ──────────────────────────────────────────────────────────────

For every requirement in requirements.txt, the lockfile pins a version that
SATISFIES it. `Django==5.2.*` is satisfied by `Django==5.2.17` and not by
`Django==5.1.15` or `Django==6.1.1`.

── WHAT IT DOES NOT CHECK ──────────────────────────────────────────────────────

That the lockfile is a complete and correct RESOLUTION — that every transitive
dependency is present and mutually compatible. Only pip can know that, and it
knows it by doing the install, which is what the image build already does. This
catches the mistake people actually make (edit one file, forget the other), not
a hand-corrupted lockfile.

It also does not verify hashes, because there are none. See the lockfile header.

Run it directly:

    python scripts/check_lockfile.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
REQUIREMENTS = BACKEND / "requirements.txt"
LOCKFILE = BACKEND / "requirements.lock.txt"

# name, then the pinned version. Extras and environment markers are stripped:
# `psycopg[binary]==3.2.*` is the psycopg distribution as far as pip freeze is
# concerned, and the lockfile lists it without the extra.
LINE = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)")


def parse(path: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line.lstrip().startswith("#") or not line.strip():
            continue
        if line.lstrip().startswith("-r "):
            continue
        match = LINE.match(line)
        if match:
            # PyPI treats names case-insensitively and normalises separators.
            name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
            found[name] = match.group(2)
    return found


def satisfies(pinned: str, wanted: str) -> bool:
    """`5.2.17` satisfies `5.2.*`; `5.1.15` and `6.1.1` do not."""
    if not wanted.endswith(".*"):
        return pinned == wanted
    prefix = wanted[:-1]  # keep the trailing dot: "5.2."
    return pinned.startswith(prefix)


def main() -> int:
    if not LOCKFILE.exists():
        print(
            f"FATAL: {LOCKFILE.name} is missing, but requirements.txt and the "
            "Dockerfile both refer to it.\n"
            "Regenerate it — the command is in the header of requirements.txt.",
            file=sys.stderr,
        )
        return 1

    wanted = parse(REQUIREMENTS)
    locked = parse(LOCKFILE)

    problems: list[str] = []
    for name, constraint in sorted(wanted.items()):
        pinned = locked.get(name)
        if pinned is None:
            problems.append(
                f"  {name}: requirements.txt asks for =={constraint}, "
                f"and the lockfile does not mention it at all"
            )
        elif not satisfies(pinned, constraint):
            problems.append(
                f"  {name}: requirements.txt asks for =={constraint}, "
                f"lockfile pins {pinned}"
            )

    if problems:
        print("The lockfile no longer matches requirements.txt.\n")
        print("\n".join(problems))
        print(
            "\n"
            "The image installs from requirements.lock.txt, so until it is\n"
            "regenerated this change reaches nothing. From backend/:\n\n"
            '    docker run --rm -v "$PWD/requirements.txt:/req.txt:ro" '
            "python:3.13-slim \\\n"
            "        sh -c 'pip install -q -r /req.txt && pip freeze "
            "--exclude-editable'\n\n"
            "Read the diff before committing it.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Lockfile matches — {len(wanted)} direct requirement(s) satisfied, "
        f"{len(locked)} package(s) pinned."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
