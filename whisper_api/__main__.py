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
        )

    app = create_app(cfg, engine_factory)
    uvicorn.run(app, host="0.0.0.0", port=cfg.port, log_level=cfg.log_level.lower())


if __name__ == "__main__":
    main()
