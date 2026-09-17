"""Signature validation, including the behind-a-proxy trap.

The scaffold's headline gotcha: a request arriving through Cloudflare Tunnel
does not know the URL Twilio signed. These tests pin the behaviour that
protects against it -- validation must succeed for the configured public URL
and fail for the URL the app would have reconstructed internally.
"""

from __future__ import annotations

import pytest
from twilio.request_validator import RequestValidator

from app.config import Settings
from app.twilio_client import TwilioGateway

TOKEN = "fake_auth_token_for_tests_only"

FORM = {
    "MessageSid": "SM00000000000000000000000000000001",
    "AccountSid": "AC00000000000000000000000000000001",
    "From": "+15552220000",
    "To": "+15551110000",
    "Body": "numbers station test",
    "NumMedia": "0",
}


@pytest.fixture
def gateway() -> TwilioGateway:
    settings = Settings(
        _env_file=None,
        TWILIO_ACCOUNT_SID="AC00000000000000000000000000000001",
        TWILIO_AUTH_TOKEN=TOKEN,
        PUBLIC_BASE_URL="https://text.example.com",
        APP_PASSPHRASE="irrelevant-here",
        SESSION_SECRET="irrelevant-here-but-long-enough-0000000",
    )
    return TwilioGateway(settings)


def sign(url: str, params: dict[str, str]) -> str:
    return RequestValidator(TOKEN).compute_signature(url, params)


def test_accepts_signature_for_configured_public_url(gateway):
    url = "https://text.example.com/webhooks/twilio/sms"
    assert gateway.validate_signature(url, FORM, sign(url, FORM)) is True


def test_rejects_signature_computed_for_the_internal_url(gateway):
    """The exact failure mode PUBLIC_BASE_URL exists to prevent.

    Twilio signs the public https URL. If validation were done against the
    URL the app sees behind the tunnel (http, localhost, a port), the
    signature would not match -- so a signature made for that internal URL
    must be rejected when checked against the public one.
    """
    internal = "http://127.0.0.1:8090/webhooks/twilio/sms"
    public = "https://text.example.com/webhooks/twilio/sms"
    assert gateway.validate_signature(public, FORM, sign(internal, FORM)) is False


def test_rejects_tampered_body(gateway):
    url = "https://text.example.com/webhooks/twilio/sms"
    signature = sign(url, FORM)
    tampered = dict(FORM, Body="something else entirely")
    assert gateway.validate_signature(url, tampered, signature) is False


def test_rejects_missing_signature(gateway):
    url = "https://text.example.com/webhooks/twilio/sms"
    assert gateway.validate_signature(url, FORM, None) is False
    assert gateway.validate_signature(url, FORM, "") is False


def test_extra_unexpected_fields_are_part_of_the_signature(gateway):
    """Twilio's field set grows; a new field must still validate cleanly."""
    url = "https://text.example.com/webhooks/twilio/sms"
    grown = dict(FORM, SomeFutureTwilioField="whatever")
    assert gateway.validate_signature(url, grown, sign(url, grown)) is True


def test_validation_can_be_disabled_for_local_development():
    settings = Settings(
        _env_file=None,
        TWILIO_ACCOUNT_SID="AC1",
        TWILIO_AUTH_TOKEN=TOKEN,
        PUBLIC_BASE_URL="http://127.0.0.1:8090",
        QUIETWAVE_VALIDATE_TWILIO_SIGNATURE=False,
        APP_PASSPHRASE="irrelevant-here",
        SESSION_SECRET="irrelevant-here-but-long-enough-0000000",
    )
    gateway = TwilioGateway(settings)
    assert gateway.validate_signature("http://whatever", FORM, None) is True


def test_webhook_urls_are_built_from_config_not_requests():
    settings = Settings(
        _env_file=None,
        PUBLIC_BASE_URL="https://text.example.com/",  # trailing slash on purpose
        APP_PASSPHRASE="irrelevant-here",
        SESSION_SECRET="irrelevant-here-but-long-enough-0000000",
    )
    assert settings.webhook_sms_url == "https://text.example.com/webhooks/twilio/sms"
    assert (
        settings.webhook_status_url == "https://text.example.com/webhooks/twilio/status"
    )
