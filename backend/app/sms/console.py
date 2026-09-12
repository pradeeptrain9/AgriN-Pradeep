"""Development gateway: prints the code instead of sending it.

Used when no provider is configured. It refuses to run outside development, so a
node cannot reach production silently unable to sign anybody in -- which is the
failure mode where everything looks healthy and no farmer can get past the login
screen.

`allow_outside_dev` (SMS_ALLOW_CONSOLE) is the one exception: an operator
testing their own deployed node before an SMS provider exists, reading the code
out of the host's logs. It has to be set on purpose and it changes nothing else
-- in particular the code is still never returned to the caller, which is what
makes this different from, and much narrower than, AGRIN_ENV=dev.
"""

import logging

from app.sms.base import SmsError, SmsGateway, SmsResult

logger = logging.getLogger(__name__)


class ConsoleGateway(SmsGateway):
    name = "console"

    def __init__(self, environment: str, *, allow_outside_dev: bool = False) -> None:
        self._environment = environment
        self._allow_outside_dev = allow_outside_dev

    async def send(self, phone: str, message: str) -> SmsResult:
        if self._environment != "dev" and not self._allow_outside_dev:
            raise SmsError(
                "No SMS provider is configured. Set SMS_PROVIDER and its "
                "credentials; the console gateway only runs in development. "
                "To read codes from this node's own logs while testing it, set "
                "SMS_ALLOW_CONSOLE=true -- but no farmer can sign in until a "
                "real provider is configured.",
                retryable=False,
            )
        # Logged, never returned. The caller gets {"sent": true} and nothing
        # else, so reaching the code requires access to the host's logs.
        logger.warning("[dev-sms] %s -> %s", phone, message)
        print(f"[dev-sms] {phone} -> {message}")
        return SmsResult(provider=self.name, accepted=True, detail="printed to log")
