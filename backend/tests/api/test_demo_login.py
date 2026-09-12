"""One published sign-in, and nothing else.

The demo account exists so a node with no SMS provider can still be evaluated.
It is a deliberate open door, which means the interesting tests are not "does it
work" but "how narrow is it" -- every one below is about something it must
refuse to do.

The failure that matters: a half-typed environment variable that opens the door
wider than one number, or a node that grows real accounts around a published
code and says nothing.
"""

import pathlib
from dataclasses import dataclass

import pytest

from app.demo import code_is_well_formed, demo_login_enabled, is_demo_phone, normalise_phone

AUTH = pathlib.Path(__file__).resolve().parents[2] / "app" / "api" / "auth.py"
READINESS = pathlib.Path(__file__).resolve().parents[2] / "app" / "api" / "readiness.py"


@dataclass
class FakeSettings:
    demo_phone: str = ""
    demo_code: str = ""


CONFIGURED = FakeSettings(demo_phone="+919876543210", demo_code="404404")


class TestItIsOffUntilDeliberatelyTurnedOn:
    def test_a_default_node_has_no_demo_account(self):
        assert demo_login_enabled(FakeSettings()) is False

    def test_no_phone_matches_when_it_is_off(self):
        # The dangerous shape: normalise("") == normalise("") is true, so a
        # blank DEMO_PHONE compared naively would match a blank submission.
        assert is_demo_phone("", FakeSettings()) is False
        assert is_demo_phone("+919876543210", FakeSettings()) is False

    @pytest.mark.parametrize("settings", [
        FakeSettings(demo_phone="+919876543210"),          # code missing
        FakeSettings(demo_code="404404"),                  # phone missing
    ])
    def test_half_configured_fails_closed(self, settings):
        # Whichever half is missing, the door must not exist. Opening on one
        # variable would mean a single stray export is enough.
        assert demo_login_enabled(settings) is False

    @pytest.mark.parametrize("code", ["4044", "40440404", "40440a", "", "   404", "abcdef"])
    def test_a_code_that_is_not_six_digits_is_refused(self, code):
        # Six digits is what generate_otp produces and what the app's code field
        # accepts, so anything else could not be entered on a phone anyway. A
        # blank one would make the comparison trivially true.
        assert code_is_well_formed(code) is False
        assert demo_login_enabled(FakeSettings("+919876543210", code)) is False

    def test_a_very_short_phone_is_refused(self):
        # OtpRequest sets min_length=6. A one- or two-character DEMO_PHONE is
        # far more likely to be a mistake than an intention.
        assert demo_login_enabled(FakeSettings("+91", "404404")) is False


class TestItOpensForExactlyOneNumber:
    def test_the_allowlisted_number_is_recognised(self):
        assert is_demo_phone("+919876543210", CONFIGURED) is True

    @pytest.mark.parametrize("written", [
        "+91 98765 43210",
        "+91-98765-43210",
        "  +919876543210  ",
    ])
    def test_it_survives_how_the_number_was_typed_into_the_environment(self, written):
        # OtpRequest strips spaces and dashes before this is reached, so an
        # operator who pastes a formatted number into DEMO_PHONE would otherwise
        # get a door that silently never opens.
        assert is_demo_phone("+919876543210", FakeSettings(written, "404404")) is True

    @pytest.mark.parametrize("other", [
        "+919876543211",      # one digit away
        "+91987654321",       # a prefix of it
        "+9198765432100",     # it, with a digit appended
        "919876543210",       # same digits, no country prefix
        "+911234567890",
    ])
    def test_every_other_number_goes_through_the_real_gateway(self, other):
        assert is_demo_phone(other, CONFIGURED) is False

    def test_a_prefix_match_is_not_a_match(self):
        # Guarding against a future `startswith`, which would open the door to
        # an entire number range.
        assert is_demo_phone("+9198765432109999", CONFIGURED) is False


