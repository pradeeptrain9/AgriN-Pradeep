"""SMS gateway interface.

Deliberately provider-agnostic, and deliberately plain HTTP rather than a vendor
SDK. Each country runs its own AgriN node and will use its own aggregator --
Twilio in some places, MSG91 or Gupshup in India, Zenvia in Brazil -- and a
digital public good should not make one of those a dependency of the codebase.

The interface is one method. Everything a provider needs beyond a phone number
and a message comes from configuration.
"""

from dataclasses import dataclass
from typing import Protocol


class SmsError(RuntimeError):
    """Delivery failed. Carries whether retrying could plausibly help."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class SmsResult:
    provider: str
    accepted: bool
    provider_message_id: str | None = None
    detail: str | None = None


class SmsGateway(Protocol):
    name: str

    async def send(self, phone: str, message: str) -> SmsResult:
        """Deliver one message. Raise SmsError on failure."""
        ...


def otp_message(code: str, minutes: int, brand: str = "AgriN") -> str:
    """The text a farmer actually receives.

    Kept short and literal on purpose: it may be read aloud by someone else, or
    read by someone who reads slowly. It names the sender so a farmer knows why
    they got it, and says plainly that nobody should ask for it -- OTP phishing
    against smallholders is common.
    """
    return (
        f"{code} is your {brand} code. It works for {minutes} minutes. "
        f"Never share it with anyone, including people claiming to be from {brand}."
    )
