"""One phone number, one account.

A farmer's phone number is their identity here: it is the only thing that ties
them to their fields, their photographs and their advice. So the same person
typing the same number has to arrive at the same account, and until now they
did not. `9876543210`, `+919876543210`, `09876543210` and `919876543210` are
one number to a farmer and were four accounts to this node -- the second time
they signed in, on a new phone or after reinstalling, their fields were gone.

It surfaced as something much smaller. The demo login is configured as
`+919876543210`, the app sends exactly what is typed, a farmer types ten
digits, and the door did not open. That was the visible half of a bug whose
invisible half loses people's data.

Deliberately not libphonenumber. That library is 200-odd countries of carrier
metadata for a node that serves one, updated on its own release cadence, and
this file is twenty lines. The tradeoff is that an unknown country code is left
untouched rather than guessed, which is the safe direction: a number this node
cannot confidently normalise is stored as typed, exactly as before.
"""

from __future__ import annotations

# Only countries a node is deployed or planned in. Anything absent is left
# alone -- see the module note. Adding one is a line, and adding one wrongly
# silently re-points every existing account in that country, so the migration
# in 011_phone_e164.sql has to run alongside it.
DIAL_CODES: dict[str, str] = {
    "IN": "91",
    "BR": "55",
    "RU": "7",
    "CN": "86",
    "ZA": "27",
}

# India's national numbering plan: ten digits, first digit 6-9 for mobiles.
NATIONAL_LENGTH: dict[str, int] = {
    "IN": 10,
}


def clean(value: str) -> str:
    """Strip the punctuation people type. No country logic."""
    return (value or "").strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")


def to_e164(value: str, country: str = "IN") -> str:
    """The number in +<country><national> form, when that can be known.

    Returns the cleaned input unchanged when this node cannot be confident --
    an unknown country, or a length that does not match the national plan.
    Guessing would merge two different people into one account, which is worse
    than leaving one person with two.
    """
    cleaned = clean(value)
    if not cleaned:
        return cleaned

    if cleaned.startswith("+"):
        return cleaned

    dial = DIAL_CODES.get((country or "").upper())
    if not dial or not cleaned.isdigit():
        return cleaned

    # A leading zero is the domestic trunk prefix, dropped in international form.
    national = cleaned.lstrip("0") if cleaned.startswith("0") else cleaned

    # Already carries the country code, just without the plus.
    if national.startswith(dial):
        rest = national[len(dial):]
        expected = NATIONAL_LENGTH.get((country or "").upper())
        if expected is None or len(rest) == expected:
            return f"+{national}"

    expected = NATIONAL_LENGTH.get((country or "").upper())
    if expected is not None and len(national) != expected:
        # Not a national number for this plan. Could be a short code, a
        # mistyping, or a foreign number without its +. Left as typed.
        return cleaned

    return f"+{dial}{national}"
