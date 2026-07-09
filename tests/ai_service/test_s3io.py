import boto3
import pytest
from moto import mock_aws

from ai_service.errors import PermanentJobError
from ai_service.s3io import download, parse_call_record_url


def test_parse_s3_scheme():
    assert parse_call_record_url("s3://call-records/2026/07/rec 1.mp3") == (
        "call-records",
        "2026/07/rec 1.mp3",
    )


def test_parse_path_style_https():
    assert parse_call_record_url("https://minio.example.kz/call-records/2026/rec.mp3") == (
        "call-records",
        "2026/rec.mp3",
    )


def test_parse_accepts_uppercase_extension():
    assert parse_call_record_url("s3://call-records/rec.MP3") == ("call-records", "rec.MP3")


def test_parse_unquotes_percent_encoding():
    assert parse_call_record_url(
        "https://host/bucket/%D0%B7%D0%B0%D0%BF%D0%B8%D1%81%D1%8C.mp3"
    ) == (
        "bucket",
        "запись.mp3",
    )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://x/y.mp3",
        "s3://bucket-only",
        "https://host/bucket-only",
        "not-a-url",
        "",
        # strict MP3-only policy: recordings are always mp3
        "s3://call-records/2026/rec.wav",
        "s3://call-records/2026/rec.ogg",
        "s3://call-records/2026/recording",
    ],
)
def test_parse_rejects_bad_urls(url):
    with pytest.raises(ValueError):
        parse_call_record_url(url)


@pytest.fixture
def s3():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="call-records")
        yield client


def test_download(s3, tmp_path):
    s3.put_object(Bucket="call-records", Key="rec.mp3", Body=b"mp3-bytes")
    dest = tmp_path / "rec.mp3"
    download(s3, "call-records", "rec.mp3", dest)
    assert dest.read_bytes() == b"mp3-bytes"


def test_download_missing_object_is_permanent_error(s3, tmp_path):
    with pytest.raises(PermanentJobError):
        download(s3, "call-records", "missing.mp3", tmp_path / "x.mp3")


def test_download_missing_bucket_is_permanent_error(s3, tmp_path):
    with pytest.raises(PermanentJobError):
        download(s3, "no-such-bucket", "rec.mp3", tmp_path / "x.mp3")
