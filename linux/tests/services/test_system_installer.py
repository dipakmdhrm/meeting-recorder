"""
Tests for OllamaInstaller and CudaInstaller.

All tests use the injected which_fn / shell_fn / popen_fn hooks so no real
shell commands are executed.  The cross-distro branch isolation tests are the
most important ones: they ensure that changing the apt-get path cannot
silently break the dnf or pacman path and vice-versa.
"""
import os

import pytest
from meeting_recorder.services.system_installer import (
    CudaInstaller,
    OllamaInstaller,
    RocmInstaller,
    WhisperEngineInstaller,
    detect_gpu_vendor,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _which_only(pm: str):
    """Return a which_fn that only recognises one package manager."""
    return lambda cmd: f"/usr/bin/{cmd}" if cmd == pm else None


def _recording_shell(rc: int = 0):
    """Return (commands_list, shell_fn) that records every command run."""
    commands: list[str] = []
    return commands, lambda cmd: commands.append(cmd) or rc


class FakePipe:
    """Simulates os.popen("rpm -E %fedora").read()."""
    def __init__(self, output: str = "41"):
        self._output = output

    def read(self) -> str:
        return self._output


# ── OllamaInstaller ───────────────────────────────────────────────────────────

class TestOllamaInstallerIsAvailable:
    def test_true_when_ollama_found(self):
        inst = OllamaInstaller(which_fn=lambda _: "/usr/bin/ollama")
        assert inst.is_available() is True

    def test_false_when_ollama_missing(self):
        inst = OllamaInstaller(which_fn=lambda _: None)
        assert inst.is_available() is False


class TestOllamaInstallerInstall:
    def test_returns_true_on_zero_exit(self):
        inst = OllamaInstaller(shell_fn=lambda _: 0)
        assert inst.install() is True

    def test_returns_false_on_nonzero_exit(self):
        inst = OllamaInstaller(shell_fn=lambda _: 1)
        assert inst.install() is False

    def test_returns_false_on_exception(self):
        def boom(_): raise OSError("network error")
        inst = OllamaInstaller(shell_fn=boom)
        assert inst.install() is False


# ── CudaInstaller.is_available ────────────────────────────────────────────────

class TestCudaInstallerIsAvailable:
    def test_true_when_nvidia_smi_present(self):
        inst = CudaInstaller(which_fn=lambda cmd: "/usr/bin/nvidia-smi" if cmd == "nvidia-smi" else None)
        assert inst.is_available() is True

    def test_false_when_nvidia_smi_absent(self):
        inst = CudaInstaller(which_fn=lambda _: None)
        assert inst.is_available() is False


# ── CudaInstaller – apt-get branch ───────────────────────────────────────────

class TestCudaInstallerAptBranch:
    def _make(self, rc: int = 0):
        cmds, shell = _recording_shell(rc)
        return CudaInstaller(which_fn=_which_only("apt-get"), shell_fn=shell), cmds

    def test_runs_exactly_one_command(self):
        inst, cmds = self._make()
        inst.install()
        assert len(cmds) == 1

    def test_command_uses_apt_get(self):
        inst, cmds = self._make()
        inst.install()
        assert "apt-get" in cmds[0]

    def test_installs_libcublas12(self):
        inst, cmds = self._make()
        inst.install()
        assert "libcublas12" in cmds[0]

    def test_installs_libcudart12(self):
        inst, cmds = self._make()
        inst.install()
        assert "libcudart12" in cmds[0]

    def test_returns_true_on_success(self):
        inst, _ = self._make(rc=0)
        assert inst.install() is True

    def test_returns_false_on_failure(self):
        inst, _ = self._make(rc=1)
        assert inst.install() is False


# ── CudaInstaller – dnf branch ────────────────────────────────────────────────

class TestCudaInstallerDnfBranch:
    def _make(self, fedora_ver: str = "41", rc: int = 0):
        cmds, shell = _recording_shell(rc)
        inst = CudaInstaller(
            which_fn=_which_only("dnf"),
            shell_fn=shell,
            popen_fn=lambda _: FakePipe(fedora_ver),
        )
        return inst, cmds

    def test_runs_exactly_one_command(self):
        inst, cmds = self._make()
        inst.install()
        assert len(cmds) == 1

    def test_command_uses_dnf(self):
        inst, cmds = self._make()
        inst.install()
        assert "dnf" in cmds[0]

    def test_includes_detected_fedora_version_in_repo_url(self):
        inst, cmds = self._make(fedora_ver="41")
        inst.install()
        assert "fedora41" in cmds[0]

    def test_installs_libcublas(self):
        inst, cmds = self._make()
        inst.install()
        assert "libcublas-12-x" in cmds[0]

    def test_installs_cuda_cudart(self):
        inst, cmds = self._make()
        inst.install()
        assert "cuda-cudart-12-x" in cmds[0]

    def test_returns_true_on_success(self):
        inst, _ = self._make(rc=0)
        assert inst.install() is True

    def test_returns_false_on_failure(self):
        inst, _ = self._make(rc=1)
        assert inst.install() is False


# ── CudaInstaller – pacman branch ─────────────────────────────────────────────

class TestCudaInstallerPacmanBranch:
    def _make(self, rc: int = 0):
        cmds, shell = _recording_shell(rc)
        return CudaInstaller(which_fn=_which_only("pacman"), shell_fn=shell), cmds

    def test_runs_exactly_one_command(self):
        inst, cmds = self._make()
        inst.install()
        assert len(cmds) == 1

    def test_command_uses_pacman(self):
        inst, cmds = self._make()
        inst.install()
        assert "pacman" in cmds[0]

    def test_installs_cuda_package(self):
        inst, cmds = self._make()
        inst.install()
        assert "cuda" in cmds[0]

    def test_returns_true_on_success(self):
        inst, _ = self._make(rc=0)
        assert inst.install() is True

    def test_returns_false_on_failure(self):
        inst, _ = self._make(rc=1)
        assert inst.install() is False


# ── CudaInstaller – no supported PM ──────────────────────────────────────────

class TestCudaInstallerNoPM:
    def test_returns_false_when_no_known_pm(self):
        inst = CudaInstaller(which_fn=lambda _: None)
        assert inst.install() is False

    def test_runs_no_shell_command(self):
        cmds, shell = _recording_shell()
        inst = CudaInstaller(which_fn=lambda _: None, shell_fn=shell)
        inst.install()
        assert cmds == []


# ── CudaInstaller – cross-distro branch isolation ────────────────────────────
#
# These are the regression tests that prevent a change in one distro's path
# from silently affecting another.  If someone edits the apt-get block and
# accidentally references "dnf", these tests will catch it.

class TestCudaInstallerBranchIsolation:
    def test_apt_branch_never_runs_dnf(self):
        cmds, shell = _recording_shell()
        CudaInstaller(which_fn=_which_only("apt-get"), shell_fn=shell).install()
        assert not any("dnf" in c for c in cmds)

    def test_apt_branch_never_runs_pacman(self):
        cmds, shell = _recording_shell()
        CudaInstaller(which_fn=_which_only("apt-get"), shell_fn=shell).install()
        assert not any("pacman" in c for c in cmds)

    def test_dnf_branch_never_runs_apt_get(self):
        cmds, shell = _recording_shell()
        CudaInstaller(
            which_fn=_which_only("dnf"),
            shell_fn=shell,
            popen_fn=lambda _: FakePipe("41"),
        ).install()
        assert not any("apt-get" in c for c in cmds)

    def test_dnf_branch_never_runs_pacman(self):
        cmds, shell = _recording_shell()
        CudaInstaller(
            which_fn=_which_only("dnf"),
            shell_fn=shell,
            popen_fn=lambda _: FakePipe("41"),
        ).install()
        assert not any("pacman" in c for c in cmds)

    def test_pacman_branch_never_runs_apt_get(self):
        cmds, shell = _recording_shell()
        CudaInstaller(which_fn=_which_only("pacman"), shell_fn=shell).install()
        assert not any("apt-get" in c for c in cmds)

    def test_pacman_branch_never_runs_dnf(self):
        cmds, shell = _recording_shell()
        CudaInstaller(which_fn=_which_only("pacman"), shell_fn=shell).install()
        assert not any("dnf" in c for c in cmds)


# ── CudaInstaller – exception handling ───────────────────────────────────────

class TestCudaInstallerExceptionHandling:
    def test_returns_false_when_shell_raises(self):
        def boom(_): raise RuntimeError("disk full")
        inst = CudaInstaller(which_fn=_which_only("apt-get"), shell_fn=boom)
        assert inst.install() is False

    def test_returns_false_when_popen_raises(self):
        def boom(_): raise OSError("popen failed")
        inst = CudaInstaller(
            which_fn=_which_only("dnf"),
            shell_fn=lambda _: 0,
            popen_fn=boom,
        )
        assert inst.install() is False


# ── RocmInstaller ─────────────────────────────────────────────────────────────

class TestRocmInstallerIsAvailable:
    def test_true_when_rocminfo_present(self):
        inst = RocmInstaller(which_fn=_which_only("rocminfo"))
        assert inst.is_available() is True

    def test_false_when_no_rocm_signals(self, monkeypatch):
        monkeypatch.setattr(os.path, "exists", lambda _p: False)
        inst = RocmInstaller(which_fn=lambda _: None)
        assert inst.is_available() is False


class TestRocmInstallerAptBranch:
    def _make(self, rc: int = 0):
        cmds, shell = _recording_shell(rc)
        return RocmInstaller(which_fn=_which_only("apt-get"), shell_fn=shell), cmds

    def test_runs_exactly_one_command(self):
        inst, cmds = self._make()
        inst.install()
        assert len(cmds) == 1

    def test_command_uses_apt_get(self):
        inst, cmds = self._make()
        inst.install()
        assert "apt-get" in cmds[0]

    def test_installs_rocm_runtime(self):
        inst, cmds = self._make()
        inst.install()
        assert "rocm-hip-runtime" in cmds[0]

    def test_returns_false_on_failure(self):
        inst, _ = self._make(rc=1)
        assert inst.install() is False


class TestRocmInstallerNoPM:
    def test_returns_false_when_no_known_pm(self):
        inst = RocmInstaller(which_fn=lambda _: None)
        assert inst.install() is False


class TestRocmInstallerBranchIsolation:
    def test_apt_branch_never_runs_dnf_or_pacman(self):
        cmds, shell = _recording_shell()
        RocmInstaller(which_fn=_which_only("apt-get"), shell_fn=shell).install()
        assert not any("dnf" in c or "pacman" in c for c in cmds)

    def test_dnf_branch_never_runs_apt_get_or_pacman(self):
        cmds, shell = _recording_shell()
        RocmInstaller(which_fn=_which_only("dnf"), shell_fn=shell).install()
        assert not any("apt-get" in c or "pacman" in c for c in cmds)

    def test_pacman_branch_never_runs_apt_get_or_dnf(self):
        cmds, shell = _recording_shell()
        RocmInstaller(which_fn=_which_only("pacman"), shell_fn=shell).install()
        assert not any("apt-get" in c or "dnf" in c for c in cmds)

    def test_rocm_apt_branch_never_installs_cuda_libs(self):
        cmds, shell = _recording_shell()
        RocmInstaller(which_fn=_which_only("apt-get"), shell_fn=shell).install()
        assert not any("libcublas" in c or "libcudart" in c for c in cmds)


class TestRocmInstallerExceptionHandling:
    def test_returns_false_when_shell_raises(self):
        def boom(_): raise RuntimeError("disk full")
        inst = RocmInstaller(which_fn=_which_only("apt-get"), shell_fn=boom)
        assert inst.install() is False


# ── detect_gpu_vendor ─────────────────────────────────────────────────────────

class TestDetectGpuVendor:
    def test_apple_on_darwin(self):
        assert detect_gpu_vendor(which_fn=lambda _: None, platform_fn=lambda: "darwin") == "apple"

    def test_nvidia_when_nvidia_smi_present(self):
        assert detect_gpu_vendor(
            which_fn=_which_only("nvidia-smi"), platform_fn=lambda: "linux"
        ) == "nvidia"

    def test_amd_when_rocminfo_present(self):
        assert detect_gpu_vendor(
            which_fn=_which_only("rocminfo"), platform_fn=lambda: "linux"
        ) == "amd"

    def test_none_when_no_gpu(self, monkeypatch):
        monkeypatch.setattr(os.path, "exists", lambda _p: False)
        assert detect_gpu_vendor(which_fn=lambda _: None, platform_fn=lambda: "linux") == "none"

    def test_nvidia_takes_precedence_over_amd(self):
        # A box with both probes present should report the NVIDIA path first.
        which = lambda cmd: f"/usr/bin/{cmd}" if cmd in ("nvidia-smi", "rocminfo") else None
        assert detect_gpu_vendor(which_fn=which, platform_fn=lambda: "linux") == "nvidia"


# ── WhisperEngineInstaller ────────────────────────────────────────────────────

class TestWhisperEngineInstaller:
    def test_available_when_spec_found(self):
        inst = WhisperEngineInstaller(find_spec_fn=lambda _: object())
        assert inst.is_available() is True

    def test_not_available_when_spec_missing(self):
        inst = WhisperEngineInstaller(find_spec_fn=lambda _: None)
        assert inst.is_available() is False

    def test_not_available_when_spec_raises(self):
        def boom(_): raise ValueError("bad")
        inst = WhisperEngineInstaller(find_spec_fn=boom)
        assert inst.is_available() is False

    def test_install_runs_pip_for_faster_whisper(self):
        captured: list[list[str]] = []
        inst = WhisperEngineInstaller(runner_fn=lambda cmd: captured.append(cmd) or 0)
        assert inst.install() is True
        assert captured and "faster-whisper" in captured[0]
        assert "pip" in captured[0] and "install" in captured[0]

    def test_install_returns_false_on_nonzero(self):
        inst = WhisperEngineInstaller(runner_fn=lambda _: 1)
        assert inst.install() is False

    def test_install_returns_false_on_exception(self):
        def boom(_): raise OSError("no pip")
        inst = WhisperEngineInstaller(runner_fn=boom)
        assert inst.install() is False
