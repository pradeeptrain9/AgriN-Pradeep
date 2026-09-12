"""A single allowlisted account that can sign in without an SMS provider.

Why this exists. The node's own `/ready` calls the missing SMS provider a
blocker, and it is right: codes go to the host's logs, so only whoever can read
those logs can get past the login screen. That is correct for farmers and
useless for anyone evaluating the node -- a reviewer installs the app, reaches
the login screen, and stops.

So: one phone number, one fixed code, both read from the environment. Requesting
a code for that number stores the fixed code instead of a random one and skips
the gateway entirely. Verification is untouched -- the stored hash matches the
way any other code's would, so the expiry, the attempt cap and the 30-second
resend interval all still apply.

This is a deliberate open door and it is described as one. It is defensible only
because this node has no farmers on it. Two things keep that honest:

  * It fails closed. Both variables must be set, and the code must look like a
    code; anything else and the door does not exist.
  * `/ready` reports it, and escalates it from degraded to blocker the moment
    any account other than the demo account exists on the node. An operator
    cannot enrol a farmer and leave this on without the node saying so.

The code is never returned in a response. It is published out of band, to the
people meant to have it, the same as handing someone a test login.
"""

from __future__ import annotations

CODE_LENGTH = 6


def normalise_phone(value: str) -> str:
    """The same cleaning `OtpRequest` applies, so comparison is like for like.

    Without this, DEMO_PHONE="+91 98765 43210" would never match the normalised
    "+919876543210" that arrives from the app, and the door would silently not
    work -- which is a confusing failure but, note, the safe direction.
    """
    return (value or "").strip().replace(" ", "").replace("-", "")


def code_is_well_formed(code: str) -> bool:
    """Six digits, matching `generate_otp` and the app's code field.

    A short or non-numeric code would be unenterable on the phone, and a blank
    one would make every comparison below trivially true.
    """
    return len(code) == CODE_LENGTH and code.isdigit()


def demo_login_enabled(settings) -> bool:
    phone = normalise_phone(settings.demo_phone)
    # min_length=6 on OtpRequest; anything shorter could not be submitted
    # anyway, and a one- or two-character value is far more likely to be a
    # mistake than an intention.
    return bool(phone) and len(phone) >= 6 and code_is_well_formed(settings.demo_code)


def is_demo_phone(phone: str, settings) -> bool:
    """True only for the one allowlisted number, and only when fully configured.

    Constant-time comparison is not used and is not needed: the phone number is
    published alongside the code, so there is no secret here to time out of it.
    """
    if not demo_login_enabled(settings):
        return False
    return normalise_phone(phone) == normalise_phone(settings.demo_phone)
