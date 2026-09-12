"""SMS gateway tests.

Without SMS nobody can sign in, so the failure modes matter more than the happy
path. In particular a misconfigured production node must fail loudly at the first
sign-in attempt rather than looking healthy while nobody can get past the login
screen.
"""

import json

import httpx
import pytest
import respx

from app.sms.base import SmsError, otp_message
from app.sms.console import ConsoleGateway
from app.sms.twilio import TwilioGateway
from app.sms.webhook import WebhookGateway


class TestOtpMessage:
    def test_contains_the_code_and_lifetime(self):
        text = otp_message("123456", 5)
        assert "123456" in text and "5 minutes" in text

    def test_warns_against_sharing(self):
        """OTP phishing against smallholders is common, so the warning is in the
        message itself rather than only in the app."""
        text = otp_message("123456", 5)
        assert "Never share" in text

    def test_names_the_sender(self):
        assert "AgriN" in otp_message("123456", 5)
        assert "KrishiNode" in otp_message("123456", 5, brand="KrishiNode")


class TestConsoleGateway:
    @pytest.mark.asyncio
    async def test_prints_in_development(self):
        result = await ConsoleGateway("dev").send("+919876500000", "hello")
        assert result.accepted

    @pytest.mark.asyncio
    async def test_refuses_outside_development(self):
        """The failure mode this prevents: a production node that looks healthy
        while no farmer can sign in."""
        with pytest.raises(SmsError) as excinfo:
            await ConsoleGateway("production").send("+919876500000", "hello")
        assert "No SMS provider is configured" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_the_refusal_names_the_escape_hatch(self):
        # Someone testing their own deployed node hits this error and needs to
        # be told the supported way out, or they will reach for AGRIN_ENV=dev --
        # which also returns the code in the response body and opens CORS.
        with pytest.raises(SmsError) as excinfo:
            await ConsoleGateway("prod").send("+919876500000", "hello")
        assert "SMS_ALLOW_CONSOLE" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_an_explicit_flag_allows_it_outside_development(self):
        # The one legitimate case: an operator reading codes out of their own
        # node's logs before an SMS provider exists.
        result = await ConsoleGateway("prod", allow_outside_dev=True).send(
            "+919876500000", "hello"
        )
        assert result.accepted

    @pytest.mark.asyncio
    async def test_the_flag_has_to_be_set_on_purpose(self):
        # Default off. A node that never heard of this flag keeps the refusal.
        with pytest.raises(SmsError):
            await ConsoleGateway("prod").send("+919876500000", "hello")

    @pytest.mark.asyncio
    async def test_the_code_is_never_returned_to_the_caller(self):
        """The whole point of the flag over AGRIN_ENV=dev.

        dev mode puts the OTP in the HTTP response body, so on a public URL
        anyone can request a code for any phone number and sign in as that
        farmer. This path logs it and returns nothing but an acknowledgement --
        reaching the code requires access to the host's logs.
        """
        result = await ConsoleGateway("prod", allow_outside_dev=True).send(
            "+919876500000", "your code is 123456"
        )
        assert "123456" not in str(result)
        assert result.detail == "printed to log"


class TestTheConsoleFlagIsNarrow:
    """It must open exactly one door.

    Structural, because the alternative failure is silent: someone later
    extends this flag to also relax the response body or CORS, and a node that
    an operator believed was merely logging its codes starts handing them to
    anyone who asks.
    """

    def test_it_does_not_touch_the_response_body(self):
        import inspect

        from app.api import auth

        source = inspect.getsource(auth)
        assert "sms_allow_console" not in source, (
            "the OTP response must depend on agrin_env alone"
        )

    def test_it_does_not_touch_cors(self):
        import inspect

        from app import main

        assert "sms_allow_console" not in inspect.getsource(main)

    def test_a_node_using_it_is_still_not_ready(self):
        # An operator can sign in by reading logs; a farmer holding a phone
        # cannot, and that is the only test that matters.
        import inspect

        from app.api import readiness

        source = inspect.getsource(readiness.readiness)
        allowed = source.split("sms_allow_console")[1][:400]
        assert '"blocker"' in allowed


