"""Shared widget building blocks for the settings pages.

Kept in one place so every page builds identical-looking rows/scroll columns.
``IdComboRow`` is re-exported from ``meeting_recorder.ui.settings_dialog`` for
backwards compatibility.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk


class IdComboRow(Adw.ComboRow):
    """An ``Adw.ComboRow`` backed by a list of display labels but addressed by a
    parallel list of stable ids — exposing ``get_active_id()`` / ``set_active_id()``
    so the save logic stays id-based (as it was with ``Gtk.ComboBoxText``)."""

    def __init__(self, title: str, ids: list[str], labels: list[str], active_id: str):
        super().__init__(title=title)
        self._ids = list(ids)
        self.set_model(Gtk.StringList.new(labels))
        self.set_active_id(active_id)

    def get_active_id(self) -> str | None:
        i = self.get_selected()
        if 0 <= i < len(self._ids):
            return self._ids[i]
        return None

    def set_active_id(self, id_: str | None) -> None:
        if id_ in self._ids:
            self.set_selected(self._ids.index(id_))
        elif self._ids:
            self.set_selected(0)


def make_scroll_page() -> tuple[Gtk.ScrolledWindow, Gtk.Box]:
    """Return (scrolled_window, content_box) for one tab — a clamped,
    vertically-scrolling column of preference groups."""
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_vexpand(True)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
    box.set_margin_top(24)
    box.set_margin_bottom(24)
    box.set_margin_start(12)
    box.set_margin_end(12)
    clamp = Adw.Clamp(maximum_size=620)
    clamp.set_child(box)
    scroll.set_child(clamp)
    return scroll, box


def install_button(label: str) -> Gtk.Button:
    btn = Gtk.Button(label=label)
    btn.add_css_class("flat")
    btn.set_valign(Gtk.Align.CENTER)
    return btn


def action_row(title: str, subtitle: str, suffix: Gtk.Widget) -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    row.add_suffix(suffix)
    row.set_activatable_widget(suffix)
    return row
