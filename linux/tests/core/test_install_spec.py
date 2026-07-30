"""Tests for the pure install-spec model and its stable keys."""

import pytest

from meeting_recorder.core.install_spec import (
    InstallSpec,
    install_key,
    spec_from_json,
    spec_to_json,
)


def test_simple_kind_key_is_the_kind():
    assert install_key(InstallSpec(kind="ollama")) == "ollama"
    assert install_key(InstallSpec(kind="whisper_engine")) == "whisper_engine"
    assert install_key(InstallSpec(kind="whisper_cpp_build", backend="cuda")) == "whisper_cpp_build"


def test_gpu_key_is_scoped_by_vendor():
    assert install_key(InstallSpec(kind="gpu", vendor="nvidia")) == "gpu:nvidia"
    assert install_key(InstallSpec(kind="gpu", vendor="amd")) == "gpu:amd"


def test_model_keys_are_scoped_by_model():
    assert install_key(InstallSpec(kind="whisper_model", model="small")) == "whisper_model:small"
    assert install_key(InstallSpec(kind="ollama_model", model="llama3")) == "ollama_model:llama3"
    # Different models of the same kind get distinct keys (can run concurrently).
    a = install_key(InstallSpec(kind="whisper_cpp_model", model="base"))
    b = install_key(InstallSpec(kind="whisper_cpp_model", model="large"))
    assert a != b


def test_json_round_trip():
    spec = InstallSpec(kind="ollama_model", model="llama3", host="http://localhost:11434")
    back = spec_from_json(spec_to_json(spec))
    assert back == spec


def test_from_json_rejects_unknown_kind():
    with pytest.raises(ValueError):
        spec_from_json('{"kind": "bogus"}')


def test_from_json_rejects_non_object():
    with pytest.raises(ValueError):
        spec_from_json("[]")
