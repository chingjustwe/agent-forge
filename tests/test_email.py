"""Tests for the email sending abstraction layer."""

from unittest.mock import patch

import pytest

from src.gateway.email import sender as _snd
from src.gateway.email.sender import ConsoleEmailSender, get_sender, send_invite_email


def _reset_sender():
    """Reset the sender singleton after each test to avoid cross-test pollution."""
    _snd._INSTANCE = None


@pytest.fixture(autouse=True)
def _cleanup_sender():
    """Reset sender singleton before and after each test."""
    _reset_sender()
    yield
    _reset_sender()


class TestConsoleEmailSender:
    """ConsoleEmailSender should log without raising."""

    def test_send_plain_text(self):
        sender = ConsoleEmailSender()
        # Should not raise
        sender.send(to="test@test.com", subject="Test", text="Hello")

    def test_send_with_html(self):
        sender = ConsoleEmailSender()
        sender.send(to="test@test.com", subject="Test", text="Hello", html="<p>Hello</p>")

    def test_send_empty_subject(self):
        sender = ConsoleEmailSender()
        sender.send(to="test@test.com", subject="", text="Hello")

    def test_send_special_characters(self):
        sender = ConsoleEmailSender()
        sender.send(to="user+tag@test.com", subject="Invitation: João", text="Olá, mundo!")


class TestGetSender:
    """get_sender() should return the right implementation based on settings."""

    def test_returns_console_when_no_smtp(self):
        """With default settings (no SMTP), get_sender() returns ConsoleEmailSender."""
        from src.infra.settings import settings

        with patch.object(settings, "smtp_host", ""):
            _snd._INSTANCE = None
            sender = get_sender()
            assert isinstance(sender, ConsoleEmailSender)

    def test_returns_console_when_smtp_host_empty(self):
        """Explicitly empty SMTP settings → ConsoleEmailSender."""
        from src.infra.settings import settings

        with patch.object(settings, "smtp_host", ""):
            sender = get_sender()
            assert isinstance(sender, ConsoleEmailSender)

    def test_returns_smtp_when_configured(self):
        """With SMTP host set, get_sender() returns SmtpEmailSender."""
        from src.infra.settings import settings
        from src.gateway.email.sender import SmtpEmailSender

        with patch.multiple(
            settings,
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="user",
            smtp_password="pass",
            smtp_from="noreply@example.com",
        ):
            _snd._INSTANCE = None
            sender = get_sender()
            assert isinstance(sender, SmtpEmailSender)
            assert sender.host == "smtp.example.com"
            assert sender.port == 587
            assert sender.from_addr == "noreply@example.com"

    def test_singleton_reused(self):
        """get_sender() should return the same instance within a session."""
        first = get_sender()
        second = get_sender()
        assert first is second


class TestSendInviteEmail:
    """send_invite_email() should call the underlying sender with correct args."""

    def test_sends_email_with_invite_url(self):
        """Verify the invite URL is passed to the sender."""
        sender = ConsoleEmailSender()
        with patch.object(sender, "send") as mock_send:
            with patch("src.gateway.email.sender.get_sender", return_value=sender):
                send_invite_email(
                    email="newuser@test.com",
                    invite_url="http://localhost:5173/invite?token=abc123",
                )
                mock_send.assert_called_once()
                _args, _kwargs = mock_send.call_args
                assert _kwargs["to"] == "newuser@test.com"
                assert "invite" in _kwargs["text"]
