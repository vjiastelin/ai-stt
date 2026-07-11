import logging

import uvicorn

from whisper_api.app import create_app
from whisper_api.config import load_config


def main() -> None:
    cfg = load_config()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    def engine_factory():
        from whisper_api.engine import Engine

        return Engine(
            cfg.model,
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
