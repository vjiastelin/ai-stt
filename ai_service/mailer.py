"""Deliver results by email via Exchange Web Services (exchangelib).

Alternative/extra channel to the BPM callback: the mail is sent from the EMAIL_FROM
mailbox, authenticated as EWS_USERNAME.
"""
import fnmatch
from dataclasses import dataclass
import logging
import re
import urllib.parse
from pathlib import PurePosixPath

import requests
from exchangelib import (
    DELEGATE, IMPERSONATION, Account, Body, Configuration, Credentials, FileAttachment, Mailbox, Message,
)
from exchangelib.errors import EWSError
from exchangelib.protocol import BaseProtocol, NoVerifyHTTPAdapter
from exchangelib.transport import BASIC, DIGEST, NTLM

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
    by: str        # file | domain | company | transcript | default
    company: str   # «Компания:» as recognized ("" if none)
    domain: str    # domain of the client's address ("" if none)
    route: str = ""  # team label: the route's / file rule's name, "" for default


def route_message(
    cfg: ServiceConfig, call_record_url: str, summary: str = "", transcript: str = ""
) -> RoutingDecision:
    """Recipients for a record, first rule that applies:

    1. file-name routes (EMAIL_ROUTES, then the routing file's [[file_route]]);
    2. client routes from the routing file — the client's e-mail domain, then
       company name, as recognized in the summary, then a known domain spelled
       out in the transcript (when the summary lost the address);
    3. the routing file's `default`, else EMAIL_TO.
    """
    company, domain = client_company(summary), client_email_domain(summary)
    if call_record_url:
        name = PurePosixPath(urllib.parse.unquote(urllib.parse.urlparse(call_record_url).path)).name
        names = dict(cfg.email_routing.file_route_names)
        for pattern, recipients in cfg.email_routes:
            if fnmatch.fnmatchcase(name, pattern):
                return RoutingDecision(recipients, "file", company, domain, names.get(pattern, pattern))
    route, by = match_client(cfg.email_routing, summary, transcript)
    if route is not None:
        return RoutingDecision(route.to, by, company, domain, route.name)
    return RoutingDecision(cfg.email_routing.default or cfg.email_to, "default", company, domain)


def recipients_for(
    cfg: ServiceConfig, call_record_url: str, summary: str = "", transcript: str = ""
) -> tuple[str, ...]:
    return route_message(cfg, call_record_url, summary, transcript).to


def _source_lines(call_record_url: str) -> str:
    if not call_record_url:
        return ""
    lines = f"Файл: {call_record_url}"
    phone = phone_from_url(call_record_url)
    if phone:
        lines += f"\nТелефон: {phone}"
    return lines


@dataclass(frozen=True)
class Mail:
    """A message as built from a job, independent of the transport that sends it."""
    sender: str
    to: tuple[str, ...]
    subject: str
    body: str  # plain text
    attachments: tuple[tuple[str, bytes], ...] = ()  # (file name, text/plain content)


def build_message(
    cfg: ServiceConfig,
    call_record_id: str,
    summary: str,
    full_text: str,
    error: bool = False,
    error_description: str = "",
    call_record_url: str = "",
) -> Mail:
    to = recipients_for(cfg, call_record_url, summary, full_text)
    if error:
        return Mail(
            sender=cfg.email_from,
            to=to,
            subject=f"Ошибка транскрибации звонка {call_record_id}",
            body=(
                f"Не удалось транскрибировать запись разговора {call_record_id}.\n"
                + (f"{_source_lines(call_record_url)}\n" if call_record_url else "")
                + f"\nПричина: {error_description}\n"
            ),
        )
    parts = [f"Запись разговора: {call_record_id}"]
    if call_record_url:
        parts[0] += f"\n{_source_lines(call_record_url)}"
    if summary:
        parts.append(f"Краткое содержание:\n{summary}")
    parts.append(f"Транскрипт:\n{full_text}")
    return Mail(
        sender=cfg.email_from,
        to=to,
        subject=f"Транскрибация звонка {call_record_id}",
        body="\n\n".join(parts) + "\n",
        attachments=((f"{call_record_id}.txt", full_text.encode("utf-8")),),
    )


_AUTH_TYPES = {"": None, "ntlm": NTLM, "basic": BASIC, "digest": DIGEST}  # None = autodetect


def _account(cfg: ServiceConfig) -> Account:
    """The EMAIL_FROM mailbox on the configured EWS endpoint (no network I/O yet)."""
    # exchangelib keeps these per process, not per connection; this mailer is its only user
    BaseProtocol.TIMEOUT = cfg.ews_timeout_seconds
    if not cfg.ews_verify_ssl:
        BaseProtocol.HTTP_ADAPTER_CLS = NoVerifyHTTPAdapter
    config = Configuration(
        service_endpoint=cfg.ews_url,
        credentials=Credentials(cfg.ews_username, cfg.ews_password),
        auth_type=_AUTH_TYPES[cfg.ews_auth_type],
        # default FailFast retry policy: the worker owns retries and backoff
    )
    access_type = IMPERSONATION if cfg.ews_access_type == "impersonation" else DELEGATE
    return Account(cfg.email_from, config=config, autodiscover=False, access_type=access_type)


def to_ews_message(account: Account | None, mail: Mail) -> Message:
    message = Message(
        account=account,
        # explicit From: without it a SendOnly message goes out as the logged-in
        # EWS_USERNAME's own mailbox; with it Exchange checks Send As on EMAIL_FROM
        author=Mailbox(email_address=mail.sender),
        subject=mail.subject,
        body=Body(mail.body),
        to_recipients=[Mailbox(email_address=address) for address in mail.to],
    )
    for name, content in mail.attachments:
        message.attach(FileAttachment(name=name, content=content, content_type="text/plain"))
    return message


def send(cfg: ServiceConfig, mail: Mail) -> None:
    to_ews_message(_account(cfg), mail).send(save_copy=cfg.ews_save_copy)


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
        send(cfg, msg)
        logger.info("emailed %s to %s", call_record_id, ", ".join(msg.to))
    except (EWSError, requests.RequestException, OSError) as exc:
        # like the BPM callback: recipients come from config, not the job, so any
        # failure (server down, auth, refused recipient) keeps the job delivering
        raise InfrastructureError(f"email delivery failed: {exc}") from exc
    if not error:
        # result mails only: failure mails carry no summary and would always count as default
        decision = route_message(cfg, call_record_url, summary, full_text)
        metrics.EMAIL_ROUTED.labels(by=decision.by, mailbox=",".join(decision.to)).inc()
        if decision.by == "default":
            logger.info(
                "email routed to default: id=%s company=%r domain=%r",
                call_record_id, decision.company, decision.domain,
            )
