"""Deliver results by email over SMTP (alternative/extra channel to the BPM callback)."""
import smtplib
import ssl
from email.message import EmailMessage

from ai_service.config import ServiceConfig
from ai_service.errors import InfrastructureError


def build_message(
    cfg: ServiceConfig,
    call_record_id: str,
    summary: str,
    full_text: str,
    error: bool = False,
    error_description: str = "",
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = cfg.email_from
    msg["To"] = ", ".join(cfg.email_to)
    if error:
        msg["Subject"] = f"Ошибка транскрибации звонка {call_record_id}"
        msg.set_content(
            f"Не удалось транскрибировать запись разговора {call_record_id}.\n\n"
            f"Причина: {error_description}\n"
        )
        return msg
    msg["Subject"] = f"Транскрибация звонка {call_record_id}"
    parts = [f"Запись разговора: {call_record_id}"]
    if summary:
        parts.append(f"Краткое содержание:\n{summary}")
    parts.append(f"Транскрипт:\n{full_text}")
    msg.set_content("\n\n".join(parts) + "\n")
    msg.add_attachment(
        full_text, subtype="plain", charset="utf-8", filename=f"{call_record_id}.txt"
    )
    return msg


def _connect(cfg: ServiceConfig) -> smtplib.SMTP:
    if cfg.smtp_security == "ssl":
        return smtplib.SMTP_SSL(
            cfg.smtp_host, cfg.smtp_port,
            timeout=cfg.smtp_timeout_seconds, context=ssl.create_default_context(),
        )
    client = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.smtp_timeout_seconds)
    if cfg.smtp_security == "starttls":
        client.starttls(context=ssl.create_default_context())
    return client


def deliver(
    cfg: ServiceConfig,
    call_record_id: str,
    summary: str,
    full_text: str,
    error: bool = False,
    error_description: str = "",
) -> None:
    msg = build_message(cfg, call_record_id, summary, full_text, error, error_description)
    try:
        with _connect(cfg) as client:
            if cfg.smtp_username:
                client.login(cfg.smtp_username, cfg.smtp_password)
            client.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        # like the BPM callback: recipients come from config, not the job, so any
        # failure (server down, auth, refused recipient) keeps the job delivering
        raise InfrastructureError(f"email delivery failed: {exc}") from exc
