import logging
import threading

import uvicorn

from ai_service.app import create_app
from ai_service.config import load_config
from ai_service.db import JobStore
from ai_service.s3io import make_client
from ai_service.worker import Worker


def main() -> None:
    cfg = load_config()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger(__name__).info(
        "starting ai-service: whisper=%s summary=%s llm=%s callback=%s db=%s",
        cfg.whisper_api_url, cfg.summary_enabled, cfg.llm_api_url or "-",
        cfg.bpm_callback_url, cfg.db_path,
    )
    store = JobStore(cfg.db_path)
    worker = Worker(cfg, store, make_client(cfg))
    threading.Thread(target=worker.run_forever, name="worker", daemon=True).start()
    app = create_app(cfg, store)
    uvicorn.run(app, host="0.0.0.0", port=cfg.port, log_level=cfg.log_level.lower())


if __name__ == "__main__":
    main()
