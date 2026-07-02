"""
Tests for OllamaInstaller, CudaInstaller, and RocmInstaller.

All tests use the injected which_fn / run_fn / capture_fn / fetch_fn hooks so
no real commands are executed.  The cross-distro branch isolation tests are
the most important ones: they ensure that changing the apt-get path cannot
silently break the dnf or pacman path and vice-versa.

Commands are argv lists; the recording helper joins them to strings so the
assertions can check for package names regardless of elevation prefix
(pkexec/sudo sh -c ...).
"""

import os

from meeting_recorder.services.system_installer import (
    CudaInstaller,
    OllamaInstaller,
    RocmInstaller,
    WhisperEngineInstaller,
    build_privileged_command,
    detect_gpu_vendor,
)

# ── helpers ───────────────────────────────────────────────────────────────────


def _which_only(pm: str):
    """Return a which_fn that only recognises one package manager."""
    return lambda cmd: f"/usr/bin/{cmd}" if cmd == pm else None


def _recording_run(rc: int = 0):
    """Return (commands_list, run_fn) that records every command as a string."""
    commands: list[str] = []
    return commands, lambda cmd: commands.append(" ".join(cmd)) or rc


# ── build_privileged_command ──────────────────────────────────────────────────


class TestBuildPrivilegedCommand:
    def test_uses_pkexec_when_available(self):
        cmd = build_privileged_command("apt-get install -y foo", which_fn=_which_only("pkexec"))
        assert cmd == ["pkexec", "sh", "-c", "apt-get install -y foo"]

    def test_falls_back_to_sudo_without_polkit(self):
        cmd = build_privileged_command("apt-get install -y foo", which_fn=lambda _: None)
        assert cmd == ["sudo", "sh", "-c", "apt-get install -y foo"]

    def test_snippet_is_single_argv_element(self):
        # The snippet must never be split/interpolated into separate args.
        cmd = build_privileged_command("a && b; c", which_fn=lambda _: None)
        assert cmd[-1] == "a && b; c"
        assert len(cmd) == 4  # elevate, sh, -c, snippet


# ── OllamaInstaller ───────────────────────────────────────────────────────────


class TestOllamaInstallerIsAvailable:
    def test_true_when_ollama_found(self):
        inst = OllamaInstaller(which_fn=lambda _: "/usr/bin/ollama")
        assert inst.is_available() is True

    def test_false_when_ollama_missing(self):
        inst = OllamaInstaller(which_fn=lambda _: None)
        assert inst.is_available() is False


class TestOllamaInstallerInstall:
    # Padded past the installer's minimum-size sanity check.
    SCRIPT = b"#!/bin/sh\necho installing\n" + b"# padding\n" * 120

    def _install(self, rc: int = 0, fetch=None):
        ran: list[list[str]] = []
        seen_content: list[bytes] = []

        def run(cmd):
            ran.append(cmd)
            # The script file must exist and hold the fetched bytes at run time.
            with open(cmd[1], "rb") as f:
                seen_content.append(f.read())
            return rc

        inst = OllamaInstaller(
            fetch_fn=fetch or (lambda _url: self.SCRIPT),
            run_fn=run,
        )
        return inst.install(), ran, seen_content

    def test_runs_downloaded_script_from_disk_not_a_pipe(self):
        ok, ran, seen = self._install(rc=0)
        assert ok is True
        assert len(ran) == 1
        assert ran[0][0] == "sh"  # ["sh", "/tmp/ollama-install-....sh"]
        assert "curl" not in " ".join(ran[0])
        assert seen == [self.SCRIPT]

    def test_temp_script_removed_after_run(self):
        _ok, ran, _seen = self._install(rc=0)
        assert not os.path.exists(ran[0][1])

    def test_returns_false_on_nonzero_exit(self):
        ok, _ran, _seen = self._install(rc=1)
        assert ok is False

    def test_returns_false_when_fetch_fails(self):
        def fetch_boom(_url):
            raise OSError("network error")

        ok, ran, _seen = self._install(fetch=fetch_boom)
        assert ok is False
        assert ran == []  # nothing executed if the download failed


# ── CudaInstaller.is_available ────────────────────────────────────────────────


