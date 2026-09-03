"""Privacy-preserving aggregate statistics for cross-node exchange.

This is the mechanism that makes AgriN a *network* rather than a set of
unrelated deployments, and it is also where a network like this most easily
becomes a surveillance system. The rule is absolute:

    Nodes exchange aggregates. Nodes never exchange farmer records.

Enforcement is structural, not procedural:

  k-anonymity   a district cell is published only when at least `k` distinct
                fields contribute to it (default 5). Below that the cell is
                suppressed entirely -- not rounded, not fuzzed, removed.

  no identifiers no field id, user id, phone number or geometry ever enters an
                aggregate. Only the district label, the crop, the indicator and
                the statistic.

  complementary suppression: suppressing only the small cells leaks them when a
                total is also published, because the reader can subtract. If any
                cell in a group is suppressed, the group total is suppressed too.

The last one is the mistake that sinks most well-meant anonymisation schemes.
"""

from dataclasses import dataclass, field as dc_field
from statistics import mean, median

DEFAULT_K = 5


@dataclass(frozen=True)
class Observation:
    """One field's contribution. Carries a field id ONLY so distinct fields can
    be counted; the id never reaches the published output."""

    field_id: str
    district: str
    crop_code: str
    indicator: str
    value: float


@dataclass
class AggregateCell:
    district: str
    crop_code: str
    indicator: str
    field_count: int
    mean: float
    median: float

    def to_dict(self) -> dict:
        return {
            "district": self.district,
            "crop_code": self.crop_code,
            "indicator": self.indicator,
            "field_count": self.field_count,
            "mean": round(self.mean, 4),
            "median": round(self.median, 4),
        }


@dataclass
class AggregateResult:
    k: int
    cells: list[AggregateCell]
    suppressed_cells: int
    suppressed_fields: int
    totals_suppressed: bool
    notes: list[str] = dc_field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "k_anonymity": self.k,
            "cells": [c.to_dict() for c in self.cells],
            "suppressed_cell_count": self.suppressed_cells,
            "suppressed_field_count": self.suppressed_fields,
            "totals_suppressed": self.totals_suppressed,
            "notes": self.notes,
        }


def aggregate(observations: list[Observation], *, k: int = DEFAULT_K) -> AggregateResult:
    """Group by (district, crop, indicator) and suppress anything below k."""
    if k < 2:
        raise ValueError("k must be at least 2; k=1 publishes individual fields")

    groups: dict[tuple[str, str, str], list[Observation]] = {}
    for observation in observations:
        key = (observation.district, observation.crop_code, observation.indicator)
        groups.setdefault(key, []).append(observation)

    cells: list[AggregateCell] = []
    suppressed_cells = 0
    suppressed_fields = 0
    notes: list[str] = []

    for (district, crop_code, indicator), items in sorted(groups.items()):
        # Distinct fields, not observations: one field reporting twenty times is
        # still one farmer, and counting rows would defeat the threshold.
        distinct = {item.field_id for item in items}
        if len(distinct) < k:
            suppressed_cells += 1
            suppressed_fields += len(distinct)
            continue

        # One value per field so a heavily-sampled field cannot dominate.
        per_field: dict[str, list[float]] = {}
        for item in items:
            per_field.setdefault(item.field_id, []).append(item.value)
        values = [mean(v) for v in per_field.values()]

        cells.append(
            AggregateCell(
                district=district,
                crop_code=crop_code,
                indicator=indicator,
                field_count=len(distinct),
                mean=mean(values),
                median=median(values),
            )
        )

    # Complementary suppression. Publishing a total alongside suppressed cells
    # lets a reader recover the suppressed values by subtraction, so the total
    # goes too.
    totals_suppressed = suppressed_cells > 0
    if totals_suppressed:
        notes.append(
            f"{suppressed_cells} cell(s) had fewer than {k} fields and were removed. "
            "Group totals are withheld as well, because a total published next to "
            "a suppressed cell would allow the suppressed value to be recovered by "
            "subtraction."
        )
    if not cells:
        notes.append(
            "No cell met the k-anonymity threshold, so nothing is published. This "
            "is the expected result for a node with few fields."
        )
    return AggregateResult(
        k=k,
        cells=cells,
        suppressed_cells=suppressed_cells,
        suppressed_fields=suppressed_fields,
        totals_suppressed=totals_suppressed,
        notes=notes,
    )


# Fields that must never appear in a published aggregate. Asserted by tests so a
# future change cannot quietly widen what leaves the node.
FORBIDDEN_KEYS = frozenset({
    "field_id", "user_id", "phone", "geometry", "centroid", "lat", "lon",
    "latitude", "longitude", "image_path", "name",
})


def contains_identifiers(payload) -> list[str]:
    """Recursively find any forbidden key in a payload about to be published."""
    found: list[str] = []

    def walk(node, path: str = "") -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in FORBIDDEN_KEYS:
                    found.append(f"{path}{key}")
                walk(value, f"{path}{key}.")
        elif isinstance(node, (list, tuple)):
            for index, item in enumerate(node):
                walk(item, f"{path}{index}.")

    walk(payload)
    return found
