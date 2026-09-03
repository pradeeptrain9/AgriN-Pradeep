"""Chemical treatment allowlist, verified-only.

Naming a pesticide, a dose and a pre-harvest interval is the most dangerous
thing this system can do. Get it wrong and a farmer sprays a product that is
unregistered for the crop, at the wrong rate, too close to harvest -- a residue
violation, a rejected consignment, or a poisoning.

So the design is safe by default:

  * Chemical advice is served ONLY from rows in the `pesticides` table whose
    `verified` flag is true. A row is verified when a qualified person has
    checked it against the national register (CIB&RC in India, MAPA in Brazil,
    and so on) and recorded who checked it and when.
  * The table ships EMPTY of chemical rows. An unverified deployment therefore
    gives cultural and preventive advice only. That is a deliberate choice:
    saying nothing is safe, saying the wrong dose is not.
  * A language model may never introduce a product name. Anything it returns is
    intersected with this table and dropped if absent -- see `filter_to_allowlist`.

The IPM advice in ai/disease_taxonomy.py is unaffected: it carries no dosages
and is always available.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ChemicalOption:
    active_ingredient: str
    product_name: str | None
    dose: str
    phi_days: int
    country: str
    notes: str | None = None
    max_applications_per_season: int | None = None
    verified_by: str | None = None
    register_source: str | None = None
    review_by: str | None = None

    def to_dict(self) -> dict:
        return {
            "active_ingredient": self.active_ingredient,
            "product_name": self.product_name,
            "dose": self.dose,
            "pre_harvest_interval_days": self.phi_days,
            "country": self.country,
            "notes": self.notes,
            "max_applications_per_season": self.max_applications_per_season,
            "provenance": {
                "verified_by": self.verified_by,
                "register_source": self.register_source,
                "review_by": self.review_by,
            },
            "warning": (
                "Read the product label before use. Wear protective clothing. "
                "Do not harvest for at least "
                f"{self.phi_days} days after spraying."
            ),
        }


async def lookup_chemicals(
    db: AsyncSession, *, country: str, crop_code: str, disease_code: str
) -> list[ChemicalOption]:
    """Servable chemical options for one country, crop and disease.

    Four conditions, all of which must hold. Any one failing removes the row:

      verified      a qualified person checked it against a national register
      not revoked   the registration has not since been withdrawn
      in review     the review date has not passed; registers change, and a row
                    checked three years ago is not evidence about today
      not denied    the active is not on this country's denylist

    Expiry is enforced in the query rather than by a cleanup job, so a node that
    nobody maintains fails closed -- it stops offering chemicals instead of
    serving stale ones indefinitely.

    An empty list is the expected state of a fresh node, not an error.
    """
    result = await db.execute(
        text(
            "SELECT p.active_ingredient, p.product_name, p.dose, p.phi_days, "
            "  p.country, p.notes_for_farmer, p.max_applications_per_season, "
            "  p.verified_by, p.register_source, p.review_by "
            "FROM pesticides p "
            "WHERE p.country = :country AND p.crop_code = :crop "
            "  AND p.disease_code = :disease "
            "  AND p.verified = true "
            "  AND p.revoked_at IS NULL "
            "  AND p.review_by >= CURRENT_DATE "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM pesticide_denylist d "
            "    WHERE d.country = p.country "
            "      AND lower(d.active_ingredient) = lower(p.active_ingredient)) "
            "ORDER BY p.active_ingredient"
        ),
        {"country": country, "crop": crop_code, "disease": disease_code},
    )
    return [
        ChemicalOption(
            active_ingredient=row["active_ingredient"],
            product_name=row["product_name"],
            dose=row["dose"],
            phi_days=row["phi_days"],
            country=row["country"],
            notes=row["notes_for_farmer"],
            max_applications_per_season=row["max_applications_per_season"],
            verified_by=row["verified_by"],
            register_source=row["register_source"],
            review_by=row["review_by"].isoformat() if row["review_by"] else None,
        )
        for row in result.mappings()
    ]


async def expiring_soon(db: AsyncSession, *, within_days: int = 60) -> list[dict]:
    """Rows whose review date is close, so an operator can re-check before they
    silently stop serving."""
    result = await db.execute(
        text(
            "SELECT country, crop_code, disease_code, active_ingredient, review_by, "
            "  verified_by, register_source "
            "FROM pesticides WHERE verified AND revoked_at IS NULL "
            "  AND review_by BETWEEN CURRENT_DATE "
            "  AND CURRENT_DATE + make_interval(days => :days) "
            "ORDER BY review_by"
        ),
        {"days": within_days},
    )
    return [
        {**dict(row), "review_by": row["review_by"].isoformat()}
        for row in result.mappings()
    ]


def filter_to_allowlist(
    candidates: list[dict], allowed: list[ChemicalOption]
) -> tuple[list[dict], list[str]]:
    """Keep only candidate chemicals that appear in the verified allowlist.

    Used on anything a language model produces. Matching is on active ingredient,
    case-insensitively; the dose and pre-harvest interval are always taken from
    the allowlist row, never from the model, even when the ingredient matches.

    Returns (kept, rejected_ingredient_names).
    """
    by_ingredient = {o.active_ingredient.strip().lower(): o for o in allowed}
    kept: list[dict] = []
    rejected: list[str] = []

    for candidate in candidates:
        name = str(candidate.get("active_ingredient", "")).strip().lower()
        match = by_ingredient.get(name)
        if match is None:
            rejected.append(candidate.get("active_ingredient") or "(unnamed)")
            continue
        # Always serve the verified row, discarding the model's own figures.
        kept.append(match.to_dict())
    return kept, rejected