class TestCudaInstallerIsAvailable:
    def test_true_when_nvidia_smi_present(self):
        inst = CudaInstaller(
            which_fn=lambda cmd: "/usr/bin/nvidia-smi" if cmd == "nvidia-smi" else None
        )
        assert inst.is_available() is True

    def test_false_when_nvidia_smi_absent(self):
        inst = CudaInstaller(which_fn=lambda _: None)
        assert inst.is_available() is False


# ── CudaInstaller – apt-get branch ───────────────────────────────────────────


class TestCudaInstallerAptBranch:
    def _make(self, rc: int = 0):
        cmds, run = _recording_run(rc)
        return CudaInstaller(which_fn=_which_only("apt-get"), run_fn=run), cmds

    def test_runs_exactly_one_command(self):
        inst, cmds = self._make()
        inst.install()
        assert len(cmds) == 1

    def test_command_uses_apt_get(self):
        inst, cmds = self._make()
        inst.install()
        assert "apt-get" in cmds[0]

    def test_command_is_privilege_elevated(self):
        inst, cmds = self._make()
        inst.install()
        assert cmds[0].startswith(("pkexec ", "sudo "))

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
        cmds, run = _recording_run(rc)
        inst = CudaInstaller(
            which_fn=_which_only("dnf"),
            run_fn=run,
            capture_fn=lambda _cmd: fedora_ver + "\n",
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

    def test_rejects_non_numeric_fedora_version(self):
        # A garbage rpm answer must not be interpolated into the repo URL.
        inst, cmds = self._make(fedora_ver="41; rm -rf /")
        assert inst.install() is False
        assert cmds == []


# ── CudaInstaller – pacman branch ─────────────────────────────────────────────


class TestCudaInstallerPacmanBranch:
    def _make(self, rc: int = 0):
        cmds, run = _recording_run(rc)
        return CudaInstaller(which_fn=_which_only("pacman"), run_fn=run), cmds

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

    def test_runs_no_command(self):
        cmds, run = _recording_run()
        inst = CudaInstaller(which_fn=lambda _: None, run_fn=run)
        inst.install()
        assert cmds == []


# ── CudaInstaller – cross-distro branch isolation ────────────────────────────
#
# These are the regression tests that prevent a change in one distro's path
# from silently affecting another.  If someone edits the apt-get block and
# accidentally references "dnf", these tests will catch it.


class TestCudaInstallerBranchIsolation:
    def test_apt_branch_never_runs_dnf(self):
        cmds, run = _recording_run()
        CudaInstaller(which_fn=_which_only("apt-get"), run_fn=run).install()
        assert not any("dnf" in c for c in cmds)

    def test_apt_branch_never_runs_pacman(self):
        cmds, run = _recording_run()
        CudaInstaller(which_fn=_which_only("apt-get"), run_fn=run).install()
        assert not any("pacman" in c for c in cmds)

    def test_dnf_branch_never_runs_apt_get(self):
        cmds, run = _recording_run()
        CudaInstaller(
            which_fn=_which_only("dnf"),
            run_fn=run,
            capture_fn=lambda _cmd: "41",
        ).install()
        assert not any("apt-get" in c for c in cmds)

    def test_dnf_branch_never_runs_pacman(self):
        cmds, run = _recording_run()
        CudaInstaller(
            which_fn=_which_only("dnf"),
            run_fn=run,
            capture_fn=lambda _cmd: "41",
        ).install()
        assert not any("pacman" in c for c in cmds)

    def test_pacman_branch_never_runs_apt_get(self):
        cmds, run = _recording_run()
        CudaInstaller(which_fn=_which_only("pacman"), run_fn=run).install()
        assert not any("apt-get" in c for c in cmds)

    def test_pacman_branch_never_runs_dnf(self):
        cmds, run = _recording_run()
        CudaInstaller(which_fn=_which_only("pacman"), run_fn=run).install()
        assert not any("dnf" in c for c in cmds)


# ── CudaInstaller – exception handling ───────────────────────────────────────


class TestCudaInstallerExceptionHandling:
    def test_returns_false_when_run_raises(self):
        def boom(_):
            raise RuntimeError("disk full")

        inst = CudaInstaller(which_fn=_which_only("apt-get"), run_fn=boom)
        assert inst.install() is False

    def test_returns_false_when_capture_raises(self):
        def boom(_):
            raise OSError("rpm failed")

        inst = CudaInstaller(
            which_fn=_which_only("dnf"),
            run_fn=lambda _: 0,
            capture_fn=boom,
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
        cmds, run = _recording_run(rc)
        return RocmInstaller(which_fn=_which_only("apt-get"), run_fn=run), cmds

    def test_runs_exactly_one_command(self):
        inst, cmds = self._make()
        inst.install()
        assert len(cmds) == 1

    def test_command_uses_apt_get(self):
        inst, cmds = self._make()
        inst.install()
        assert "apt-get" in cmds[0]

    def test_command_is_privilege_elevated(self):
        inst, cmds = self._make()
        inst.install()
        assert cmds[0].startswith(("pkexec ", "sudo "))

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
        cmds, run = _recording_run()
        RocmInstaller(which_fn=_which_only("apt-get"), run_fn=run).install()
        assert not any("dnf" in c or "pacman" in c for c in cmds)

    def test_dnf_branch_never_runs_apt_get_or_pacman(self):
        cmds, run = _recording_run()
        RocmInstaller(which_fn=_which_only("dnf"), run_fn=run).install()
        assert not any("apt-get" in c or "pacman" in c for c in cmds)

    def test_pacman_branch_never_runs_apt_get_or_dnf(self):
        cmds, run = _recording_run()
        RocmInstaller(which_fn=_which_only("pacman"), run_fn=run).install()
        assert not any("apt-get" in c or "dnf" in c for c in cmds)

    def test_rocm_apt_branch_never_installs_cuda_libs(self):
        cmds, run = _recording_run()
        RocmInstaller(which_fn=_which_only("apt-get"), run_fn=run).install()
        assert not any("libcublas" in c or "libcudart" in c for c in cmds)


class TestRocmInstallerExceptionHandling:
    def test_returns_false_when_run_raises(self):
        def boom(_):
            raise RuntimeError("disk full")

        inst = RocmInstaller(which_fn=_which_only("apt-get"), run_fn=boom)
        assert inst.install() is False


# ── detect_gpu_vendor ─────────────────────────────────────────────────────────


class TestDetectGpuVendor:
    def test_apple_on_darwin(self):
        assert detect_gpu_vendor(which_fn=lambda _: None, platform_fn=lambda: "darwin") == "apple"

    def test_nvidia_when_nvidia_smi_present(self):
        assert (
            detect_gpu_vendor(which_fn=_which_only("nvidia-smi"), platform_fn=lambda: "linux")
            == "nvidia"
        )

    def test_amd_when_rocminfo_present(self):
        assert (
            detect_gpu_vendor(which_fn=_which_only("rocminfo"), platform_fn=lambda: "linux")
            == "amd"
        )

    def test_none_when_no_gpu(self, monkeypatch):
        monkeypatch.setattr(os.path, "exists", lambda _p: False)
        assert detect_gpu_vendor(which_fn=lambda _: None, platform_fn=lambda: "linux") == "none"

    def test_nvidia_takes_precedence_over_amd(self):
        # A box with both probes present should report the NVIDIA path first.
        def which(cmd):
            return f"/usr/bin/{cmd}" if cmd in ("nvidia-smi", "rocminfo") else None

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
        def boom(_):
            raise ValueError("bad")

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
        def boom(_):
            raise OSError("no pip")

        inst = WhisperEngineInstaller(runner_fn=boom)
        assert inst.install() is False


class TestOllamaInstallerScriptValidation:
    def test_tiny_fetched_script_aborts_without_running(self):
        # A captive portal / proxy can answer 200 with a tiny body; executing
        # it would silently "succeed" without installing anything.
        ran: list[list[str]] = []
        inst = OllamaInstaller(
            fetch_fn=lambda _url: b"#!/bin/sh\n",
            run_fn=lambda cmd: ran.append(cmd) or 0,
        )
        assert inst.install() is False
        assert ran == []
