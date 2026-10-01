"""HTTP API for BPM integration (spec §3.1)."""
import logging
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from datetime import datetime, timedelta, timezone

from ai_service import callback, mailer, metrics
from ai_service.config import ServiceConfig
from ai_service.db import JobStore
from ai_service.routing import normalize_company
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
        description="Recording location: s3://bucket/key.mp3 or a path-style http(s) object URL"
        " (must end in .mp3 or .wav)",
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
    route: str = Field("", description="Team the delivered result was attributed to")
    route_by: str = Field("", description="file / domain / company / default; empty until delivered")


JobState = Literal["queued", "processing", "delivering", "done", "failed"]


class JobResultResponse(BaseModel):
    CallRecordId: str
    status: JobState
    Summary: str = Field(description="Empty until the job reaches delivering/done")
    FullText: str = Field(description="Transcript with [HH:MM:SS] timecodes; empty until processed")


class JobListResponse(BaseModel):
    count: int
    jobs: list[JobStatusResponse]


class UnmatchedClient(BaseModel):
    company: str = Field(description="«Компания:» as recognized (first spelling seen)")
    domain: str = Field(description="Domain of the client's address, empty if none")
    count: int
    last_seen: str
    examples: list[str] = Field(description="Up to 3 CallRecordIds, newest first")


class UnmatchedReport(BaseModel):
    days: int
    checked: int = Field(description="Processed jobs with a summary in the window")
    unmatched: int = Field(description="Of those, routed to the default mailbox")
    unrecognized: int = Field(description="Of the unmatched, with neither company nor domain")
    clients: list[UnmatchedClient] = Field(description="Unmatched clients, most calls first")


class RouteInfo(BaseModel):
    route: str = Field(description="Team (route name) the call belongs to; empty = not recognized")
    by: Literal["file", "domain", "company", "default"]
    company: str = Field(description="«Компания:» as recognized in the summary")
    domain: str = Field(description="Domain of the client's address in the summary")
    email_to: list[str] = Field(description="Recipients of the result e-mail (when email is on)")


class SentRouting(BaseModel):
    route: str
    by: str
    email_to: list[str]
    at: str = Field(description="When the delivery was recorded")


class BpmDelivery(BaseModel):
    url: str
    payload: dict = Field(description="Body POSTed to BPM (fixed contract)")
    delivered: bool = Field(description="BPM answered 200 for this result")


class EmailDelivery(BaseModel):
    to: list[str]
    subject: str
    body: str
    attachment: str | None
    delivered: bool = Field(description="The SMTP server accepted this result")


class DeliveryResponse(BaseModel):
    CallRecordId: str
    status: JobState
    sent: SentRouting | None = Field(description="Recorded at delivery: who it actually went to")
    current: RouteInfo | None = Field(description="Where it would go with the current table")
    bpm: BpmDelivery | None = Field(description="null when the BPM channel is off or nothing to send yet")
    email: EmailDelivery | None = Field(description="null when email is off or nothing to send yet")


class PreviewRequest(BaseModel):
    CallRecordUrl: str = Field("", description="Recording path; only its file name is used (file rules)")
    Summary: str = Field(min_length=1, description="Summary text with «Компания:» / «Почта:» lines")


class StatsRoute(BaseModel):
    route: str = Field(description="Team (route name); «не опознан» for the default mailbox")
    count: int
    by: dict[str, int] = Field(description="How the calls were attributed: file / domain / company / default")
    email_to: list[str] = Field(description="Mailboxes these results were e-mailed to")


class StatsResponse(BaseModel):
    days: int
    total: int = Field(description="Delivered results in the window")
    by: dict[str, int] = Field(description="Totals per rule")
    routes: list[StatsRoute] = Field(description="Teams, most calls first")


