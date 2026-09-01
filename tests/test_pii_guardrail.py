import pytest
from guardrails.pii_guardrail import PIIGuardrail


def test_email_masking():
    assert PIIGuardrail.mask_pii("Contact me at user@example.com") == "Contact me at ***@example.com"
    assert PIIGuardrail.mask_pii("Multiple: a@b.com and test.user+label@domain.co.uk") == "Multiple: ***@b.com and ***@domain.co.uk"


def test_phone_masking():
    assert PIIGuardrail.mask_pii("Call 555-123-4567 today") == "Call ***-***-**** today"
    assert PIIGuardrail.mask_pii("Phone: (555) 123-4567") == "Phone: ***-***-****"


def test_no_pii():
    assert PIIGuardrail.mask_pii("Hello world 123") == "Hello world 123"


def test_intl_phone_masking():
    # E.164: + followed by 7-15 digits. The mask keeps the leading + and
    # replaces the remaining digits with stars, one fewer than the digit
    # count to keep the visual width stable.
    out = PIIGuardrail.mask_pii("Call +14155552671 anytime")
    assert "+1" not in out.replace("+**********", "")
    # 11 digits -> 10 stars
    assert "+**********" in out
    # The full original number must not survive
    assert "4155552671" not in out

    out2 = PIIGuardrail.mask_pii("UK office: +44 20 7946 0958")
    # 12 digits -> 11 stars
    assert "+***********" in out2


def test_ssn_masking():
    out = PIIGuardrail.mask_pii("SSN 123-45-6789 recorded")
    assert "***-**-****" in out
    assert "123-45-6789" not in out

    # Non-SSN digit runs (e.g. dates, zip codes) are left alone.
    assert PIIGuardrail.mask_pii("Born 1990-01-15") == "Born 1990-01-15"


def test_ipv4_masking():
    out = PIIGuardrail.mask_pii("Server 192.168.1.42 responded")
    assert "***.***.***.***" in out
    assert "192.168.1.42" not in out

    # A non-IP dot-separated number (octet > 255) is left alone.
    assert PIIGuardrail.mask_pii("Version 999.888.777.666 is fine") == "Version 999.888.777.666 is fine"


def test_credit_card_masking_valid_luhn():
    # 4111 1111 1111 1111 is a well-known Luhn-valid test number.
    out = PIIGuardrail.mask_pii("Card 4111 1111 1111 1111 expires 12/30")
    assert "****-****-****-****" in out
    assert "4111 1111 1111 1111" not in out

    # 5500-0000-0000-0004 is also Luhn-valid (Mastercard test card).
    out2 = PIIGuardrail.mask_pii("Backup card 5500-0000-0000-0004")
    assert "****-****-****-****" in out2


def test_credit_card_does_not_mask_invalid_luhn():
    # 4111 1111 1111 1112 fails the Luhn check; we must not mask it to
    # avoid false positives on plain digit sequences.
    out = PIIGuardrail.mask_pii("Reference 4111 1111 1111 1112 is unrelated")
    assert "4111 1111 1111 1112" in out
    assert "****-****-****-****" not in out


def test_combined_pii_in_one_string():
    text = (
        "Customer bob@example.com (SSN 111-22-3333) on 10.0.0.5, "
        "card 4111 1111 1111 1111, phone (415) 555-2671."
    )
    out = PIIGuardrail.mask_pii(text)
    assert "***@example.com" in out
    assert "***-**-****" in out
    assert "***.***.***.***" in out
    assert "****-****-****-****" in out
    assert "***-***-****" in out
    # No raw PII survives
    for raw in ("bob@example.com", "111-22-3333", "10.0.0.5",
                "4111 1111 1111 1111", "(415) 555-2671"):
        assert raw not in out


def test_pii_mask_handles_non_string_input():
    # Non-strings are stringified; the result is masked if it contains
    # a recognisable pattern.
    assert PIIGuardrail.mask_pii(None) == "None"
    assert PIIGuardrail.mask_pii(42) == "42"
    # Email-shaped string: the local-part is masked, the domain keeps
    # the @-sign so a downstream consumer knows it was an email.
    out = PIIGuardrail.mask_pii("foo@bar.com")
    assert out == "***@bar.com"
    # The local-part must be gone
    assert "foo" not in out
