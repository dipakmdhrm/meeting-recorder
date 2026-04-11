"""
Reusable GTK grid widget for displaying downloadable models with status
labels and action buttons.

Used by the Settings dialog for both Whisper and Ollama model sections,
eliminating the previously duplicated grid-building and row-state code.
"""

from __future__ import annotations

from typing import Callable

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


class ModelRowGrid(Gtk.Grid):
    """
    Renders a table of models:  Model | Size | Note | Status | (button)

    Row state is updated via the public setters below, which are safe to
    call directly from ``GLib.idle_add``.
    """

    def __init__(
        self,
        models: list[str],
        model_info: dict[str, dict],
        on_download: Callable[[str], None],
    ) -> None:
        super().__init__(column_spacing=12, row_spacing=8)
        self._rows: dict[str, dict] = {}

        for col, text in enumerate(["Model", "Size", "Note", "Status", ""]):
            lbl = Gtk.Label(xalign=0)
            lbl.set_markup(f"<b>{text}</b>")
            self.attach(lbl, col, 0, 1, 1)

        for r, model in enumerate(models, start=1):
            info = model_info.get(model, {})
            self.attach(Gtk.Label(label=model, xalign=0), 0, r, 1, 1)
            self.attach(Gtk.Label(label=info.get("size", ""), xalign=0), 1, r, 1, 1)
            self.attach(Gtk.Label(label=info.get("note", ""), xalign=0), 2, r, 1, 1)

            status_lbl = Gtk.Label(label="Checking\u2026", xalign=0)
            self.attach(status_lbl, 3, r, 1, 1)

            btn = Gtk.Button(label="Download")
            btn.connect("clicked", lambda _b, m=model: on_download(m))
            self.attach(btn, 4, r, 1, 1)

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
