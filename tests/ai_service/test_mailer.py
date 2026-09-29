import smtplib

import pytest

from ai_service import mailer
from ai_service.errors import InfrastructureError


class FakeSMTP:
    """Stands in for smtplib.SMTP / SMTP_SSL; records what the mailer did."""

    instances: list["FakeSMTP"] = []
    fail_with: Exception | None = None

    def __init__(self, host, port, timeout=None, context=None):
        if FakeSMTP.fail_with is not None:
            raise FakeSMTP.fail_with
        self.host, self.port, self.timeout = host, port, timeout
        self.started_tls = False
        self.login_args = None
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.fail_with = None
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mailer.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


@pytest.fixture
def email_config(service_config):
    def make(**overrides):
        base = dict(
            smtp_host="smtp.example.kz",
            email_from="stt@example.kz",
            email_to=("ops@example.kz", "qa@example.kz"),
        )
        base.update(overrides)
        return service_config(**base)

    return make


def test_success_message_has_summary_transcript_and_attachment(email_config):
    msg = mailer.build_message(email_config(), "id-1", "суть", "[00:00:00] привет")

    assert msg["From"] == "stt@example.kz"
    assert msg["To"] == "ops@example.kz, qa@example.kz"
    assert msg["Subject"] == "Транскрибация звонка id-1"
    body = msg.get_body(("plain",)).get_content()
    assert "Краткое содержание:\nсуть" in body
    assert "[00:00:00] привет" in body
    [attachment] = list(msg.iter_attachments())
    assert attachment.get_filename() == "id-1.txt"
    assert attachment.get_content().rstrip("\n") == "[00:00:00] привет"


def test_success_message_without_summary_omits_section(email_config):
    body = mailer.build_message(email_config(), "id-1", "", "t").get_body(("plain",)).get_content()
    assert "Краткое содержание" not in body


def test_error_message_carries_reason(email_config):
    msg = mailer.build_message(
        email_config(), "id-1", "", "", error=True, error_description="corrupt audio"
    )
    assert msg["Subject"] == "Ошибка транскрибации звонка id-1"
    assert "corrupt audio" in msg.get_content()
    assert list(msg.iter_attachments()) == []


def test_deliver_starttls_with_login(email_config, fake_smtp):
    cfg = email_config(smtp_username="user", smtp_password="pw")
    mailer.deliver(cfg, "id-1", "s", "t")

    [client] = fake_smtp.instances
    assert (client.host, client.port) == ("smtp.example.kz", 587)
    assert client.started_tls is True
    assert client.login_args == ("user", "pw")
    assert client.sent[0]["Subject"] == "Транскрибация звонка id-1"


def test_deliver_plain_without_login(email_config, fake_smtp):
    mailer.deliver(email_config(smtp_security="none", smtp_port=25), "id-1", "s", "t")

    [client] = fake_smtp.instances
    assert client.started_tls is False
    assert client.login_args is None
    assert len(client.sent) == 1


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError("down"),
        TimeoutError("slow"),
        smtplib.SMTPAuthenticationError(535, b"bad creds"),
    ],
)
def test_smtp_failures_are_infrastructure_errors(email_config, fake_smtp, exc):
    fake_smtp.fail_with = exc
    with pytest.raises(InfrastructureError):
        mailer.deliver(email_config(), "id-1", "s", "t")


def test_source_file_shown_when_known(email_config):
    ok = mailer.build_message(email_config(), "id-1", "s", "t", call_record_url="s3://calls/a.mp3")
    assert "Файл: s3://calls/a.mp3" in ok.get_body(("plain",)).get_content()
    err = mailer.build_message(email_config(), "id-1", "", "", error=True,
                               error_description="boom", call_record_url="s3://calls/a.mp3")
    assert "Файл: s3://calls/a.mp3" in err.get_content()
    assert "Файл:" not in mailer.build_message(email_config(), "id-1", "s", "t").get_body(("plain",)).get_content()
