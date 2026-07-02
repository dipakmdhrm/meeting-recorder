"""Settings → Prompts tab: transcription / summarization / title prompt editors.

Storing an empty string for a prompt key means "use the built-in default"
(same convention as the Android app); ``apply()`` writes ``""`` when the
editor content equals the default.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from meeting_recorder.config.defaults import (
    GEMINI_TRANSCRIPTION_PROMPT,
    SUMMARIZATION_PROMPT,
    TITLE_PROMPT,
)

from .widgets import make_scroll_page

_PROMPT_DEFAULTS = {
    "transcription": GEMINI_TRANSCRIPTION_PROMPT,
    "summarization": SUMMARIZATION_PROMPT,
    "title": TITLE_PROMPT,
}


class PromptsPage:
    """Builds the Prompts tab and writes its values back on save via ``apply()``."""

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._prompt_views: dict[str, Gtk.TextView] = {}
        self.widget = self._build()

    # ------------------------------------------------------------------
    # Build — three sections built from a single helper (DRY)
    # ------------------------------------------------------------------

    def _build(self) -> Gtk.Widget:
        scroll, box = make_scroll_page()
        box.append(
            self._build_prompt_section(
                key="transcription",
                label="Transcription prompt",
                note="Transcription prompts apply to Gemini only. Whisper does not use prompts.",
                height=160,
            )
        )
        box.append(
            self._build_prompt_section(
                key="summarization",
                label="Summarization prompt",
                height=160,
            )
        )
        box.append(
            self._build_prompt_section(
                key="title",
                label="Title prompt",
                note=(
                    "Used for auto-titling recordings and the AI title button in the "
                    "Library. Must contain {transcript}."
                ),
                height=120,
            )
        )
        return scroll

    def _build_prompt_section(
        self,
        key: str,
        label: str,
        note: str | None = None,
        height: int = 160,
    ) -> Gtk.Widget:
        group = Adw.PreferencesGroup(title=label)
        if note:
            group.set_description(note)

        reset_btn = Gtk.Button(label="Reset to default")
        reset_btn.add_css_class("flat")
        reset_btn.connect("clicked", lambda *_: self._reset_prompt(key))
        group.set_header_suffix(reset_btn)

        view = Gtk.TextView()
        view.set_wrap_mode(Gtk.WrapMode.WORD)
        view.set_monospace(True)
        view.set_top_margin(8)
        view.set_bottom_margin(8)
        view.set_left_margin(8)
        view.set_right_margin(8)
        stored = self._cfg.get(f"{key}_prompt") or _PROMPT_DEFAULTS[key]
        view.get_buffer().set_text(stored)

        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(height)
        scroll.set_child(view)
        scroll.add_css_class("card")
        group.add(scroll)

        self._prompt_views[key] = view
        return group

    def _reset_prompt(self, key: str) -> None:
        view = self._prompt_views.get(key)
        if view and key in _PROMPT_DEFAULTS:
            view.get_buffer().set_text(_PROMPT_DEFAULTS[key])

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def apply(self, cfg: dict) -> None:
        """Write this tab's values into ``cfg`` (called by the dialog's save flow)."""
        for key, default in _PROMPT_DEFAULTS.items():
            cfg[f"{key}_prompt"] = self._read_prompt(self._prompt_views[key], default)

    @staticmethod
    def _read_prompt(view: Gtk.TextView, default: str) -> str:
        buf = view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        return "" if text == default.strip() else text