class TestTheCodeNeverLeavesTheNode:
    """The one property that cannot be given up: a published code is fine, a
    code returned on the wire to anybody who asks is an open node."""

    def test_the_request_response_does_not_carry_the_demo_code(self):
        source = AUTH.read_text()
        request_body = source.split("async def request_otp")[1].split("async def verify")[0]
        assert 'response["demo_account"] = True' in request_body
        assert "demo_code" not in request_body.split("response = ")[1]

    def test_only_the_dev_branch_ever_returns_a_code(self):
        # dev_code is the pre-existing local-testing convenience and is gated on
        # agrin_env == "dev". The demo path must not acquire a sibling.
        source = AUTH.read_text()
        returning = [
            line for line in source.splitlines()
            if "response[" in line and "=" in line
        ]
        assert any("dev_code" in line for line in returning)
        assert not any("demo" in line and "code" in line for line in returning)


class TestVerificationIsUnchanged:
    def test_the_demo_code_is_stored_hashed_like_any_other(self):
        source = AUTH.read_text()
        request_body = source.split("async def request_otp")[1].split("async def verify")[0]
        # One insert, one hash call, whichever branch produced the code.
        assert request_body.count("hash_otp(code, payload.phone)") == 1
        assert request_body.count("INSERT INTO otp_codes") == 1

    def test_verification_knows_nothing_about_the_demo_account(self):
        # Expiry, the attempt cap and single-use all live in verify(). If the
        # demo path had its own branch there, those would have to be re-proved.
        source = AUTH.read_text()
        verify_body = source.split("async def verify")[1]
        assert "demo" not in verify_body.lower()

    def test_the_resend_interval_still_applies(self):
        # The rate limit is checked before the branch, so the demo number cannot
        # be used to hammer the database either.
        source = AUTH.read_text()
        request_body = source.split("async def request_otp")[1].split("async def verify")[0]
        assert request_body.index("OTP_MIN_INTERVAL_SECONDS") < request_body.index("is_demo_phone")


class TestNoSmsIsSentForIt:
    def test_the_gateway_is_skipped_for_the_demo_number(self):
        # The entire point: this node has no working provider. Calling it would
        # raise 503 and the door would not open.
        source = AUTH.read_text()
        assert "if not demo:\n        await send_otp" in source

    def test_a_real_number_still_goes_through_the_gateway(self):
        source = AUTH.read_text()
        assert source.count("await send_otp(") == 1


class TestTheNodeKeepsTellingTheTruth:
    def test_readiness_reports_the_door_at_all(self):
        assert "demo_login" in READINESS.read_text()

    def test_it_becomes_a_blocker_once_anyone_else_has_an_account(self):
        # The whole defence of this feature is "no farmers on this node". The
        # node checks that itself rather than trusting the operator to remember,
        # because the moment it stops being true nobody will be watching.
        source = READINESS.read_text()
        demo_block = source.split("# --- demo login")[1].split("# --- satellite")[0]
        assert "SELECT count(*) FROM users WHERE phone <> :phone" in demo_block
        assert '"demo_login", "blocker"' in demo_block
        assert '"demo_login", "degraded"' in demo_block

    def test_the_blocker_says_how_to_close_the_door(self):
        source = READINESS.read_text()
        demo_block = source.split("# --- demo login")[1].split("# --- satellite")[0]
        assert "DEMO_PHONE" in demo_block and "DEMO_CODE" in demo_block

    def test_half_configured_is_reported_rather_than_silent(self):
        # Failing closed is right, but an operator who was handed a login that
        # does not work deserves to be told why.
        source = READINESS.read_text()
        assert "half-configured" in source

    def test_the_sms_blocker_is_untouched(self):
        # A demo account does not mean farmers can sign in, and /ready must not
        # start implying they can.
        source = READINESS.read_text()
        assert "No farmer can." in source


class TestNormalisation:
    @pytest.mark.parametrize("written,expected", [
        ("+91 98765 43210", "+919876543210"),
        ("+91-98765-43210", "+919876543210"),
        ("  +919876543210 ", "+919876543210"),
        ("", ""),
    ])
    def test_it_matches_what_OtpRequest_does(self, written, expected):
        assert normalise_phone(written) == expected

    def test_it_tolerates_an_unset_variable(self):
        assert normalise_phone(None) == ""
