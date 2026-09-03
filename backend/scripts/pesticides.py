"""Manage the pesticide allowlist.

The table is empty by default and rows serve only when verified. This is the
tool that adds them, and it exists to make the safe path the easy one: every
import demands provenance, validates against the crop and disease taxonomy, and
refuses anything it cannot check.

    python scripts/pesticides.py template > rows.csv     # blank template
    python scripts/pesticides.py validate rows.csv       # check, change nothing
    python scripts/pesticides.py import rows.csv \
        --verified-by "Dr A. Sharma, State Agriculture University" \
        --register "CIB&RC Major Uses of Pesticides, 2026 edition" \
        --review-by 2027-09-01
    python scripts/pesticides.py list
    python scripts/pesticides.py expiring --days 90
    python scripts/pesticides.py deny --country IN --active <name> \
        --reason "..." --source "..." --by "..."
    python scripts/pesticides.py revoke <id> --reason "..."

NEVER populate this from a language model, a web search, or another country's
register. Doses and pre-harvest intervals are jurisdiction-specific and are set
by the registered product label. If you cannot point at the label, the row does
not go in.
"""

import argparse
import asyncio
import csv
import datetime
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.ai.disease_taxonomy import DISEASES  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.engine.crops import CROPS  # noqa: E402

COLUMNS = [
    "country", "crop_code", "disease_code", "active_ingredient", "product_name",
    "dose", "phi_days", "max_applications_per_season", "notes_for_farmer",
]

TEMPLATE_HELP = """\
# AgriN pesticide allowlist import template.
#
# One row per registered use. Copy the values from the product label or the
# national register; do not derive them from anything else.
#
#   country       ISO code, e.g. IN
#   crop_code     must exist in app/engine/crops.py, e.g. rice
#   disease_code  must exist in app/ai/disease_taxonomy.py, e.g. rice__blast
#   dose          exactly as the label states it, including units
#   phi_days      pre-harvest interval in days, 1-365
#
# Lines beginning with # are ignored.
"""


def read_rows(path: pathlib.Path) -> list[dict]:
    text_body = path.read_text()
    lines = [ln for ln in text_body.splitlines() if not ln.lstrip().startswith("#")]
    return list(csv.DictReader(io.StringIO("\n".join(lines))))


def validate(rows: list[dict]) -> list[str]:
    """Every check that can be made without a human. The ones that cannot -- is
    this the right dose, is this product actually registered -- are exactly why
    verified_by is mandatory."""
    problems: list[str] = []
    seen: set[tuple] = set()

    for index, row in enumerate(rows, start=2):
        where = f"row {index}"
        crop = (row.get("crop_code") or "").strip()
        disease = (row.get("disease_code") or "").strip()
        active = (row.get("active_ingredient") or "").strip()
        dose = (row.get("dose") or "").strip()

        if crop not in CROPS:
            problems.append(f"{where}: unknown crop_code {crop!r}")
        if disease not in DISEASES:
            problems.append(f"{where}: unknown disease_code {disease!r}")
        elif crop and disease and not disease.startswith(crop.split("_")[0]):
            problems.append(
                f"{where}: disease {disease!r} does not belong to crop {crop!r}"
            )
        if not active:
            problems.append(f"{where}: active_ingredient is required")
        if not dose:
            problems.append(f"{where}: dose is required, copied from the label")
        if not (row.get("country") or "").strip():
            problems.append(f"{where}: country is required")

        try:
            phi = int(row.get("phi_days") or 0)
            if not 1 <= phi <= 365:
                problems.append(f"{where}: phi_days {phi} outside 1-365")
        except ValueError:
            problems.append(f"{where}: phi_days must be a whole number")

        # A disease the fungicide cannot act on is a wasted spray and a wasted day.
        entry = DISEASES.get(disease)
        if entry and entry.pathogen_type in ("bacterial", "viral"):
            problems.append(
                f"{where}: {disease} is {entry.pathogen_type}; a chemical row here "
                "will never be served, because the diagnosis path removes chemicals "
                "for bacterial and viral disease"
            )
        if entry and entry.is_healthy:
            problems.append(f"{where}: {disease} is the healthy class")

        key = (row.get("country"), crop, disease, active.lower())
        if key in seen:
            problems.append(f"{where}: duplicate of an earlier row")
        seen.add(key)

    return problems


async def do_import(rows: list[dict], verified_by: str, register: str,
                    review_by: datetime.date) -> int:
    inserted = 0
    async with SessionLocal() as db:
        for row in rows:
            await db.execute(
                text(
                    "INSERT INTO pesticides (country, crop_code, disease_code, "
                    "  active_ingredient, product_name, dose, phi_days, "
                    "  max_applications_per_season, notes_for_farmer, verified, "
                    "  verified_by, verified_at, register_source, review_by) "
                    "VALUES (:country, :crop, :disease, :active, :product, :dose, "
                    "  :phi, :maxapp, :notes, true, :by, now(), :register, :review) "
                    "ON CONFLICT (country, crop_code, disease_code, active_ingredient) "
                    "DO UPDATE SET dose = EXCLUDED.dose, phi_days = EXCLUDED.phi_days, "
                    "  verified_by = EXCLUDED.verified_by, verified_at = now(), "
                    "  register_source = EXCLUDED.register_source, "
                    "  review_by = EXCLUDED.review_by, revoked_at = NULL"
                ),
                {
                    "country": row["country"].strip(),
                    "crop": row["crop_code"].strip(),
                    "disease": row["disease_code"].strip(),
                    "active": row["active_ingredient"].strip(),
                    "product": (row.get("product_name") or "").strip() or None,
                    "dose": row["dose"].strip(),
                    "phi": int(row["phi_days"]),
                    "maxapp": int(row["max_applications_per_season"])
                    if (row.get("max_applications_per_season") or "").strip() else None,
                    "notes": (row.get("notes_for_farmer") or "").strip() or None,
                    "by": verified_by, "register": register, "review": review_by,
                },
            )
            inserted += 1
        await db.commit()
    return inserted


