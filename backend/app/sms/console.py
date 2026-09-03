"""Development gateway: prints the code instead of sending it.

Used when no provider is configured. It refuses to run outside development, so a
node cannot reach production silently unable to sign anybody in -- which is the
failure mode where everything looks healthy and no farmer can get past the login
screen.
"""

import logging

from app.sms.base import SmsError, SmsGateway, SmsResult

logger = logging.getLogger(__name__)


class ConsoleGateway(SmsGateway):
    name = "console"

    def __init__(self, environment: str) -> None:
        self._environment = environment

    async def send(self, phone: str, message: str) -> SmsResult:
        if self._environment != "dev":
            raise SmsError(
                "No SMS provider is configured. Set SMS_PROVIDER and its "
                "credentials; the console gateway only runs in development.",
                retryable=False,
            )
        logger.warning("[dev-sms] %s -> %s", phone, message)
        print(f"[dev-sms] {phone} -> {message}")
        return SmsResult(provider=self.name, accepted=True, detail="printed to log")
