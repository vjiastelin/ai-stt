import logging
import os

import uvicorn

from whisper_api.app import create_app
from whisper_api.config import load_config


def main() -> None:
    cfg = load_config()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one INFO line per hub request

    # per-request read timeout of the hub client: a dead connection is retried
    # instead of hanging forever (read when huggingface_hub is first imported)
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
    # plain HTTP instead of Xet: resumable .incomplete files whose growth the progress
    # log can see (Xet writes the file only at the end, and stalled on flaky hosts);
    # set HF_HUB_DISABLE_XET=0 to use Xet again
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

    def engine_factory(status):
        from whisper_api.download import ensure_model
        from whisper_api.engine import Engine

        model_path = ensure_model(
            cfg.model,
            retries=cfg.download_retries,
            deadline_seconds=cfg.download_timeout_seconds,
            status=status,
        )
        status("loading model into memory")
        return Engine(
            model_path,
            cfg.device,
            cfg.compute_type,
            vad_filter=cfg.vad_filter,
            condition_on_previous_text=cfg.condition_on_previous_text,
            transcribe_options=cfg.transcribe_options,
        )

    app = create_app(cfg, engine_factory)
    tls_enabled = bool(cfg.ssl_certfile and cfg.ssl_keyfile)
    logging.getLogger(__name__).info(
        "serving on 0.0.0.0:%d (%s)", cfg.port, "https" if tls_enabled else "http"
    )
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=cfg.port,
        log_level=cfg.log_level.lower(),
        ssl_certfile=cfg.ssl_certfile or None,
        ssl_keyfile=cfg.ssl_keyfile or None,
        ssl_keyfile_password=cfg.ssl_keyfile_password or None,
    )


if __name__ == "__main__":
    main()
