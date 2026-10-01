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


@pytest.mark.parametrize(
    "url,phone",
    [
        ("s3://calls/in/IVRrecord_00000000-01dd-50cc-52ec-24dc00009d25_9019988207.wav",
         "9019988207"),
        ("https://minio/calls/IVRrecord_abc_79019988207.MP3", "79019988207"),
        ("s3://calls/IVRrecord_abc_901%20998.wav", ""),  # not all digits
        ("s3://calls/in_9019988207/rec.wav", ""),         # digits in a folder, not the file
        ("s3://calls/rec.wav", ""),                        # no "_"
        ("s3://calls/rec_12.wav", ""),                     # too short to be a phone
        ("", ""),
    ],
)
def test_phone_from_url(url, phone):
    assert mailer.phone_from_url(url) == phone


def test_phone_shown_after_file_path(email_config):
    url = "s3://calls/IVRrecord_00000000-01dd-50cc-52ec-24dc00009d25_9019988207.wav"
    ok = mailer.build_message(email_config(), "id-1", "s", "t", call_record_url=url)
    assert f"Файл: {url}\nТелефон: 9019988207\n" in ok.get_body(("plain",)).get_content()
    err = mailer.build_message(email_config(), "id-1", "", "", error=True,
                               error_description="boom", call_record_url=url)
    assert f"Файл: {url}\nТелефон: 9019988207\n" in err.get_content()
    plain = mailer.build_message(email_config(), "id-1", "s", "t", call_record_url="s3://c/rec.wav")
    assert "Телефон" not in plain.get_body(("plain",)).get_content()


ROUTES = (
    ("AWAD_IVRrecord_*", ("anywayanyday-info-gate@yandex.ru",)),
    ("GATE_IVRrecord_*", ("info@go.gate.ru",)),
)


@pytest.mark.parametrize(
    "url,to",
    [
        ("s3://calls/in/AWAD_IVRrecord_0000-01dd_9019988207.wav", "anywayanyday-info-gate@yandex.ru"),
        ("s3://calls/GATE_IVRrecord_0000-01dd_9019988207.mp3", "info@go.gate.ru"),
        ("s3://calls/IVRrecord_0000-01dd_9019988207.wav", "time017@aeroclub.team"),
        ("s3://calls/AWAD_IVRrecord/IVRrecord_1_9019988207.wav", "time017@aeroclub.team"),  # folder, not file
        ("s3://calls/awad_IVRrecord_1_9019988207.wav", "time017@aeroclub.team"),  # case-sensitive
        ("https://minio/calls/x/GATE_IVRrecord_%231_9019988207.wav", "info@go.gate.ru"),
        ("", "time017@aeroclub.team"),  # BPM request without a URL → default
    ],
)
def test_recipients_routed_by_file_name(email_config, url, to):
    cfg = email_config(email_to=("time017@aeroclub.team",), email_routes=ROUTES)
    assert mailer.recipients_for(cfg, url) == (to,)
    assert mailer.build_message(cfg, "id-1", "s", "t", call_record_url=url)["To"] == to


def test_first_matching_route_wins_and_failures_are_routed_too(email_config):
    cfg = email_config(email_to=("default@x.ru",), email_routes=(
        ("GATE_*", ("a@x.ru", "b@x.ru")), ("GATE_IVRrecord_*", ("never@x.ru",))))
    url = "s3://calls/GATE_IVRrecord_1_9019988207.wav"
    assert mailer.recipients_for(cfg, url) == ("a@x.ru", "b@x.ru")
    err = mailer.build_message(cfg, "id-1", "", "", error=True, error_description="boom",
                               call_record_url=url)
    assert err["To"] == "a@x.ru, b@x.ru"


def test_routed_message_is_sent_to_route_recipients(email_config, fake_smtp):
    cfg = email_config(email_to=("time017@aeroclub.team",), email_routes=ROUTES)
    mailer.deliver(cfg, "id-1", "s", "t", call_record_url="s3://c/GATE_IVRrecord_1_9019988207.wav")
    [client] = fake_smtp.instances
    assert client.sent[0]["To"] == "info@go.gate.ru"
