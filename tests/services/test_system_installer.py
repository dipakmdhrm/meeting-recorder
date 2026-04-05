"""
Tests for OllamaInstaller and CudaInstaller.

All tests use the injected which_fn / shell_fn / popen_fn hooks so no real
shell commands are executed.  The cross-distro branch isolation tests are the
most important ones: they ensure that changing the apt-get path cannot
silently break the dnf or pacman path and vice-versa.
"""
import pytest
from meeting_recorder.services.system_installer import CudaInstaller, OllamaInstaller


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
