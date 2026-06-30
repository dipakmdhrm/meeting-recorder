"""
Small GTK4 container helpers.

GTK4 removed ``Gtk.Container`` and its ``get_children()`` / ``add()`` / ``remove()``
child-management API in favour of the per-widget sibling list
(``get_first_child()`` / ``get_next_sibling()``). These helpers reproduce the two
GTK3 idioms the UI relied on: iterating a widget's children and clearing a box.
"""

from __future__ import annotations

from typing import Iterator

from gi.repository import Gtk


def iter_children(widget: Gtk.Widget) -> Iterator[Gtk.Widget]:
    """Yield each direct child of ``widget`` (GTK4 replacement for get_children()).

    The next sibling is captured before yielding so the caller may remove the
    current child mid-iteration without breaking the walk.
    """
    child = widget.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        yield child
        child = nxt


def remove_all_children(box: Gtk.Widget) -> None:
    """Remove every child of ``box`` (GTK4 has no container-level clear)."""
    child = box.get_first_child()
    while child is not None:
        box.remove(child)
        child = box.get_first_child()
