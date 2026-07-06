"""FastAPI app: OpenAI-compatible transcription endpoint (spec §4)."""
import logging
import tempfile
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from whisper_api.config import ApiConfig

logger = logging.getLogger(__name__)


def create_app(cfg: ApiConfig, engine_factory: Callable | None) -> FastAPI:
    def _load_engine(app: FastAPI) -> None:
        logger.info("loading model %s on %s (%s)", cfg.model, cfg.device, cfg.compute_type)
        try:
            app.state.engine = engine_factory()
            logger.info("model loaded")
        except Exception:
            logger.exception("model loading failed")
            app.state.load_error = True

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if engine_factory is not None:
            threading.Thread(target=_load_engine, args=(app,), daemon=True).start()
        yield

    app = FastAPI(title="whisper-api", lifespan=lifespan)
    app.state.engine = None
    app.state.load_error = False

    def _check_auth(authorization: str | None) -> None:
        if cfg.api_key and authorization != f"Bearer {cfg.api_key}":
            raise HTTPException(status_code=401, detail="invalid or missing API key")

    def _require_engine():
        if app.state.load_error:
            raise HTTPException(status_code=500, detail="model failed to load")
        if app.state.engine is None:
            raise HTTPException(status_code=503, detail="model loading")
        return app.state.engine

    @app.get("/healthz")
    def healthz():
        _require_engine()
        return {"status": "ok", "model": cfg.model}

    @app.post("/v1/audio/transcriptions")
    async def transcriptions(
        file: UploadFile = File(...),
        model: str = Form(""),
        language: str = Form(""),
        response_format: str = Form("verbose_json"),
        authorization: str | None = Header(None),
    ):
        _check_auth(authorization)
        if response_format != "verbose_json":
            raise HTTPException(
                status_code=422, detail="only response_format=verbose_json is supported"
            )
        engine = _require_engine()
        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="empty or missing audio file")
        with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
            tmp.write(contents)
            tmp.flush()
            try:
                result = await run_in_threadpool(engine.transcribe, tmp.name, language or None)
            except Exception as exc:
                logger.exception("transcription failed")
                raise HTTPException(status_code=500, detail=f"transcription failed: {exc}") from exc
        return {
            "task": "transcribe",
            "language": result.language,
            "duration": result.duration,
            "text": result.text,
            "segments": result.segments,
        }

    return app
