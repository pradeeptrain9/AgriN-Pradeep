"""Numeric guard for model-generated narration.

The product rule is that every quantity a farmer sees is computed by the engine.
A language model that invents an irrigation depth or a nitrogen dose is a
crop-failure liability, and "we told it not to" is not an enforcement mechanism.

So the guard is mechanical: collect every number the deterministic payload
contains, extract every number the model wrote, and reject the narration if the
model produced one that is not in the payload. Rejection triggers one corrective
retry, then falls back to the deterministic template.

Numbers inside payload *strings* count as allowed too, because engine notes
legitimately carry figures ("15 cm below the soil surface", "5 t/ha of manure").
"""

import re

# Matches integers, decimals and thousands-separated numbers, with an optional
# leading sign. Deliberately greedy about separators so "1,250.5" is one number.
NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _normalise(token: str) -> float | None:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def numbers_in_text(text: str) -> list[float]:
    out: list[float] = []
    for match in NUMBER_RE.findall(text or ""):
        value = _normalise(match)
        if value is not None:
            out.append(value)
    return out


def collect_allowed_numbers(payload) -> set[float]:
    """Every number the engine produced, including those inside note strings."""
    allowed: set[float] = set()

    def walk(node) -> None:
        if isinstance(node, bool):
            return  # bools are ints in Python; they are not quantities
        if isinstance(node, (int, float)):
            allowed.add(float(node))
        elif isinstance(node, str):
            allowed.update(numbers_in_text(node))
        elif isinstance(node, dict):
            for key, value in node.items():
                allowed.update(numbers_in_text(str(key)))
                walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(payload)

    # A model may legitimately round: 45.4 mm -> "45 mm". Admit the rounded
    # forms of every allowed value rather than loosening the comparison.
    for value in list(allowed):
        allowed.add(round(value))
        allowed.add(round(value, 1))
    return allowed


def is_supported(value: float, allowed: set[float]) -> bool:
    if value in allowed:
        return True
    for candidate in allowed:
        # Absolute tolerance for small numbers, relative for large ones.
        tolerance = max(0.05, abs(candidate) * 0.005)
        if abs(value - candidate) <= tolerance:
            return True
    return False


def unsupported_numbers(text: str, allowed: set[float]) -> list[float]:
    """Numbers the model wrote that the engine never produced."""
    return [n for n in numbers_in_text(text) if not is_supported(n, allowed)]


def check_narration(narration: dict, payload) -> list[str]:
    """Validate a narration dict against a payload.

    Returns a list of human-readable violations; empty means the narration only
    uses figures the engine computed.
    """
    allowed = collect_allowed_numbers(payload)
    violations: list[str] = []

    def check(label: str, text: str) -> None:
        bad = unsupported_numbers(text, allowed)
        if bad:
            rendered = ", ".join(f"{n:g}" for n in bad)
            violations.append(f"{label} contains figures not in the data: {rendered}")

    check("summary", narration.get("summary", ""))
    check("explanation", narration.get("explanation", ""))
    for index, action in enumerate(narration.get("actions") or []):
        check(f"action[{index}].title", action.get("title", ""))
        check(f"action[{index}].detail", action.get("detail", ""))
    return violations
