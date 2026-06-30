"""
Reusable libadwaita widget for displaying downloadable models with status
labels and action buttons.

Used by the Settings dialog for the Whisper, whisper.cpp, and Ollama model
sections. Rendered as an ``Adw.PreferencesGroup`` of ``Adw.ActionRow``s — one
row per model (title = model name, subtitle = size · note), each with a status
label and a download/retry button in the row suffix.
"""

from __future__ import annotations

from typing import Callable

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk


class ModelRowGrid(Adw.PreferencesGroup):
    """
    A group of model rows. Row state is updated via the public setters below,
    which are safe to call directly from ``GLib.idle_add``.
    """

    def __init__(
        self,
        models: list[str],
        model_info: dict[str, dict],
        on_download: Callable[[str], None],
        title: str | None = None,
    ) -> None:
        super().__init__()
        if title:
            self.set_title(title)
        self._rows: dict[str, dict] = {}

        for model in models:
            info = model_info.get(model, {})
            subtitle_parts = [p for p in (info.get("size", ""), info.get("note", "")) if p]
            row = Adw.ActionRow(title=model, subtitle="  ·  ".join(subtitle_parts))

            suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            suffix.set_valign(Gtk.Align.CENTER)

            status_lbl = Gtk.Label(label="Checking…")
            status_lbl.add_css_class("dim-label")
            suffix.append(status_lbl)

            btn = Gtk.Button(label="Download")
            btn.add_css_class("flat")
            btn.connect("clicked", lambda _b, m=model: on_download(m))
            suffix.append(btn)

            row.add_suffix(suffix)
            self.add(row)

            self._rows[model] = {"status": status_lbl, "btn": btn}

    # ------------------------------------------------------------------
    # Public state setters
    # ------------------------------------------------------------------

    def set_not_downloaded(self, model: str) -> None:
        self._update_row(model, "Not downloaded", "Download", sensitive=True)

    def set_ready(self, model: str) -> None:
        self._update_row(model, "Ready", "Downloaded", sensitive=False)

    def set_error(self, model: str, msg: str) -> None:
        self._update_row(model, msg[:60], "Retry", sensitive=True)

    def set_progress(self, model: str, text: str) -> None:
        """Show in-progress text without changing the button label."""
        row = self._rows.get(model)
        if row:
            row["status"].set_text(text[:40])
            row["btn"].set_sensitive(False)

    def set_status_text(self, model: str, text: str, btn_sensitive: bool = True) -> None:
        """Update only the status label; optionally control button sensitivity."""
        row = self._rows.get(model)
        if row:
            row["status"].set_text(text)
            row["btn"].set_sensitive(btn_sensitive)

    # ------------------------------------------------------------------

    def _update_row(
        self, model: str, status: str, btn_label: str, sensitive: bool
    ) -> None:
        row = self._rows.get(model)
        if row:
            row["status"].set_text(status)
            row["btn"].set_label(btn_label)
            row["btn"].set_sensitive(sensitive)
