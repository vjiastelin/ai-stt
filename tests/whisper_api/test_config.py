from whisper_api.config import load_config


def test_defaults():
    cfg = load_config({})
    assert cfg.model == "large-v3"
    assert cfg.device == "cuda"
    assert cfg.compute_type == "float16"  # auto for cuda
    assert cfg.api_key == ""
    assert cfg.port == 8000
    assert cfg.log_level == "INFO"


def test_compute_type_auto_for_cpu():
    assert load_config({"DEVICE": "cpu"}).compute_type == "int8"


def test_explicit_compute_type_wins():
    cfg = load_config({"DEVICE": "cpu", "COMPUTE_TYPE": "float32"})
    assert cfg.compute_type == "float32"


def test_overrides():
    cfg = load_config({"WHISPER_MODEL": "tiny", "DEVICE": "cpu", "PORT": "9001"})
    assert (cfg.model, cfg.device, cfg.port) == ("tiny", "cpu", 9001)
