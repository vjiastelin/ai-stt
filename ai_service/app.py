"""HTTP API for BPM integration (spec §3.1)."""
import logging

from fastapi import FastAPI, HTTPException, Request

from ai_service.config import ServiceConfig
from ai_service.db import JobStore
from ai_service.s3io import parse_call_record_url

logger = logging.getLogger(__name__)


def create_app(cfg: ServiceConfig, store: JobStore) -> FastAPI:
    app = FastAPI(title="ai-service")

    @app.post("/requestTranscription")
    async def request_transcription(request: Request):
        # manual parsing so validation errors are 400 (spec), not FastAPI's 422
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="request body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="request body must be a JSON object")
        call_record_id = str(body.get("CallRecordId") or "").strip()
        call_record_url = str(body.get("CallRecordUrl") or "").strip()
        if not call_record_id or not call_record_url:
            raise HTTPException(
                status_code=400, detail="CallRecordId and CallRecordUrl are required"
            )
        try:
            parse_call_record_url(call_record_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        job = store.enqueue(call_record_id, call_record_url)
        logger.info("accepted %s (status=%s)", call_record_id, job.status)
        return {"status": "accepted", "CallRecordId": call_record_id}

    @app.get("/jobs/{call_record_id}")
    def job_status(call_record_id: str):
        job = store.get(call_record_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        return {
            "CallRecordId": job.call_record_id,
            "status": job.status,
            "attempts": job.attempts,
            "error": job.error,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app