def _route_info(decision, email_enabled: bool) -> RouteInfo:
    return RouteInfo(
        route=decision.route, by=decision.by, company=decision.company, domain=decision.domain,
        email_to=list(decision.to) if email_enabled else [],
    )


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
        route=job.route,
        route_by=job.route_by,
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
        metrics.JOBS_ENQUEUED.inc()
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

    @app.get(
        "/routing/unmatched",
        response_model=UnmatchedReport,
        summary="Clients whose mail went to the default mailbox (candidates for the routing table)",
    )
    def routing_unmatched(
        days: int = Query(30, ge=1, le=365),
        limit: int = Query(100, ge=1, le=1000),
    ):
        # re-routes stored summaries with the CURRENT table: clients added since drop out
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        jobs = store.list_summaries_since(since)
        groups: dict[tuple[str, str], dict] = {}
        unmatched = unrecognized = 0
        for job in jobs:
            decision = mailer.route_message(cfg, job.call_record_url, job.summary)
            if decision.by != "default":
                continue
            unmatched += 1
            if not decision.company and not decision.domain:
                unrecognized += 1
                continue
            key = (normalize_company(decision.company), decision.domain)
            entry = groups.setdefault(key, {"company": decision.company, "domain": decision.domain,
                                            "count": 0, "last_seen": "", "examples": []})
            entry["count"] += 1
            entry["last_seen"] = max(entry["last_seen"], job.created_at)
            entry["examples"].insert(0, job.call_record_id)
            del entry["examples"][3:]
        clients = sorted(groups.values(), key=lambda e: (-e["count"], e["company"], e["domain"]))
        return UnmatchedReport(
            days=days, checked=len(jobs), unmatched=unmatched, unrecognized=unrecognized,
            clients=[UnmatchedClient(**e) for e in clients[:limit]],
        )

    @app.get(
        "/routing/stats",
        response_model=StatsResponse,
        summary="Delivered results per team and rule (as recorded at delivery, any channel)",
    )
    def routing_stats(days: int = Query(30, ge=1, le=365)):
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        jobs = store.list_routed_since(since)
        totals: dict[str, int] = {}
        routes: dict[str, dict] = {}
        for job in jobs:
            totals[job.route_by] = totals.get(job.route_by, 0) + 1
            name = job.route or "не опознан"
            entry = routes.setdefault(name, {"route": name, "count": 0, "by": {}, "email_to": []})
            entry["count"] += 1
            entry["by"][job.route_by] = entry["by"].get(job.route_by, 0) + 1
            for address in filter(None, job.emailed_to.split(",")):
                if address not in entry["email_to"]:
                    entry["email_to"].append(address)
        ordered = sorted(routes.values(), key=lambda e: (-e["count"], e["route"]))
        return StatsResponse(days=days, total=len(jobs), by=totals,
                             routes=[StatsRoute(**e) for e in ordered])

    @app.post(
        "/routing/preview",
        response_model=RouteInfo,
        responses={400: {"model": ErrorResponse, "description": "Invalid request"}},
        summary="Dry run: which team / mailbox a summary would be routed to",
    )
    def routing_preview(payload: PreviewRequest):
        decision = mailer.route_message(cfg, payload.CallRecordUrl.strip(), payload.Summary)
        return _route_info(decision, cfg.email_enabled)

    @app.get(
        "/jobs/{call_record_id}/delivery",
        response_model=DeliveryResponse,
        responses={404: {"model": ErrorResponse, "description": "Unknown CallRecordId"}},
        summary="What was / would be delivered to BPM and by e-mail, and why",
    )
    def job_delivery(call_record_id: str):
        job = store.get(call_record_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        processed = job.status in ("delivering", "done", "failed") and (
            job.full_text is not None or bool(job.error)
        )
        is_error = processed and job.full_text is None
        summary, full_text = job.summary or "", job.full_text or ""
        error_description = (job.error or "") if is_error else ""
        sent = None
        if job.route_by:
            sent = SentRouting(route=job.route, by=job.route_by,
                               email_to=[a for a in job.emailed_to.split(",") if a], at=job.routed_at)
        current = bpm = email = None
        if processed and not is_error:
            current = _route_info(
                mailer.route_message(cfg, job.call_record_url, summary), cfg.email_enabled
            )
        if processed and cfg.bpm_enabled:
            bpm = BpmDelivery(
                url=callback.result_url(cfg, job.call_record_id),
                payload=callback.build_payload(summary, full_text, is_error, error_description),
                delivered="bpm" in job.delivered_channels,
            )
        if processed and cfg.email_enabled:
            msg = mailer.build_message(cfg, job.call_record_id, summary, full_text,
                                       is_error, error_description, job.call_record_url)
            attachments = [part.get_filename() for part in msg.iter_attachments()]
            email = EmailDelivery(
                to=[a.strip() for a in msg["To"].split(",") if a.strip()],
                subject=msg["Subject"],
                body=(msg.get_body(("plain",)) or msg).get_content(),
                attachment=attachments[0] if attachments else None,
                delivered="email" in job.delivered_channels,
            )
        return DeliveryResponse(CallRecordId=job.call_record_id, status=job.status,
                                sent=sent, current=current, bpm=bpm, email=email)

    @app.get("/healthz", response_model=HealthResponse, summary="Liveness probe")
    def healthz():
        return HealthResponse()

    @app.get("/metrics", summary="Prometheus metrics (text exposition format)")
    def prometheus_metrics():
        content, content_type = metrics.render(store)
        return Response(content=content, media_type=content_type)

    return app
