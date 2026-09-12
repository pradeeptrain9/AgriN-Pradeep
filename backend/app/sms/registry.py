"""Select the SMS gateway from configuration."""

import json

from app.config import get_settings
from app.sms.base import SmsError, SmsGateway
from app.sms.console import ConsoleGateway
from app.sms.twilio import TwilioGateway
from app.sms.webhook import WebhookGateway

PROVIDERS = ("console", "twilio", "webhook")

_cached: SmsGateway | None = None


def build_gateway() -> SmsGateway:
    settings = get_settings()
    provider = (settings.sms_provider or "console").lower()

    if provider == "console":
        return ConsoleGateway(
            settings.agrin_env, allow_outside_dev=settings.sms_allow_console
        )
    if provider == "twilio":
        return TwilioGateway(
            account_sid=settings.twilio_account_sid,
            auth_token=settings.twilio_auth_token,
            sender=settings.twilio_from_number,
            messaging_service_sid=settings.twilio_messaging_service_sid or None,
        )
    if provider == "webhook":
        headers = {}
        if settings.sms_webhook_headers:
            try:
                headers = json.loads(settings.sms_webhook_headers)
            except json.JSONDecodeError as exc:
                raise SmsError(f"SMS_WEBHOOK_HEADERS is not valid JSON: {exc}") from exc
        return WebhookGateway(
            url=settings.sms_webhook_url,
            method=settings.sms_webhook_method,
            payload_template=settings.sms_webhook_payload,
            headers=headers,
            content_type=settings.sms_webhook_content_type,
            success_field=settings.sms_webhook_success_field or None,
        )
    raise SmsError(f"Unknown SMS_PROVIDER {provider!r}. Known: {PROVIDERS}")


def gateway() -> SmsGateway:
    global _cached
    if _cached is None:
        _cached = build_gateway()
    return _cached


def reset() -> None:
    """Drop the cached gateway. Used by tests and after a config change."""
    global _cached
    _cached = None
