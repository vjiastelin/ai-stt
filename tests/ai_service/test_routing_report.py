import logging

import pytest
from fastapi.testclient import TestClient

from ai_service import mailer, metrics
from ai_service.app import create_app
from ai_service.db import JobStore
from ai_service.routing import parse_routing
from tests.ai_service.test_mailer import FakeSMTP

ROUTING = parse_routing({
    "default": "time017@aeroclub.team",
    "file_route": [{"pattern": "GATE_*", "to": "info@go.gate.ru"}],
    "route": [{"to": "time001@aeroclub.team",
               "client": [{"name": "Байер", "domains": ["bayer.ru"]}]}],
})


def summary(company="не указано", email="не указано"):
    return f"Имя: Иван\nКомпания: {company}\nПочта: {email}\nСуть обращения: …"


@pytest.fixture
def cfg(service_config):
    return service_config(smtp_host="smtp", email_from="stt@aeroclub.team",
                          email_to=("fallback@aeroclub.team",), email_routing=ROUTING,
                          email_routes=ROUTING.file_routes)


@pytest.mark.parametrize("url,company,email,by", [
    ("s3://c/GATE_1.wav", "Байер", "a@bayer.ru", "file"),
    ("s3://c/IVR_1.wav", "не указано", "a@bayer.ru", "domain"),
    ("s3://c/IVR_1.wav", "Байер", "не указано", "company"),
    ("s3://c/IVR_1.wav", "Ромашка", "a@romashka.ru", "default"),
])
def test_route_message_reports_the_rule(cfg, url, company, email, by):
    assert mailer.route_message(cfg, url, summary(company, email)).by == by


def _counter(by):
    return metrics.EMAIL_ROUTED.labels(by=by)._value.get()


def test_sent_result_mail_is_counted_and_default_is_logged(cfg, monkeypatch, caplog):
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.instances, FakeSMTP.fail_with = [], None
    before = {by: _counter(by) for by in ("domain", "default")}
    with caplog.at_level(logging.INFO, logger="ai_service.mailer"):
        mailer.deliver(cfg, "id-1", summary(email="a@bayer.ru"), "t", call_record_url="s3://c/IVR_1.wav")
        mailer.deliver(cfg, "id-2", summary("Ромашка", "a@romashka.ru"), "t",
                       call_record_url="s3://c/IVR_2.wav")
        mailer.deliver(cfg, "id-3", "", "", error=True, error_description="boom",
                       call_record_url="s3://c/IVR_3.wav")  # failure mail: not counted
    assert _counter("domain") - before["domain"] == 1
    assert _counter("default") - before["default"] == 1
    assert "email routed to default: id=id-2 company='Ромашка' domain='romashka.ru'" in caplog.text
    assert "id=id-3" not in caplog.text


def test_failed_send_is_not_counted(cfg, monkeypatch):
    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.instances, FakeSMTP.fail_with = [], ConnectionRefusedError("down")
    before = _counter("domain")
    with pytest.raises(Exception):
        mailer.deliver(cfg, "id-1", summary(email="a@bayer.ru"), "t", call_record_url="s3://c/I.wav")
    FakeSMTP.fail_with = None
    assert _counter("domain") == before


def test_unmatched_report(cfg, tmp_path):
    store = JobStore(str(tmp_path / "r.db"))
    jobs = {
        "known": summary("Байер", "a@bayer.ru"),
        "gate": summary("Ромашка", "a@romashka.ru"),          # file route → not unmatched
        "r1": summary("Ромашка", "a@romashka.ru"),
        "r2": summary("ООО «Ромашка»", "b@romashka.ru"),      # same client, other spelling
        "x": summary("не указано", "c@xmail.ru"),
        "nobody": summary(),
    }
    for job_id, text in jobs.items():
        url = "s3://c/GATE_1.wav" if job_id == "gate" else f"s3://c/IVR_{job_id}.wav"
        store.enqueue(job_id, url)
        store.set_result(job_id, "[00:00:00] …", text)
    store.enqueue("queued", "s3://c/IVR_q.wav")  # no summary yet → not checked

    body = TestClient(create_app(cfg, store)).get("/routing/unmatched?days=7").json()
    assert (body["days"], body["checked"], body["unmatched"], body["unrecognized"]) == (7, 6, 4, 1)
    assert [(c["company"], c["domain"], c["count"]) for c in body["clients"]] == [
        ("Ромашка", "romashka.ru", 2),   # grouped by normalized company + domain
        ("", "xmail.ru", 1),
    ]
    assert body["clients"][0]["examples"] == ["r2", "r1"]
    assert body["clients"][0]["company"] == "Ромашка"  # first spelling seen


def test_unmatched_report_validates_params(cfg, tmp_path):
    client = TestClient(create_app(cfg, JobStore(str(tmp_path / "r.db"))))
    assert client.get("/routing/unmatched?days=0").status_code == 400
    assert client.get("/routing/unmatched").json()["clients"] == []
