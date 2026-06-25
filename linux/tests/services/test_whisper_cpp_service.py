"""
Tests for the whisper.cpp engine service: GPU backend detection, the cmake
build-command builder, the from-source builder, and GGML model status/download.

All tests inject which_fn / shell_fn / downloader / cache_root so no compiler,
GPU, or network is touched.  The per-backend and cross-distro branch-isolation
tests are the important regression guards.
"""
import os

import pytest
from meeting_recorder.services.whisper_cpp_service import (
    WhisperCppBuilder,
    WhisperCppModelDownloader,
    WhisperCppStatusChecker,
    build_cmake_command,
    detect_gpu_backend,
)


def _which_only(name: str):
    return lambda cmd: f"/usr/bin/{cmd}" if cmd == name else None


def _recording_shell(rc: int = 0):
    commands: list[str] = []
    return commands, lambda cmd: commands.append(cmd) or rc


# ── detect_gpu_backend ────────────────────────────────────────────────────────

class TestDetectGpuBackend:
    def test_metal_on_darwin(self):
        assert detect_gpu_backend(
            which_fn=lambda _: None, platform_fn=lambda: "darwin"
        ) == "metal"

    def test_cuda_when_nvidia_smi_present(self):
        assert detect_gpu_backend(
            which_fn=_which_only("nvidia-smi"),
            platform_fn=lambda: "linux",
            path_exists_fn=lambda _p: False,
        ) == "cuda"

    def test_rocm_when_rocminfo_present(self):
        assert detect_gpu_backend(
            which_fn=_which_only("rocminfo"),
            platform_fn=lambda: "linux",
            path_exists_fn=lambda _p: False,
        ) == "rocm"

    def test_rocm_when_kfd_device_present(self):
        assert detect_gpu_backend(
            which_fn=lambda _: None,
            platform_fn=lambda: "linux",
            path_exists_fn=lambda p: p == "/dev/kfd",
        ) == "rocm"

    def test_vulkan_when_vulkaninfo_present(self):
        assert detect_gpu_backend(
            which_fn=_which_only("vulkaninfo"),
            platform_fn=lambda: "linux",
            path_exists_fn=lambda _p: False,
        ) == "vulkan"

    def test_cpu_fallback(self):
        assert detect_gpu_backend(
            which_fn=lambda _: None,
            platform_fn=lambda: "linux",
            path_exists_fn=lambda _p: False,
        ) == "cpu"


# ── build_cmake_command ───────────────────────────────────────────────────────

class TestBuildCmakeCommand:
    def test_cuda_flag(self):
        assert "-DGGML_CUDA=1" in build_cmake_command("cuda")

    def test_rocm_flag(self):
        assert "-DGGML_HIPBLAS=1" in build_cmake_command("rocm")

    def test_vulkan_flag(self):
        assert "-DGGML_VULKAN=1" in build_cmake_command("vulkan")

    def test_metal_flag(self):
        assert "-DGGML_METAL=1" in build_cmake_command("metal")

    def test_cpu_has_no_backend_flag(self):
        cmd = build_cmake_command("cpu")
        assert "GGML_CUDA" not in cmd
        assert "GGML_HIPBLAS" not in cmd
        assert "GGML_VULKAN" not in cmd
        assert "GGML_METAL" not in cmd

    def test_always_builds_release(self):
        assert "cmake --build build" in build_cmake_command("cpu")

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError):
            build_cmake_command("opencl")


# ── WhisperCppBuilder ─────────────────────────────────────────────────────────

class TestWhisperCppBuilderIsBuilt:
    def test_true_when_binary_exists(self, tmp_path):
        binary = tmp_path / "build" / "bin" / "whisper-cli"
        binary.parent.mkdir(parents=True)
        binary.write_text("")
        builder = WhisperCppBuilder(binary_path=binary, home=tmp_path)
        assert builder.is_built() is True

    def test_false_when_binary_absent(self, tmp_path):
        builder = WhisperCppBuilder(binary_path=tmp_path / "missing", home=tmp_path)
        assert builder.is_built() is False


