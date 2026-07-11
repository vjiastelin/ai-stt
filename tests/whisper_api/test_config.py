import pytest

from whisper_api.config import ConfigError, load_config


def test_defaults():
    cfg = load_config({})
    assert cfg.model == "large-v3"
    assert cfg.device == "cuda"
    assert cfg.compute_type == "float16"  # auto for cuda
    assert cfg.api_key == ""
    assert cfg.port == 8000
    assert cfg.log_level == "INFO"
    assert cfg.transcribe_options == {}
    assert cfg.ssl_certfile == ""
    assert cfg.ssl_keyfile == ""
    assert cfg.ssl_keyfile_password == ""


def test_compute_type_auto_for_cpu():
    assert load_config({"DEVICE": "cpu"}).compute_type == "int8"


def test_explicit_compute_type_wins():
    cfg = load_config({"DEVICE": "cpu", "COMPUTE_TYPE": "float32"})
    assert cfg.compute_type == "float32"


def test_overrides():
    cfg = load_config({"WHISPER_MODEL": "tiny", "DEVICE": "cpu", "PORT": "9001"})
    assert (cfg.model, cfg.device, cfg.port) == ("tiny", "cpu", 9001)


def test_vad_and_conditioning_defaults():
    cfg = load_config({})
    assert cfg.vad_filter is True
    assert cfg.condition_on_previous_text is True


def test_vad_and_conditioning_overrides():
    cfg = load_config({"VAD_FILTER": "false", "CONDITION_ON_PREVIOUS_TEXT": "false"})
    assert cfg.vad_filter is False
    assert cfg.condition_on_previous_text is False


def test_transcribe_options_parsed_from_json():
    cfg = load_config(
        {"TRANSCRIBE_OPTIONS": '{"beam_size": 5, "vad_parameters": {"speech_pad_ms": 100}}'}
    )
    assert cfg.transcribe_options == {"beam_size": 5, "vad_parameters": {"speech_pad_ms": 100}}


def test_transcribe_options_invalid_json_raises():
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config({"TRANSCRIBE_OPTIONS": "{beam_size: 5"})


def test_transcribe_options_non_object_raises():
    with pytest.raises(ConfigError, match="must be a JSON object"):
        load_config({"TRANSCRIBE_OPTIONS": "[1, 2, 3]"})


def test_ssl_overrides():
    cfg = load_config({"SSL_CERTFILE": "/certs/c.pem", "SSL_KEYFILE": "/certs/k.pem"})
    assert cfg.ssl_certfile == "/certs/c.pem"
    assert cfg.ssl_keyfile == "/certs/k.pem"
