from __future__ import annotations

import logging
from typing import Type

from .audio.base import AudioBackend
from .screen.base import ScreenRecorder

logger = logging.getLogger(__name__)


class PlatformRegistry:
    """Maps config strings to concrete platform implementation classes."""

    def get_audio_backend(self, name: str) -> Type[AudioBackend] | None:
        backends = self._audio_backends()
        return backends.get(name)

    def get_screen_recorder(self, name: str) -> Type[ScreenRecorder] | None:
        recorders = self._screen_recorders()
        return recorders.get(name)

    def available_audio_backends(self) -> list[str]:
        return list(self._audio_backends().keys())

    def available_screen_recorders(self) -> list[str]:
        return list(self._screen_recorders().keys())

    def _audio_backends(self) -> dict[str, Type[AudioBackend]]:
        result: dict[str, Type[AudioBackend]] = {}
        try:
            from .audio.pulseaudio import PulseAudioBackend
            result["pulseaudio"] = PulseAudioBackend
        except ImportError:
            logger.debug("PulseAudio backend not available")
        try:
            from .audio.pipewire import PipeWireBackend
            result["pipewire"] = PipeWireBackend
        except ImportError:
            logger.debug("PipeWire backend not available")
        return result

    def _screen_recorders(self) -> dict[str, Type[ScreenRecorder]]:
        result: dict[str, Type[ScreenRecorder]] = {}
        try:
            from .screen.none import NoOpScreenRecorder
            result["none"] = NoOpScreenRecorder
        except ImportError:
            pass
        try:
            from .screen.gpu_screen_recorder import GpuScreenRecorder
            result["gpu-screen-recorder"] = GpuScreenRecorder
        except ImportError:
            logger.debug("gpu-screen-recorder backend not available")
        return result