async def do_list() -> None:
    async with SessionLocal() as db:
        result = await db.execute(
            text(
                "SELECT country, crop_code, disease_code, active_ingredient, dose, "
                "  phi_days, verified, revoked_at, review_by, verified_by "
                "FROM pesticides ORDER BY country, crop_code, disease_code"
            )
        )
        rows = list(result.mappings())
        if not rows:
            print("Allowlist is empty. No chemical advice will be offered, which "
                  "is the safe default for an unverified node.")
            return
        today = datetime.date.today()
        for row in rows:
            state = "REVOKED" if row["revoked_at"] else (
                "EXPIRED" if row["review_by"] and row["review_by"] < today
                else "serving" if row["verified"] else "unverified")
            print(f"  [{state:<10}] {row['country']} {row['crop_code']}/"
                  f"{row['disease_code']} {row['active_ingredient']} -- {row['dose']}, "
                  f"PHI {row['phi_days']}d, review {row['review_by']}")

        denied = await db.execute(text("SELECT country, active_ingredient, reason "
                                       "FROM pesticide_denylist ORDER BY country"))
        for row in denied.mappings():
            print(f"  [DENIED    ] {row['country']} {row['active_ingredient']} "
                  f"-- {row['reason']}")


async def do_expiring(days: int) -> None:
    from app.engine.treatments import expiring_soon

    async with SessionLocal() as db:
        rows = await expiring_soon(db, within_days=days)
    if not rows:
        print(f"Nothing expires in the next {days} days.")
        return
    print(f"{len(rows)} row(s) stop serving within {days} days unless re-verified:")
    for row in rows:
        print(f"  {row['review_by']}  {row['country']} {row['crop_code']}/"
              f"{row['disease_code']} {row['active_ingredient']} "
              f"(last verified by {row['verified_by']})")


async def do_deny(country: str, active: str, reason: str, source: str, by: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO pesticide_denylist (country, active_ingredient, reason, "
                "  source, added_by) VALUES (:c, :a, :r, :s, :b) "
                "ON CONFLICT (country, active_ingredient) DO UPDATE SET "
                "  reason = EXCLUDED.reason, source = EXCLUDED.source"
            ),
            {"c": country, "a": active, "r": reason, "s": source, "b": by},
        )
        await db.commit()
    print(f"{active} is now denied in {country}. It will not be served even if a "
          "verified row exists for it.")


async def do_revoke(row_id: str, reason: str) -> None:
    async with SessionLocal() as db:
        await db.execute(
            text("UPDATE pesticides SET revoked_at = now(), revoked_reason = :r "
                 "WHERE id = :id"),
            {"id": row_id, "r": reason},
        )
        await db.commit()
    print(f"Revoked {row_id}: {reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("template")
    v = sub.add_parser("validate"); v.add_argument("csv")
    i = sub.add_parser("import")
    i.add_argument("csv")
    i.add_argument("--verified-by", required=True,
                   help="Name and role of the person who checked these rows")
    i.add_argument("--register", required=True,
                   help="The register and edition consulted")
    i.add_argument("--review-by", required=True,
                   help="Date these rows must be re-checked, YYYY-MM-DD")
    sub.add_parser("list")
    e = sub.add_parser("expiring"); e.add_argument("--days", type=int, default=60)
    d = sub.add_parser("deny")
    for flag in ("--country", "--active", "--reason", "--source", "--by"):
        d.add_argument(flag, required=True)
    r = sub.add_parser("revoke"); r.add_argument("id"); r.add_argument("--reason", required=True)

    args = parser.parse_args()

    if args.command == "template":
        print(TEMPLATE_HELP + ",".join(COLUMNS))
        return 0

    if args.command in ("validate", "import"):
        rows = read_rows(pathlib.Path(args.csv))
        if not rows:
            print("No rows found.")
            return 1
        problems = validate(rows)
        for problem in problems:
            print(f"  {problem}")
        if problems:
            print(f"\n{len(problems)} problem(s). Nothing imported.")
            return 1
        print(f"{len(rows)} row(s) passed automated validation.")
        if args.command == "validate":
            print("Automated checks cannot tell you whether a dose is correct. "
                  "That is what --verified-by attests to.")
            return 0
        review = datetime.date.fromisoformat(args.review_by)
        if review <= datetime.date.today():
            print("--review-by must be in the future.")
            return 1
        count = asyncio.run(do_import(rows, args.verified_by, args.register, review))
        print(f"Imported {count} row(s), attributed to {args.verified_by}, "
              f"review by {review}.")
        return 0

    if args.command == "list":
        asyncio.run(do_list()); return 0
    if args.command == "expiring":
        asyncio.run(do_expiring(args.days)); return 0
    if args.command == "deny":
        asyncio.run(do_deny(args.country, args.active, args.reason, args.source, args.by))
        return 0
    if args.command == "revoke":
        asyncio.run(do_revoke(args.id, args.reason)); return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
