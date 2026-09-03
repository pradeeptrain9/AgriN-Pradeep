"""Twilio, over its REST API.

No `twilio` SDK dependency: one authenticated form POST is the whole integration,
and a vendor SDK in a digital public good is a dependency every other country
inherits whether or not they use that vendor.

REGISTRATION, which is what actually delays a launch:
  - US 10DLC senders need brand and campaign registration, 10-15 business days
  - India requires DLT registration with the telcos, independently of Twilio
  - Twilio Verify is exempt from A2P registration and is available immediately

If a pilot is blocked waiting on registration, Verify is the way through. It owns
code generation and checking, so it replaces this node's OTP logic rather than
plugging in behind it -- see docs/DEPLOYMENT.md.
"""

import httpx

from app.sms.base import SmsError, SmsGateway, SmsResult

API_ROOT = "https://api.twilio.com/2010-04-01"
TIMEOUT = 20.0


class TwilioGateway(SmsGateway):
    name = "twilio"

    def __init__(self, account_sid: str, auth_token: str, sender: str,
                 messaging_service_sid: str | None = None) -> None:
        if not account_sid or not auth_token:
            raise SmsError("Twilio requires TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN")
        if not sender and not messaging_service_sid:
            raise SmsError(
                "Twilio requires either TWILIO_FROM_NUMBER or "
                "TWILIO_MESSAGING_SERVICE_SID"
            )
        self._sid = account_sid
        self._token = auth_token
        self._sender = sender
        self._service = messaging_service_sid

    async def send(self, phone: str, message: str) -> SmsResult:
        payload = {"To": phone, "Body": message}
        # A Messaging Service handles sender pools, geo-matching and sticky
        # sender; a bare number does not. Prefer it when configured.
        if self._service:
            payload["MessagingServiceSid"] = self._service
        else:
            payload["From"] = self._sender

        url = f"{API_ROOT}/Accounts/{self._sid}/Messages.json"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    url, data=payload, auth=(self._sid, self._token), timeout=TIMEOUT
                )
        except httpx.HTTPError as exc:
            raise SmsError(f"Twilio unreachable: {exc}", retryable=True) from exc

        if response.status_code in (200, 201):
            body = response.json()
            return SmsResult(
                provider=self.name, accepted=True,
                provider_message_id=body.get("sid"), detail=body.get("status"),
            )

        detail = _twilio_detail(response)
        # 429 and 5xx are worth another attempt; a 400 means the request is wrong
        # and retrying just burns money.
        retryable = response.status_code == 429 or response.status_code >= 500
        raise SmsError(f"Twilio rejected the message: {detail}", retryable=retryable)


def _twilio_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    code = body.get("code")
    message = body.get("message", f"HTTP {response.status_code}")
    # The two errors that actually happen in practice, named plainly.
    if code == 30034:
        return (f"{message} (error 30034: the sending number is not registered "
                "for A2P 10DLC)")
    if code == 21608:
        return (f"{message} (error 21608: trial accounts can only send to "
                "verified numbers)")
    return f"{message} (error {code})" if code else message