class TestWhisperCppBuilderBuild:
    def _make(self, pm: str = "apt-get", rc: int = 0, home=None):
        cmds, shell = _recording_shell(rc)
        builder = WhisperCppBuilder(
            binary_path=(home / "x") if home else None,
            home=home,
            which_fn=_which_only(pm),
            shell_fn=shell,
        )
        return builder, cmds

    def test_unknown_backend_raises_before_any_command(self, tmp_path):
        builder, cmds = self._make(home=tmp_path)
        with pytest.raises(ValueError):
            builder.build("opencl")
        assert cmds == []

    def test_returns_true_on_success(self, tmp_path):
        builder, _ = self._make(home=tmp_path)
        assert builder.build("cpu") is True

    def test_installs_toolchain_and_builds(self, tmp_path):
        builder, cmds = self._make(home=tmp_path)
        builder.build("cuda")
        joined = "\n".join(cmds)
        assert "cmake" in joined
        assert "-DGGML_CUDA=1" in joined

    def test_clones_repo_when_absent(self, tmp_path):
        builder, cmds = self._make(home=tmp_path)
        builder.build("cpu")
        assert any("git clone" in c for c in cmds)

    def test_skips_clone_when_repo_present(self, tmp_path):
        (tmp_path / ".git").mkdir()
        builder, cmds = self._make(home=tmp_path)
        builder.build("cpu")
        assert not any("git clone" in c for c in cmds)

    def test_returns_false_when_no_package_manager(self, tmp_path):
        cmds, shell = _recording_shell()
        builder = WhisperCppBuilder(
            home=tmp_path, which_fn=lambda _: None, shell_fn=shell
        )
        assert builder.build("cpu") is False
        assert cmds == []

    def test_returns_false_when_build_step_fails(self, tmp_path):
        builder, _ = self._make(rc=1, home=tmp_path)
        assert builder.build("cpu") is False

    def test_returns_false_when_shell_raises(self, tmp_path):
        def boom(_): raise RuntimeError("disk full")
        builder = WhisperCppBuilder(
            home=tmp_path, which_fn=_which_only("apt-get"), shell_fn=boom
        )
        assert builder.build("cpu") is False


# ── WhisperCppStatusChecker / WhisperCppModelDownloader ───────────────────────

class TestWhisperCppStatusChecker:
    def test_true_when_model_file_exists(self, tmp_path):
        (tmp_path / "ggml-small.bin").write_text("")
        checker = WhisperCppStatusChecker(cache_root=tmp_path)
        assert checker.is_cached("small") is True

    def test_false_when_model_file_absent(self, tmp_path):
        checker = WhisperCppStatusChecker(cache_root=tmp_path)
        assert checker.is_cached("small") is False

    def test_model_path_uses_ggml_filename(self, tmp_path):
        checker = WhisperCppStatusChecker(cache_root=tmp_path)
        assert checker.model_path("large-v3").name == "ggml-large-v3.bin"


class TestWhisperCppModelDownloader:
    def test_downloads_correct_url_and_dest(self, tmp_path):
        calls: list[tuple[str, str]] = []
        dl = WhisperCppModelDownloader(
            cache_root=tmp_path,
            downloader=lambda url, dest: calls.append((url, os.path.basename(str(dest)))),
        )
        dl.download("small")
        assert len(calls) == 1
        url, dest_name = calls[0]
        assert url.endswith("ggml-small.bin")
        assert dest_name == "ggml-small.bin"

    def test_propagates_downloader_errors(self, tmp_path):
        def boom(_url, _dest): raise OSError("network down")
        dl = WhisperCppModelDownloader(cache_root=tmp_path, downloader=boom)
        with pytest.raises(OSError):
            dl.download("small")
