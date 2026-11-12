import pytest
import requests
from exchangelib import Body, FileAttachment
from exchangelib.errors import ErrorServerBusy, TransportError, UnauthorizedError
from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter
from exchangelib.transport import NTLM

from ai_service import mailer
from ai_service.errors import InfrastructureError


class FakeOutbox(list):
    """Stands in for mailer.send (the EWS call); records Mail objects, or raises `fail_with`."""

    fail_with: Exception | None = None

    def __call__(self, cfg, mail):
        if self.fail_with is not None:
            raise self.fail_with
        self.append(mail)


@pytest.fixture
def fake_outbox(monkeypatch):
    outbox = FakeOutbox()
    monkeypatch.setattr(mailer, "send", outbox)
    return outbox


@pytest.fixture
def email_config(service_config):
    def make(**overrides):
        base = dict(
            ews_url="https://mail.example.kz/EWS/Exchange.asmx",
            ews_username="EXAMPLE\\stt",
            ews_password="pw",
            email_from="stt@example.kz",
            email_to=("ops@example.kz", "qa@example.kz"),
        )
        base.update(overrides)
        return service_config(**base)

    return make


def test_success_message_has_summary_transcript_and_attachment(email_config):
    msg = mailer.build_message(email_config(), "id-1", "суть", "[00:00:00] привет")

    assert msg.sender == "stt@example.kz"
    assert msg.to == ("ops@example.kz", "qa@example.kz")
    assert msg.subject == "Транскрибация звонка id-1"
    assert "Краткое содержание:\nсуть" in msg.body
    assert "[00:00:00] привет" in msg.body
    assert msg.attachments == (("id-1.txt", "[00:00:00] привет".encode("utf-8")),)


def test_success_message_without_summary_omits_section(email_config):
    body = mailer.build_message(email_config(), "id-1", "", "t").body
    assert "Краткое содержание" not in body


def test_error_message_carries_reason(email_config):
    msg = mailer.build_message(
        email_config(), "id-1", "", "", error=True, error_description="corrupt audio"
    )
    assert msg.subject == "Ошибка транскрибации звонка id-1"
    assert "corrupt audio" in msg.body
    assert msg.attachments == ()


def test_deliver_sends_the_built_message(email_config, fake_outbox):
    mailer.deliver(email_config(), "id-1", "s", "t")
    [mail] = fake_outbox
    assert (mail.subject, mail.to) == ("Транскрибация звонка id-1", ("ops@example.kz", "qa@example.kz"))


@pytest.mark.parametrize(
    "exc",
    [
        TransportError("connection refused"),
        UnauthorizedError("bad creds"),
        ErrorServerBusy("throttled"),
        requests.ConnectionError("down"),
        TimeoutError("slow"),
    ],
)
def test_exchange_failures_are_infrastructure_errors(email_config, fake_outbox, exc):
    fake_outbox.fail_with = exc
    with pytest.raises(InfrastructureError):
        mailer.deliver(email_config(), "id-1", "s", "t")
    assert fake_outbox == []


def test_ews_message_carries_recipients_body_and_attachment(email_config):
    mail = mailer.build_message(email_config(), "id-1", "суть", "[00:00:00] привет")
    message = mailer.to_ews_message(None, mail)
    assert message.subject == "Транскрибация звонка id-1"
    assert [r.email_address for r in message.to_recipients] == ["ops@example.kz", "qa@example.kz"]
    assert isinstance(message.body, Body) and "суть" in message.body
    [attachment] = message.attachments
    assert isinstance(attachment, FileAttachment)
    assert (attachment.name, attachment.content_type) == ("id-1.txt", "text/plain")
    assert attachment.content.decode("utf-8") == "[00:00:00] привет"


def test_ews_message_without_attachment_for_failures(email_config):
    mail = mailer.build_message(email_config(), "id-1", "", "", error=True, error_description="x")
    assert mailer.to_ews_message(None, mail).attachments == []


@pytest.fixture
def restore_protocol():
    saved = BaseProtocol.TIMEOUT, BaseProtocol.HTTP_ADAPTER_CLS
    yield
    BaseProtocol.TIMEOUT, BaseProtocol.HTTP_ADAPTER_CLS = saved


def test_account_targets_the_sender_mailbox_on_the_endpoint(email_config, restore_protocol):
    account = mailer._account(email_config(ews_auth_type="ntlm", ews_timeout_seconds=7))
    assert account.primary_smtp_address == "stt@example.kz"
    assert account.protocol.service_endpoint == "https://mail.example.kz/EWS/Exchange.asmx"
    assert account.protocol.auth_type == NTLM
    assert account.protocol.credentials.username == "EXAMPLE\\stt"
    assert BaseProtocol.TIMEOUT == 7
    assert BaseProtocol.HTTP_ADAPTER_CLS is not NoVerifyHTTPAdapter


def test_account_can_skip_tls_verification(email_config, restore_protocol):
    mailer._account(email_config(ews_verify_ssl=False))
    assert BaseProtocol.HTTP_ADAPTER_CLS is NoVerifyHTTPAdapter


def test_send_uses_save_copy_setting(email_config, monkeypatch):
    calls = []
    monkeypatch.setattr(mailer, "_account", lambda cfg: None)
    monkeypatch.setattr(mailer.Message, "send", lambda self, save_copy: calls.append(save_copy))
    mail = mailer.build_message(email_config(), "id-1", "s", "t")
    mailer.send(email_config(), mail)
    mailer.send(email_config(ews_save_copy=True), mail)
    assert calls == [False, True]


def test_source_file_shown_when_known(email_config):
    ok = mailer.build_message(email_config(), "id-1", "s", "t", call_record_url="s3://calls/a.mp3")
    assert "Файл: s3://calls/a.mp3" in ok.body
    err = mailer.build_message(email_config(), "id-1", "", "", error=True,
                               error_description="boom", call_record_url="s3://calls/a.mp3")
    assert "Файл: s3://calls/a.mp3" in err.body
    assert "Файл:" not in mailer.build_message(email_config(), "id-1", "s", "t").body


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
    assert f"Файл: {url}\nТелефон: 9019988207\n" in ok.body
    err = mailer.build_message(email_config(), "id-1", "", "", error=True,
                               error_description="boom", call_record_url=url)
    assert f"Файл: {url}\nТелефон: 9019988207\n" in err.body
    plain = mailer.build_message(email_config(), "id-1", "s", "t", call_record_url="s3://c/rec.wav")
    assert "Телефон" not in plain.body


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
    assert mailer.build_message(cfg, "id-1", "s", "t", call_record_url=url).to == (to,)


def test_first_matching_route_wins_and_failures_are_routed_too(email_config):
    cfg = email_config(email_to=("default@x.ru",), email_routes=(
        ("GATE_*", ("a@x.ru", "b@x.ru")), ("GATE_IVRrecord_*", ("never@x.ru",))))
    url = "s3://calls/GATE_IVRrecord_1_9019988207.wav"
    assert mailer.recipients_for(cfg, url) == ("a@x.ru", "b@x.ru")
    err = mailer.build_message(cfg, "id-1", "", "", error=True, error_description="boom",
                               call_record_url=url)
    assert err.to == ("a@x.ru", "b@x.ru")


def test_routed_message_is_sent_to_route_recipients(email_config, fake_outbox):
    cfg = email_config(email_to=("time017@aeroclub.team",), email_routes=ROUTES)
    mailer.deliver(cfg, "id-1", "s", "t", call_record_url="s3://c/GATE_IVRrecord_1_9019988207.wav")
    [mail] = fake_outbox
    assert mail.to == ("info@go.gate.ru",)
