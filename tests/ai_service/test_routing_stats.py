import sqlite3

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from ai_service.app import create_app
from ai_service.config import load_config
from ai_service.db import JobStore
from ai_service.routing import parse_routing
from ai_service.worker import Worker
from tests.ai_service.test_config import REQUIRED

ROUTING = parse_routing({
    "default": "time017@aeroclub.team",
    "file_route": [{"name": "Gate", "pattern": "GATE_*", "to": "info@go.gate.ru"}],
    "route": [{"name": "Альянс", "to": "time001@aeroclub.team",
               "client": [{"name": "Байер", "domains": ["bayer.ru"]}]}],
})


def summary(company="не указано", email="не указано"):
    return f"Имя: Иван\nКомпания: {company}\nПочта: {email}\nСуть обращения: …"


@pytest.fixture
def email_cfg(service_config):
    return service_config(smtp_host="smtp", email_from="stt@aeroclub.team",
                          email_to=("fallback@aeroclub.team",), email_routing=ROUTING,
                          email_routes=ROUTING.file_routes, bpm_callback_url="")


def deliver_result(store, cfg, job_id, url, text):
    """Run a stored result through the worker's delivery step with stubbed channels."""
    store.enqueue(job_id, url)
    store.set_result(job_id, "[00:00:00] привет", text)
    Worker(cfg, store, s3_client=None)._deliver(store.get(job_id))


@pytest.fixture
def no_smtp(monkeypatch):
    from ai_service import mailer
    monkeypatch.setattr(mailer, "deliver", lambda *a, **k: None)


