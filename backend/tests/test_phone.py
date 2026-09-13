"""One phone number, one account.

The number is the farmer's identity -- the only thing tying them to their
fields and photographs. Four spellings of one number meant four accounts, and
the second sign-in on a new phone found none of their data.
"""

import pytest

from app.phone import to_e164


class TestIndianNumbers:
    @pytest.mark.parametrize(
        "typed",
        [
            "9876543210",        # what a farmer actually types
            "+919876543210",     # what the demo login is configured as
            "09876543210",       # trunk prefix, as dialled at home
            "919876543210",      # country code, no plus
            "+91 98765 43210",   # as printed on a form
            "98765-43210",       # as written by hand
        ],
    )
    def test_every_spelling_reaches_one_account(self, typed):
        assert to_e164(typed, "IN") == "+919876543210"


class TestWhenNotToGuess:
    """Merging two people into one account is worse than one person having two."""

    def test_a_number_that_is_not_a_national_mobile_is_left_alone(self):
        # Could be a short code or a mistyping. Storing it as typed changes
        # nothing about how it behaved before.
        assert to_e164("12345", "IN") == "12345"
        assert to_e164("987654321", "IN") == "987654321"
        assert to_e164("98765432101", "IN") == "98765432101"

    def test_an_unknown_country_is_left_alone(self):
        assert to_e164("5551234", "XX") == "5551234"

    def test_an_international_number_is_never_re_prefixed(self):
        assert to_e164("+14155550100", "IN") == "+14155550100"

    def test_empty_stays_empty(self):
        assert to_e164("", "IN") == ""
        assert to_e164(None, "IN") == ""


class TestIdempotence:
    def test_normalising_twice_changes_nothing(self):
        # The OTP is stored against the normalised form and verified against it
        # again. A rule that is not idempotent fails every second sign-in with
        # "Incorrect code" and tells the farmer nothing true.
        for typed in ("9876543210", "+919876543210", "12345"):
            once = to_e164(typed, "IN")
            assert to_e164(once, "IN") == once


class TestRequestAndVerifyAgree:
    def test_both_schemas_normalise_the_same_way(self):
        from app.schemas import OtpRequest, OtpVerify

        request = OtpRequest(phone="9876543210")
        verify = OtpVerify(phone="+91 98765 43210", code="404404")
        assert request.phone == verify.phone == "+919876543210"


class TestDemoDoor:
    def test_the_demo_number_opens_from_the_form_a_farmer_types(self):
        """The bug that surfaced all of this: DEMO_PHONE is written +91…, the
        app sends ten digits, and the door silently did not open."""
        from app.demo import is_demo_phone

        class Settings:
            demo_phone = "+919876543210"
            demo_code = "404404"
            node_country = "IN"

        assert is_demo_phone("9876543210", Settings()) is True
        assert is_demo_phone("+919876543210", Settings()) is True
        assert is_demo_phone("9876543211", Settings()) is False
