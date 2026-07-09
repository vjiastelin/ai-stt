"""HTTP API for BPM integration (spec §3.1)."""
import logging
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ai_service.config import ServiceConfig
from ai_service.db import JobStore
from ai_service.s3io import parse_call_record_url

logger = logging.getLogger(__name__)


class TranscriptionRequest(BaseModel):
    CallRecordId: str = Field(
        min_length=1,
        description="Identifier of the «Запись разговора» record in BPM",
        examples=["3fa85f64-5717-4562-b3fc-2c963f66afa6"],
    )
    CallRecordUrl: str = Field(
        min_length=1,
        description="MP3 location: s3://bucket/key.mp3 or a path-style http(s) object URL"
        " (must end in .mp3)",
        examples=["s3://call-records/2026/07/rec-123.mp3"],
    )


class AcceptedResponse(BaseModel):
    status: Literal["accepted"] = "accepted"
    CallRecordId: str


class JobStatusResponse(BaseModel):
    CallRecordId: str
    status: Literal["queued", "processing", "delivering", "done", "failed"]
    attempts: int
    error: str | None
    created_at: str
    updated_at: str


JobState = Literal["queued", "processing", "delivering", "done", "failed"]


class JobResultResponse(BaseModel):
    CallRecordId: str
    status: JobState
    Summary: str = Field(description="Empty until the job reaches delivering/done")
    FullText: str = Field(description="Transcript with [HH:MM:SS] timecodes; empty until processed")


class JobListResponse(BaseModel):
    count: int
    jobs: list[JobStatusResponse]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorResponse(BaseModel):
    detail: str


def _job_status_response(job) -> "JobStatusResponse":
    return JobStatusResponse(
        CallRecordId=job.call_record_id,
        status=job.status,
        attempts=job.attempts,
        error=job.error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def create_app(cfg: ServiceConfig, store: JobStore) -> FastAPI:
    app = FastAPI(
        title="ai-service",
        description="BPM-driven speech-to-text: accepts transcription requests from "
        "BPMSoft and delivers Summary/FullText via callback.",
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error_as_400(request: Request, exc: RequestValidationError):
        # spec §3.1: validation failures are 400, not FastAPI's default 422
        detail = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        return JSONResponse(status_code=400, content={"detail": detail})

    @app.post(
        "/requestTranscription",
        response_model=AcceptedResponse,
        responses={400: {"model": ErrorResponse, "description": "Invalid request"}},
        summary="Queue a call record for transcription (idempotent by CallRecordId)",
    )
    async def request_transcription(payload: TranscriptionRequest):
        call_record_id = payload.CallRecordId.strip()
        call_record_url = payload.CallRecordUrl.strip()
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
        return AcceptedResponse(CallRecordId=call_record_id)

    @app.get(
        "/jobs",
        response_model=JobListResponse,
        responses={400: {"model": ErrorResponse, "description": "Invalid status filter"}},
        summary="List jobs, newest first (diagnostics)",
    )
    def list_jobs(
        status: JobState | None = None,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        jobs = store.list_jobs(status=status, limit=limit, offset=offset)
        return JobListResponse(count=len(jobs), jobs=[_job_status_response(j) for j in jobs])

    @app.get(
        "/jobs/{call_record_id}",
        response_model=JobStatusResponse,
        responses={404: {"model": ErrorResponse, "description": "Unknown CallRecordId"}},
        summary="Job status for diagnostics",
    )
    def job_status(call_record_id: str):
        job = store.get(call_record_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        return _job_status_response(job)

    @app.get(
        "/jobs/{call_record_id}/result",
        response_model=JobResultResponse,
        responses={404: {"model": ErrorResponse, "description": "Unknown CallRecordId"}},
        summary="Transcript (FullText) and Summary of a job",
    )
    def job_result(call_record_id: str):
        job = store.get(call_record_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        return JobResultResponse(
            CallRecordId=job.call_record_id,
            status=job.status,
            Summary=job.summary or "",
            FullText=job.full_text or "",
        )

    @app.get("/healthz", response_model=HealthResponse, summary="Liveness probe")
    def healthz():
        return HealthResponse()

    return app
