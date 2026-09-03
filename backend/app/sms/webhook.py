"""Generic HTTP gateway for any aggregator.

This is what makes the SMS layer usable outside the countries whose providers we
happened to implement. India's DLT rules push most senders onto local
aggregators -- MSG91, Gupshup, Kaleyra -- and Brazil, South Africa and China each
have their own. Shipping an SDK per provider is not sustainable for a network
meant to be deployed by other people.

Instead: a configurable form or JSON POST. `{phone}`, `{message}` and any
configured extras are substituted into the template, so most aggregator APIs are
reachable with configuration alone.

INDIA: DLT registration with the telcos is a legal requirement regardless of
aggregator. Sender ID and message template must be registered before anything is
delivered, and the delivered text must match the registered template exactly --
which is why SMS_TEMPLATE_ID exists below.
"""

import json

import httpx

from app.sms.base import SmsError, SmsGateway, SmsResult

TIMEOUT = 20.0


class WebhookGateway(SmsGateway):
    name = "webhook"

    def __init__(self, url: str, method: str = "POST", payload_template: str = "",
                 headers: dict | None = None, content_type: str = "form",
                 success_field: str | None = None) -> None:
        if not url:
            raise SmsError("SMS_WEBHOOK_URL is required for the webhook gateway")
        self._url = url
        self._method = method.upper()
        self._template = payload_template or '{"to": "{phone}", "text": "{message}"}'
        self._headers = headers or {}
        self._content_type = content_type
        self._success_field = success_field

    def _render(self, phone: str, message: str) -> dict:
        rendered = (
            self._template
            .replace("{phone}", phone)
            # JSON-escape the message: an apostrophe or newline in the text would
            # otherwise produce a malformed body that the aggregator rejects.
            .replace("{message}", json.dumps(message)[1:-1])
        )
        try:
            return json.loads(rendered)
        except json.JSONDecodeError as exc:
            raise SmsError(
                f"SMS_WEBHOOK_PAYLOAD is not valid JSON after substitution: {exc}"
            ) from exc

    async def send(self, phone: str, message: str) -> SmsResult:
        body = self._render(phone, message)
        try:
            async with httpx.AsyncClient() as client:
                kwargs = {"headers": self._headers, "timeout": TIMEOUT}
                if self._content_type == "json":
                    kwargs["json"] = body
                else:
                    kwargs["data"] = body
                response = await client.request(self._method, self._url, **kwargs)
        except httpx.HTTPError as exc:
            raise SmsError(f"SMS webhook unreachable: {exc}", retryable=True) from exc

        if response.status_code >= 400:
            retryable = response.status_code == 429 or response.status_code >= 500
            raise SmsError(
                f"SMS webhook returned {response.status_code}: {response.text[:200]}",
                retryable=retryable,
            )

        # Several aggregators return HTTP 200 with a failure in the body, so an
        # optional success field can be asserted rather than trusting the status.
        detail = response.text[:200]
        if self._success_field:
            try:
                payload = response.json()
            except ValueError:
                raise SmsError(f"Expected JSON to check "
                               f"'{self._success_field}', got: {detail}") from None
            if not payload.get(self._success_field):
                raise SmsError(
                    f"Aggregator reported failure in '{self._success_field}': {detail}"
                )
        return SmsResult(provider=self.name, accepted=True, detail=detail)
