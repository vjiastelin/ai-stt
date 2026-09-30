"""CallRecordUrl parsing and S3 download (spec §3.1, §3.3)."""
import urllib.parse
from pathlib import Path

import boto3
import botocore.exceptions

from ai_service.config import ServiceConfig
from ai_service.errors import InfrastructureError, PermanentJobError


# supported call-record formats: key extension → upload content type
AUDIO_CONTENT_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav"}


def audio_suffix(key: str) -> str:
    """Lower-cased extension of a supported key (".mp3"/".wav"), else ValueError."""
    suffix = Path(key).suffix.lower()
    if suffix not in AUDIO_CONTENT_TYPES:
        raise ValueError(f"unsupported audio format {suffix or '(none)'!r}: {key!r}")
    return suffix


def parse_call_record_url(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "s3":
        bucket, key = parsed.netloc, parsed.path.lstrip("/")
    elif parsed.scheme in ("http", "https"):
        # path-style object URL: host is ignored, configured endpoint is used
        parts = parsed.path.lstrip("/").split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""
    else:
        raise ValueError(f"unsupported CallRecordUrl scheme: {url!r}")
    if not bucket or not key:
        raise ValueError(f"CallRecordUrl must contain bucket and key: {url!r}")
    if Path(key).suffix.lower() not in AUDIO_CONTENT_TYPES:
        # strict allow-list (spec §3.1): recordings are mp3, wav accepted too
        raise ValueError(f"CallRecordUrl must point to an .mp3 or .wav file: {url!r}")
    return bucket, urllib.parse.unquote(key)


def make_client(cfg: ServiceConfig):
    return boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint_url,
        aws_access_key_id=cfg.s3_access_key,
        aws_secret_access_key=cfg.s3_secret_key,
    )


def download(client, bucket: str, key: str, dest_path: Path) -> None:
    try:
        client.download_file(bucket, key, str(dest_path))
    except botocore.exceptions.ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        if 400 <= status < 500:
            raise PermanentJobError(f"cannot download s3://{bucket}/{key}: {exc}") from exc
        raise InfrastructureError(f"S3 error for s3://{bucket}/{key}: {exc}") from exc
    except botocore.exceptions.BotoCoreError as exc:
        raise InfrastructureError(f"S3 unreachable: {exc}") from exc
