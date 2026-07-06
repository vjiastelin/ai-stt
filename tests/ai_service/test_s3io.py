import boto3
import pytest
from moto import mock_aws

from ai_service.errors import PermanentJobError
from ai_service.s3io import download, parse_call_record_url


def test_parse_s3_scheme():
    assert parse_call_record_url("s3://call-records/2026/07/rec 1.wav") == (
        "call-records",
        "2026/07/rec 1.wav",
    )


def test_parse_path_style_https():
    assert parse_call_record_url("https://minio.example.kz/call-records/2026/rec.wav") == (
        "call-records",
        "2026/rec.wav",
    )


def test_parse_unquotes_percent_encoding():
    assert parse_call_record_url("https://host/bucket/%D0%B7%D0%B0%D0%BF%D0%B8%D1%81%D1%8C.wav") == (
        "bucket",
        "запись.wav",
    )


@pytest.mark.parametrize(
    "url",
    ["ftp://x/y.wav", "s3://bucket-only", "https://host/bucket-only", "not-a-url", ""],
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
    s3.put_object(Bucket="call-records", Key="rec.wav", Body=b"wav-bytes")
    dest = tmp_path / "rec.wav"
    download(s3, "call-records", "rec.wav", dest)
    assert dest.read_bytes() == b"wav-bytes"


def test_download_missing_object_is_permanent_error(s3, tmp_path):
    with pytest.raises(PermanentJobError):
        download(s3, "call-records", "missing.wav", tmp_path / "x.wav")


def test_download_missing_bucket_is_permanent_error(s3, tmp_path):
    with pytest.raises(PermanentJobError):
        download(s3, "no-such-bucket", "rec.wav", tmp_path / "x.wav")
