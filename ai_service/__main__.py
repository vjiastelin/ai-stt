import logging
import threading

import uvicorn

from ai_service.app import create_app
from ai_service.config import load_config
from ai_service.db import JobStore
from ai_service.s3io import make_client
from ai_service.scanner import Scanner
from ai_service.worker import Worker


def main() -> None:
    cfg = load_config()
    logging.basicConfig(
        level=cfg.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger(__name__).info(
        "starting ai-service: prompts=%s whisper=%s summary=%s llm=%s callback=%s email=%s db=%s",
        cfg.prompt_profile, cfg.whisper_api_url, cfg.summary_enabled, cfg.llm_api_url or "-",
        cfg.bpm_callback_url or "-",
        ",".join(cfg.email_to) if cfg.email_enabled else "-", cfg.db_path,
    )
    for pattern, recipients in cfg.email_routes:
        logging.getLogger(__name__).info("email route: %s → %s", pattern, ",".join(recipients))
    routing = cfg.email_routing
    if routing.client_routes or routing.default:
        logging.getLogger(__name__).info(
            "email routing file: %d client route(s), %d domain(s), %d compan(y/ies), default %s",
            len(routing.client_routes), sum(len(r.domains) for r in routing.client_routes),
            sum(len(r.companies) for r in routing.client_routes),
            ",".join(routing.default) or "EMAIL_TO",
        )
    for name, verify in (("WHISPER", cfg.whisper_verify_ssl), ("LLM", cfg.llm_verify_ssl)):
        if not verify:
            logging.getLogger(__name__).warning(
                "%s_VERIFY_SSL=false: TLS certificates of the %s endpoint are NOT verified",
                name, name.lower(),
            )
    store = JobStore(cfg.db_path)
    worker = Worker(cfg, store, make_client(cfg))
    threading.Thread(target=worker.run_forever, name="worker", daemon=True).start()
    if cfg.s3_scan_enabled:
        logging.getLogger(__name__).info(
            "S3 scanner: s3://%s/%s every %ds (modified after %s)",
            cfg.s3_scan_bucket, cfg.s3_scan_prefix, cfg.s3_scan_interval_seconds,
            cfg.s3_scan_modified_after.isoformat() if cfg.s3_scan_modified_after else "-",
        )
        scanner = Scanner(cfg, store, make_client(cfg))
        threading.Thread(target=scanner.run_forever, name="s3-scanner", daemon=True).start()
    app = create_app(cfg, store)
    uvicorn.run(app, host="0.0.0.0", port=cfg.port, log_level=cfg.log_level.lower())


if __name__ == "__main__":
    main()
