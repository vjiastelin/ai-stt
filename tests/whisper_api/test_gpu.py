from whisper_api.gpu import describe_gpu


def _proc(tmp_path, version: str | None, gpus: dict[str, str]):
    if version is not None:
        (tmp_path / "version").write_text(version)
    for bus_id, info in gpus.items():
        gpu_dir = tmp_path / "gpus" / bus_id
        gpu_dir.mkdir(parents=True)
        (gpu_dir / "information").write_text(info)
    return tmp_path


def test_proprietary_driver_and_gpu_model(tmp_path):
    proc = _proc(
        tmp_path,
        "NVRM version: NVIDIA UNIX x86_64 Kernel Module  535.113.01  Tue Sep 12 19:41:24 UTC 2023\n"
        "GCC version:  gcc version 11.4.0\n",
        {"0000:01:00.0": "Model: \t\t NVIDIA GeForce RTX 3090\nIRQ:   \t\t 130\n"},
    )
    assert describe_gpu(proc) == "NVIDIA GeForce RTX 3090; driver 535.113.01"


def test_open_kernel_module_and_several_gpus(tmp_path):
    proc = _proc(
        tmp_path,
        "NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  570.211.01  Release Build\n",
        {
            "0000:02:00.0": "Model: \t\t NVIDIA H100 80GB HBM3\n",
            "0000:01:00.0": "Model: \t\t NVIDIA H100 80GB HBM3\n",
        },
    )
    assert describe_gpu(proc) == "NVIDIA H100 80GB HBM3, NVIDIA H100 80GB HBM3; driver 570.211.01"


def test_no_driver_proc_entries(tmp_path):
    assert describe_gpu(tmp_path / "missing").startswith("no NVIDIA driver info")


def test_unparsable_version_and_no_gpus(tmp_path):
    proc = _proc(tmp_path, "something else\n", {})
    assert describe_gpu(proc) == "no GPUs listed; driver unknown"
