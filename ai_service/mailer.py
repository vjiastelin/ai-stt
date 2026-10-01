"""Deliver results by email over SMTP (alternative/extra channel to the BPM callback)."""
import fnmatch
from dataclasses import dataclass
import logging
import re
import smtplib
import ssl
import urllib.parse
from email.message import EmailMessage
from pathlib import PurePosixPath

from ai_service.config import ServiceConfig
from ai_service.errors import InfrastructureError
from ai_service import metrics
from ai_service.routing import client_company, client_email_domain, match_client

logger = logging.getLogger(__name__)


# IVR recordings are named <prefix>_<uuid>_<phone>.<ext>, e.g.
# IVRrecord_00000000-01dd-50cc-52ec-24dc00009d25_9019988207.wav → 9019988207
_PHONE_SUFFIX = re.compile(r"_(\d{5,15})$")


def phone_from_url(call_record_url: str) -> str:
    """Caller phone from the digits after the file name's last "_", else ""."""
    path = urllib.parse.unquote(urllib.parse.urlparse(call_record_url).path)
    match = _PHONE_SUFFIX.search(PurePosixPath(path).stem)
    return match.group(1) if match else ""


@dataclass(frozen=True)
class RoutingDecision:
    to: tuple[str, ...]
    by: str        # file | domain | company | default
    company: str   # «Компания:» as recognized ("" if none)
    domain: str    # domain of the client's address ("" if none)
    route: str = ""  # team label: the route's / file rule's name, "" for default


def route_message(cfg: ServiceConfig, call_record_url: str, summary: str = "") -> RoutingDecision:
    """Recipients for a record, first rule that applies:

    1. file-name routes (EMAIL_ROUTES, then the routing file's [[file_route]]);
    2. client routes from the routing file — the client's e-mail domain, then
       company name, as recognized in the summary;
    3. the routing file's `default`, else EMAIL_TO.
    """
    company, domain = client_company(summary), client_email_domain(summary)
    if call_record_url:
        name = PurePosixPath(urllib.parse.unquote(urllib.parse.urlparse(call_record_url).path)).name
        names = dict(cfg.email_routing.file_route_names)
        for pattern, recipients in cfg.email_routes:
            if fnmatch.fnmatchcase(name, pattern):
                return RoutingDecision(recipients, "file", company, domain, names.get(pattern, pattern))
    route, by = match_client(cfg.email_routing, summary)
    if route is not None:
        return RoutingDecision(route.to, by, company, domain, route.name)
    return RoutingDecision(cfg.email_routing.default or cfg.email_to, "default", company, domain)


def recipients_for(
    cfg: ServiceConfig, call_record_url: str, summary: str = ""
) -> tuple[str, ...]:
    return route_message(cfg, call_record_url, summary).to


def _source_lines(call_record_url: str) -> str:
    if not call_record_url:
        return ""
    lines = f"Файл: {call_record_url}"
    phone = phone_from_url(call_record_url)
    if phone:
        lines += f"\nТелефон: {phone}"
    return lines


def build_message(
    cfg: ServiceConfig,
    call_record_id: str,
    summary: str,
    full_text: str,
    error: bool = False,
    error_description: str = "",
    call_record_url: str = "",
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = cfg.email_from
    msg["To"] = ", ".join(recipients_for(cfg, call_record_url, summary))
    if error:
        msg["Subject"] = f"Ошибка транскрибации звонка {call_record_id}"
        msg.set_content(
            f"Не удалось транскрибировать запись разговора {call_record_id}.\n"
            + (f"{_source_lines(call_record_url)}\n" if call_record_url else "")
            + f"\nПричина: {error_description}\n"
        )
        return msg
    msg["Subject"] = f"Транскрибация звонка {call_record_id}"
    parts = [f"Запись разговора: {call_record_id}"]
    if call_record_url:
        parts[0] += f"\n{_source_lines(call_record_url)}"
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
    call_record_url: str = "",
) -> None:
    msg = build_message(
        cfg, call_record_id, summary, full_text, error, error_description, call_record_url
    )
    try:
        with _connect(cfg) as client:
            if cfg.smtp_username:
                client.login(cfg.smtp_username, cfg.smtp_password)
            client.send_message(msg)
        logger.info("emailed %s to %s", call_record_id, msg["To"])
    except (smtplib.SMTPException, OSError) as exc:
        # like the BPM callback: recipients come from config, not the job, so any
        # failure (server down, auth, refused recipient) keeps the job delivering
        raise InfrastructureError(f"email delivery failed: {exc}") from exc
    if not error:
        # result mails only: failure mails carry no summary and would always count as default
        decision = route_message(cfg, call_record_url, summary)
        metrics.EMAIL_ROUTED.labels(by=decision.by, mailbox=",".join(decision.to)).inc()
        if decision.by == "default":
            logger.info(
                "email routed to default: id=%s company=%r domain=%r",
                call_record_id, decision.company, decision.domain,
            )
