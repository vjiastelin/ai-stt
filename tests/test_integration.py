"""Full-chain integration: request → S3 → whisper-api → LLM stub → BPM stub (spec §7)."""
import socket
import threading
import time

import boto3
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from moto import mock_aws

from ai_service.app import create_app as create_service_app
from ai_service.db import JobStore
from ai_service.worker import Worker
from tests.wavgen import write_test_wav
from whisper_api.app import create_app as create_whisper_app
from whisper_api.config import ApiConfig
from whisper_api.engine import EngineResult


class FakeEngine:
    model_name = "fake"

    def transcribe(self, audio_path: str, language: str | None) -> EngineResult:
        return EngineResult(
            language=language or "ru",
            duration=2.0,
            segments=[{"id": 0, "start": 0.0, "end": 2.0, "text": " тестовая запись"}],
            text="тестовая запись",
        )


def _serve(app):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            return server, thread, port
        time.sleep(0.05)
    raise RuntimeError("uvicorn did not start")


def _make_llm_stub():
    app = FastAPI()

    @app.post("/v1/chat/completions")
    def chat():
        return {"choices": [{"message": {"role": "assistant", "content": "Суть звонка."}}]}

    return app


def _make_bpm_stub(received: list):
    app = FastAPI()

    @app.post("/onTranscriptionComplete")
    async def on_complete(payload: dict):
        received.append(payload)
        return {"ok": True}

    return app


def test_full_chain(tmp_path, service_config):
    whisper_app = create_whisper_app(
        ApiConfig(model="fake", device="cpu", compute_type="int8", api_key="", port=0, log_level="INFO"),
        engine_factory=None,
    )
    whisper_app.state.engine = FakeEngine()
    received: list = []
    servers = [_serve(whisper_app), _serve(_make_llm_stub()), _serve(_make_bpm_stub(received))]
    (_, _, whisper_port), (_, _, llm_port), (_, _, bpm_port) = servers
    try:
        with mock_aws():
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket="call-records")
            wav = tmp_path / "rec.wav"
            write_test_wav(wav, seconds=2.0)
            s3.put_object(Bucket="call-records", Key="2026/rec.wav", Body=wav.read_bytes())

            cfg = service_config(
                whisper_api_url=f"http://127.0.0.1:{whisper_port}/v1",
                llm_api_url=f"http://127.0.0.1:{llm_port}/v1",
                bpm_callback_url=f"http://127.0.0.1:{bpm_port}/onTranscriptionComplete",
            )
            store = JobStore(cfg.db_path)
            api = TestClient(create_service_app(cfg, store))

            response = api.post(
                "/requestTranscription",
                json={"CallRecordId": "call-1", "CallRecordUrl": "s3://call-records/2026/rec.wav"},
            )
            assert response.status_code == 200

            worker = Worker(cfg, store, s3)
            worker.run_once()  # process
            worker.run_once()  # deliver

            assert api.get("/jobs/call-1").json()["status"] == "done"
            assert received == [
                {
                    "CallRecordId": "call-1",
                    "Summary": "Суть звонка.",
                    "FullText": "[00:00:00] тестовая запись",
                }
            ]
    finally:
        for server, thread, _ in servers:
            server.should_exit = True
            thread.join(timeout=5)