def test_routing_file_is_loaded_without_the_email_channel(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text('[[route]]\nname = "Альянс"\nto = "t@x.ru"\ncompanies = ["Байер"]\n',
                    encoding="utf-8")
    cfg = load_config({**REQUIRED, "EMAIL_ROUTING_FILE": str(path)})  # BPM only
    assert not cfg.email_enabled
    assert cfg.email_routing.client_routes[0].name == "Альянс"


@respx.mock
def test_bpm_only_delivery_records_the_team(service_config, tmp_path):
    respx.post(url__regex=r"http://bpm/.*").mock(return_value=httpx.Response(200))
    cfg = service_config(email_routing=ROUTING, email_routes=ROUTING.file_routes)  # BPM on, email off
    store = JobStore(str(tmp_path / "j.db"))
    deliver_result(store, cfg, "id-1", "s3://c/IVR_1.wav", summary("Байер"))
    job = store.get("id-1")
    assert (job.status, job.route, job.route_by, job.emailed_to) == ("done", "Альянс", "company", "")
    assert job.routed_at


def test_email_delivery_records_recipients_and_new_result_resets_them(email_cfg, tmp_path, no_smtp):
    store = JobStore(str(tmp_path / "j.db"))
    deliver_result(store, email_cfg, "id-1", "s3://c/GATE_1.wav", summary("Байер"))
    job = store.get("id-1")
    assert (job.route, job.route_by, job.emailed_to) == ("Gate", "file", "info@go.gate.ru")
    store.set_result("id-1", "t2", "s2")
    assert (store.get("id-1").route_by, store.get("id-1").emailed_to) == ("", "")


def test_failure_is_not_attributed(email_cfg, tmp_path, no_smtp):
    store = JobStore(str(tmp_path / "j.db"))
    store.enqueue("id-1", "s3://c/IVR_1.wav")
    store.set_failed_result("id-1", "corrupt audio")
    Worker(email_cfg, store, s3_client=None)._deliver(store.get("id-1"))
    assert (store.get("id-1").status, store.get("id-1").route_by) == ("failed", "")


def test_stats(email_cfg, tmp_path, no_smtp):
    store = JobStore(str(tmp_path / "j.db"))
    for job_id, url, text in [
        ("a", "s3://c/IVR_a.wav", summary(email="i@bayer.ru")),
        ("b", "s3://c/IVR_b.wav", summary("Байер")),
        ("c", "s3://c/GATE_c.wav", summary()),
        ("d", "s3://c/IVR_d.wav", summary("Ромашка")),
        ("e", "s3://c/IVR_e.wav", summary()),
    ]:
        deliver_result(store, email_cfg, job_id, url, text)
    store.enqueue("q", "s3://c/IVR_q.wav")  # not delivered → not counted

    body = TestClient(create_app(email_cfg, store)).get("/routing/stats?days=7").json()
    assert (body["days"], body["total"]) == (7, 5)
    assert body["by"] == {"domain": 1, "company": 1, "file": 1, "default": 2}
    assert body["routes"] == [
        {"route": "Альянс", "count": 2, "by": {"domain": 1, "company": 1},
         "email_to": ["time001@aeroclub.team"]},
        {"route": "не опознан", "count": 2, "by": {"default": 2},
         "email_to": ["time017@aeroclub.team"]},
        {"route": "Gate", "count": 1, "by": {"file": 1}, "email_to": ["info@go.gate.ru"]},
    ]
    jobs = TestClient(create_app(email_cfg, store)).get("/jobs?status=done").json()["jobs"]
    assert {(j["CallRecordId"], j["route"], j["route_by"]) for j in jobs} >= {("a", "Альянс", "domain")}


def test_preview(email_cfg, tmp_path):
    client = TestClient(create_app(email_cfg, JobStore(str(tmp_path / "j.db"))))
    ok = client.post("/routing/preview", json={"Summary": summary(email="ivan@bayer.ru")}).json()
    assert ok == {"route": "Альянс", "by": "domain", "company": "", "domain": "bayer.ru",
                  "email_to": ["time001@aeroclub.team"]}
    gate = client.post("/routing/preview",
                       json={"CallRecordUrl": "s3://c/GATE_1.wav", "Summary": summary()}).json()
    assert (gate["route"], gate["by"], gate["email_to"]) == ("Gate", "file", ["info@go.gate.ru"])
    assert client.post("/routing/preview", json={"Summary": ""}).status_code == 400


def test_delivery_view_shows_bpm_payload_email_and_routing(service_config, tmp_path, no_smtp):
    cfg = service_config(smtp_host="smtp", email_from="stt@aeroclub.team",
                         email_to=("fallback@aeroclub.team",), email_routing=ROUTING,
                         email_routes=ROUTING.file_routes)  # BPM + email
    store = JobStore(str(tmp_path / "j.db"))
    store.enqueue("id-1", "s3://c/IVRrecord_1_9019988207.wav")
    store.set_result("id-1", "[00:00:00] привет", summary("Байер", "ivan@bayer.ru"))
    store.mark_delivered_to("id-1", "email")
    client = TestClient(create_app(cfg, store))

    body = client.get("/jobs/id-1/delivery").json()
    assert body["sent"] is None  # BPM still pending → not recorded yet
    assert body["current"]["route"] == "Альянс" and body["current"]["by"] == "domain"
    assert body["bpm"]["payload"] == {"Summary": summary("Байер", "ivan@bayer.ru"),
                                      "FullText": "[00:00:00] привет", "Error": False,
                                      "ErrorDescription": ""}
    assert body["bpm"]["url"].endswith("/transcriptions/id-1/result")
    assert (body["bpm"]["delivered"], body["email"]["delivered"]) == (False, True)
    assert body["email"]["to"] == ["time001@aeroclub.team"]
    assert body["email"]["subject"] == "Транскрибация звонка id-1"
    assert "Телефон: 9019988207" in body["email"]["body"]
    assert body["email"]["attachment"] == "id-1.txt"

    store.set_routing("id-1", "Альянс", "domain", "time001@aeroclub.team")
    sent = client.get("/jobs/id-1/delivery").json()["sent"]
    assert (sent["route"], sent["by"], sent["email_to"]) == ("Альянс", "domain", ["time001@aeroclub.team"])


def test_delivery_view_for_failed_and_unprocessed_jobs(email_cfg, tmp_path):
    store = JobStore(str(tmp_path / "j.db"))
    store.enqueue("bad", "s3://c/IVR_b.wav")
    store.set_failed_result("bad", "corrupt audio")
    store.enqueue("new", "s3://c/IVR_n.wav")
    client = TestClient(create_app(email_cfg, store))

    bad = client.get("/jobs/bad/delivery").json()
    assert bad["current"] is None
    assert bad["email"]["subject"] == "Ошибка транскрибации звонка bad"
    assert "corrupt audio" in bad["email"]["body"]
    new = client.get("/jobs/new/delivery").json()
    assert (new["email"], new["bpm"], new["current"]) == (None, None, None)
    assert client.get("/jobs/missing/delivery").status_code == 404


def test_old_database_gets_routing_columns(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE jobs (call_record_id TEXT PRIMARY KEY, call_record_url TEXT NOT NULL,"
        " status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, error TEXT,"
        " full_text TEXT, summary TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
        " delivered_to TEXT NOT NULL DEFAULT '')"
    )
    conn.execute("INSERT INTO jobs VALUES ('a', 's3://b/a.mp3', 'done', 0, NULL, 't', 's',"
                 " '2026-01-01T00:00:00.000000Z', '2026-01-01T00:00:00.000000Z', 'email')")
    conn.commit()
    conn.close()
    store = JobStore(path)
    assert (store.get("a").route, store.get("a").route_by) == ("", "")
    store.set_routing("a", "Альянс", "domain", "t@x.ru")
    assert store.get("a").route == "Альянс"