class TestTwilioGateway:
    def test_requires_credentials(self):
        with pytest.raises(SmsError):
            TwilioGateway("", "", "+15550000000")

    def test_requires_a_sender(self):
        with pytest.raises(SmsError):
            TwilioGateway("AC123", "token", "")

    def test_messaging_service_satisfies_the_sender_requirement(self):
        gateway = TwilioGateway("AC123", "token", "", messaging_service_sid="MG1")
        assert gateway.name == "twilio"

    @respx.mock
    @pytest.mark.asyncio
    async def test_successful_send(self):
        route = respx.post(
            "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
        ).mock(return_value=httpx.Response(201, json={"sid": "SM1", "status": "queued"}))
        result = await TwilioGateway("AC123", "t", "+15550000000").send("+91987", "hi")
        assert result.accepted and result.provider_message_id == "SM1"
        sent = dict(x.split("=", 1) for x in route.calls[0].request.content.decode().split("&"))
        assert "From" in sent

    @respx.mock
    @pytest.mark.asyncio
    async def test_messaging_service_is_preferred_over_a_bare_number(self):
        route = respx.post(
            "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
        ).mock(return_value=httpx.Response(201, json={"sid": "SM1"}))
        await TwilioGateway("AC123", "t", "+1555", messaging_service_sid="MG1").send(
            "+91987", "hi")
        body = route.calls[0].request.content.decode()
        assert "MessagingServiceSid" in body and "From=" not in body

    @respx.mock
    @pytest.mark.asyncio
    async def test_unregistered_sender_error_is_explained(self):
        """30034 is the error every unregistered US sender hits first."""
        respx.post(
            "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
        ).mock(return_value=httpx.Response(
            400, json={"code": 30034, "message": "Message blocked"}))
        with pytest.raises(SmsError) as excinfo:
            await TwilioGateway("AC123", "t", "+1555").send("+91987", "hi")
        assert "A2P 10DLC" in str(excinfo.value)
        assert excinfo.value.retryable is False

    @respx.mock
    @pytest.mark.asyncio
    async def test_trial_account_error_is_explained(self):
        respx.post(
            "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
        ).mock(return_value=httpx.Response(
            400, json={"code": 21608, "message": "unverified"}))
        with pytest.raises(SmsError) as excinfo:
            await TwilioGateway("AC123", "t", "+1555").send("+91987", "hi")
        assert "verified numbers" in str(excinfo.value)

    @respx.mock
    @pytest.mark.asyncio
    async def test_rate_limit_is_retryable_but_bad_request_is_not(self):
        respx.post(
            "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
        ).mock(return_value=httpx.Response(429, json={"message": "slow down"}))
        with pytest.raises(SmsError) as excinfo:
            await TwilioGateway("AC123", "t", "+1555").send("+91987", "hi")
        assert excinfo.value.retryable is True


class TestWebhookGateway:
    def test_requires_a_url(self):
        with pytest.raises(SmsError):
            WebhookGateway("")

    @respx.mock
    @pytest.mark.asyncio
    async def test_substitutes_phone_and_message(self):
        route = respx.post("https://sms.example/send").mock(
            return_value=httpx.Response(200, json={"ok": True}))
        gateway = WebhookGateway(
            "https://sms.example/send",
            payload_template='{"mobile": "{phone}", "msg": "{message}", "id": "T1"}',
            content_type="json")
        await gateway.send("+919876500000", "your code is 123456")
        body = json.loads(route.calls[0].request.content)
        assert body["mobile"] == "+919876500000"
        assert body["msg"] == "your code is 123456"
        assert body["id"] == "T1"          # DLT template id passes through

    @respx.mock
    @pytest.mark.asyncio
    async def test_quotes_in_the_message_do_not_corrupt_the_payload(self):
        """An apostrophe or newline in the text would otherwise produce a
        malformed body the aggregator rejects."""
        respx.post("https://sms.example/send").mock(
            return_value=httpx.Response(200, json={"ok": True}))
        gateway = WebhookGateway(
            "https://sms.example/send",
            payload_template='{"msg": "{message}"}', content_type="json")
        result = await gateway.send("+91987", 'say "hi"\nnow')
        assert result.accepted

    @respx.mock
    @pytest.mark.asyncio
    async def test_http_200_with_a_failure_body_is_caught(self):
        """Several aggregators report failure in the body with a 200 status."""
        respx.post("https://sms.example/send").mock(
            return_value=httpx.Response(200, json={"success": False, "error": "no credit"}))
        gateway = WebhookGateway(
            "https://sms.example/send", payload_template='{"m":"{message}"}',
            content_type="json", success_field="success")
        with pytest.raises(SmsError) as excinfo:
            await gateway.send("+91987", "hi")
        assert "reported failure" in str(excinfo.value)

    @respx.mock
    @pytest.mark.asyncio
    async def test_server_error_is_retryable(self):
        respx.post("https://sms.example/send").mock(return_value=httpx.Response(502))
        gateway = WebhookGateway("https://sms.example/send",
                                 payload_template='{"m":"{message}"}')
        with pytest.raises(SmsError) as excinfo:
            await gateway.send("+91987", "hi")
        assert excinfo.value.retryable is True

    def test_invalid_template_is_reported_clearly(self):
        gateway = WebhookGateway("https://sms.example/send",
                                 payload_template='{"broken": ')
        with pytest.raises(SmsError) as excinfo:
            gateway._render("+91987", "hi")
        assert "not valid JSON" in str(excinfo.value)
