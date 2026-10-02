#!/usr/bin/env python3
"""
The website's prices and the portal's prices are the same prices.

═══════════════════════════════════════════════════════════════════════════════
CLAUDE.md has said this since the catalogue was written:

  "Published prices are duplicated in gen-portal/backend/operations/management/
   commands/seed_services.py; they currently match, and NOTHING TESTS THAT THEY
   STILL DO."

This file makes that sentence false.

On 2026-10-02 the drift arrived in a new shape. Managed hosting shipped to
genmars.co.ke while the portal change sat in an unmerged pull request, so the
live site carried three "Order — Managed hosting" buttons pointing at
app.genmars.co.ke/order?service=hosting, a slug the catalogue did not have.
Nothing crashed. The client simply arrived at a page where the thing they had
just been sold was not listed.
═══════════════════════════════════════════════════════════════════════════════

── WHY THE TWO COPIES EXIST AT ALL ─────────────────────────────────────────────

They are not redundant. The website is a static export with no database, and
the portal cannot reach across a repository boundary at runtime, so a signed-in
client picking a tier needs the numbers locally or they get sent back out to
genmars.co.ke to read one and come back. ServiceTier's own docstring says so.

What was missing was anything that notices when the two disagree. Two price
lists is how a client is quoted one number and billed another, and the window
in which they disagree is not an edge case — it is every deploy, because the
repositories ship separately.

── WHAT IT CHECKS, AND WHAT IT DELIBERATELY DOES NOT ───────────────────────────

Only `available: "now"` offers. The platform tiers are published as coming and
are deliberately absent from the catalogue: something nobody can buy must not
be orderable, and flagging that as drift would train people to ignore this.

For each of those:

  1. The offer slug exists in CATALOGUE      — the ?service= link resolves
  2. The unit matches UNITS                  — "per year" billed as monthly is
                                               a real invoice error
  3. Every tier slug exists in TIERS         — the ?tier= link resolves
  4. Every tier price matches                — the number itself
  5. `open: true` matches `is_from`          — "from KES X" is a floor; the
                                               same figure without it is a
                                               quote we have not given

It does NOT compare tier NAMES or the `includes` lists. Those are wording, the
seed command refreshes them from this same source on every run, and a check
that fires on a comma produces exactly the red-by-design pipeline the CI
workflow's own header warns against.

── IT MUST FAIL LOUDLY WHEN IT CANNOT PARSE ───────────────────────────────────

company.ts is TypeScript and this is a regex over it, which is fragile on
purpose: the alternative is a Node dependency in a Python check (Charter 03
§I). Fragile is fine; SILENTLY fragile is not. A parse that finds no offers, or
implausibly few, is reported as a failure of this script rather than as
agreement — a drift checker that passes because it understood nothing is worse
than no checker, because it is believed.

Usage:
    check_price_parity.py                       # sibling checkout
    check_price_parity.py --company path/to/company.ts
    check_price_parity.py --company -           # read company.ts from stdin
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SEED = REPO / "backend" / "operations" / "management" / "commands" / "seed_services.py"

# ~/Genmars/gen-website beside ~/Genmars/gen-portal. CI passes --company
# instead, because it has only this repository checked out.
SIBLING = REPO.parent / "gen-website" / "src" / "lib" / "company.ts"

# Below this, assume the parse failed rather than that the catalogue shrank.
# Seven services shipped before hosting existed; eight after. Six would mean
# something structural changed and was not noticed.
MIN_OFFERS = 6


def fail(message: str) -> None:
    print(f"\n  ✗ {message}\n", file=sys.stderr)
    raise SystemExit(1)


# ── reading the website ──────────────────────────────────────────────────────


def _price_to_decimal(price: str) -> str:
    """'KES 15,000' -> '15000'. The portal stores a number; the site renders."""
    digits = re.sub(r"[^0-9.]", "", price)
    return digits.split(".")[0] or "0"


def parse_company(source: str) -> dict[str, dict]:
    """
    The `offers` array, as {slug: {unit, tiers: {slug: (price, is_from)}}}.

    Offers are split on the top-level `slug:` key, which is the only one at
    four-space indentation — a tier's slug sits deeper, inside `tiers: [`.
    """
    start = source.find("export const offers = [")
    if start == -1:
        fail("Could not find `export const offers = [` in company.ts.")
    body = source[start:]
    end = body.find("\n] as const;")
    if end == -1:
        fail("Could not find the end of the `offers` array in company.ts.")
    body = body[:end]

    # Each offer begins at exactly this indentation; tier slugs are deeper.
    chunks = re.split(r"\n  \{\n", body)[1:]
    offers: dict[str, dict] = {}

    for chunk in chunks:
        slug = re.search(r'^\s{4}slug:\s*"([^"]+)"', chunk, re.M)
        available = re.search(r'^\s{4}available:\s*"([^"]+)"', chunk, re.M)
        unit = re.search(r'^\s{4}unit:\s*"([^"]*)"', chunk, re.M)
        if not slug or not available:
            continue
        if available.group(1) != "now":
            continue

        tiers: dict[str, tuple[str, bool]] = {}
        for line in re.finditer(
            r'\{\s*slug:\s*"([^"]+)",\s*name:\s*"[^"]*",\s*price:\s*"([^"]*)"'
            r"(,\s*open:\s*(true|false))?",
            chunk,
        ):
            tiers[line.group(1)] = (
                _price_to_decimal(line.group(2)),
                line.group(4) == "true",
            )

        if not tiers:
            fail(f"Parsed offer '{slug.group(1)}' from company.ts with no tiers.")

        offers[slug.group(1)] = {
            "unit": unit.group(1) if unit else "",
            "tiers": tiers,
        }

    if len(offers) < MIN_OFFERS:
        fail(
            f"Only parsed {len(offers)} sellable offers from company.ts, expected "
            f"at least {MIN_OFFERS}. The shape of that file has probably changed "
            f"— fix this script rather than lowering MIN_OFFERS."
        )
    return offers


# ── reading the portal ───────────────────────────────────────────────────────


def _literal(source: str, name: str):
    """
    Pull one module-level assignment out of seed_services.py and evaluate it.

    ast.literal_eval, never import: this script runs in CI before `pip
    install`, exactly like check_identity_boundary.py, so importing a Django
    management command is not available and would not be wanted if it were.
    """
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    fail(f"Could not find `{name}` in {SEED.name}.")


def parse_seed(source: str) -> tuple[set[str], dict[str, str], dict[str, dict]]:
    catalogue = _literal(source, "CATALOGUE")
    units = _literal(source, "UNITS")
    tiers = _literal(source, "TIERS")
    slugs = {entry["slug"] for entry in catalogue}
    shaped = {
        service: {t["slug"]: (str(t["price_kes"]), bool(t["is_from"])) for t in rows}
        for service, rows in tiers.items()
    }
    return slugs, units, shaped


# ── the comparison ───────────────────────────────────────────────────────────


def compare(offers: dict[str, dict], seed) -> list[str]:
    slugs, units, seed_tiers = seed
    problems: list[str] = []

    for slug, offer in sorted(offers.items()):
        if slug not in slugs:
            problems.append(
                f"{slug}: on genmars.co.ke as sellable, absent from CATALOGUE. "
                f"Every 'Order' button for it links to "
                f"/order?service={slug}, which resolves to nothing."
            )
            continue

        if units.get(slug, "") != offer["unit"]:
            problems.append(
                f"{slug}: the site bills it '{offer['unit']}', UNITS says "
                f"'{units.get(slug, '')}'."
            )

        theirs = seed_tiers.get(slug, {})
        for tier, (price, is_from) in sorted(offer["tiers"].items()):
            if tier not in theirs:
                problems.append(
                    f"{slug}/{tier}: published, missing from TIERS. "
                    f"/order?service={slug}&tier={tier} selects nothing."
                )
                continue
            ours_price, ours_from = theirs[tier]
            if ours_price != price:
                problems.append(
                    f"{slug}/{tier}: the site says KES {price}, the portal "
                    f"would quote KES {ours_price}."
                )
            if ours_from != is_from:
                published = "from KES" if is_from else "a fixed KES"
                held = "from KES" if ours_from else "a fixed KES"
                problems.append(
                    f"{slug}/{tier}: the site publishes {published}, the "
                    f"portal holds {held}. A floor shown as a quote is a "
                    f"price we have not given."
                )

        for tier in sorted(set(theirs) - set(offer["tiers"])):
            problems.append(
                f"{slug}/{tier}: in TIERS, not published. The portal offers a "
                f"size the price list does not carry."
            )

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--company",
        default=None,
        help="Path to gen-website's src/lib/company.ts, or - for stdin.",
    )
    args = parser.parse_args()

    if args.company == "-":
        company_src = sys.stdin.read()
        where = "stdin"
    else:
        path = Path(args.company) if args.company else SIBLING
        if not path.exists():
            fail(
                f"No company.ts at {path}. Pass --company, or check out "
                f"gen-website beside this repository."
            )
        company_src = path.read_text()
        where = str(path)

    offers = parse_company(company_src)
    seed = parse_seed(SEED.read_text())
    problems = compare(offers, seed)

    if problems:
        print(f"\nThe price list and the catalogue disagree ({where}):\n")
        for problem in problems:
            print(f"  ✗ {problem}")
        print(
            "\nThe website is the price list. Correct seed_services.py to match "
            "it and re-run `seed_services --force` against production.\n"
        )
        return 1

    tiers = sum(len(o["tiers"]) for o in offers.values())
    print(
        f"Prices agree — {len(offers)} sellable services, {tiers} tiers, "
        f"checked against {where}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
