"""
Pure model of a model/engine install request, shared across the daemon/UI split.

The Models settings tab issues install/build/download requests; the daemon runs
each in a short-lived ``--install`` child (installing faster-whisper imports the
heavy CTranslate2 stack, so it must not load into the long-lived daemon). This
module is the GTK-free description of *what* to install plus ``install_key`` —
the stable identifier the daemon dedups on and the UI keys progress to.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# Install kinds. The first four succeed/fail; the last three are model pulls
# (``model`` set). ``gpu`` carries a ``vendor``; ``whisper_cpp_build`` a ``backend``.
OLLAMA = "ollama"
WHISPER_ENGINE = "whisper_engine"
WHISPER_CPP_BUILD = "whisper_cpp_build"
GPU = "gpu"
WHISPER_MODEL = "whisper_model"
WHISPER_CPP_MODEL = "whisper_cpp_model"
OLLAMA_MODEL = "ollama_model"

KINDS = frozenset(
    {
        OLLAMA,
        WHISPER_ENGINE,
        WHISPER_CPP_BUILD,
        GPU,
        WHISPER_MODEL,
        WHISPER_CPP_MODEL,
        OLLAMA_MODEL,
    }
)

_MODEL_KINDS = frozenset({WHISPER_MODEL, WHISPER_CPP_MODEL, OLLAMA_MODEL})


@dataclass
class InstallSpec:
    kind: str
    model: str = ""
    backend: str = ""
    vendor: str = ""
    host: str = ""


def install_key(spec: InstallSpec) -> str:
    """Stable id for dedup + UI mapping.

    Per-model and per-vendor installs get a scoped key so different models (or
    GPU vendors) can install concurrently while the same request dedups.
    """
    if spec.kind == GPU:
        return f"{GPU}:{spec.vendor}"
    if spec.kind in _MODEL_KINDS:
        return f"{spec.kind}:{spec.model}"
    return spec.kind


def spec_to_json(spec: InstallSpec) -> str:
    return json.dumps(
        {
            "kind": spec.kind,
            "model": spec.model,
            "backend": spec.backend,
            "vendor": spec.vendor,
            "host": spec.host,
        }
    )


def spec_from_json(payload: str) -> InstallSpec:
    data = json.loads(payload)
    if not isinstance(data, dict) or data.get("kind") not in KINDS:
        raise ValueError(f"invalid install spec: {payload!r}")
    return InstallSpec(
        kind=data["kind"],
        model=data.get("model", ""),
        backend=data.get("backend", ""),
        vendor=data.get("vendor", ""),
        host=data.get("host", ""),
    )
