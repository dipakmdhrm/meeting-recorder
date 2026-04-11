"""Meeting Explorer — browse, manage, and AI-title recorded meetings."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gtk, GLib, Pango

from meeting_recorder.config import settings
from meeting_recorder.config.defaults import TITLE_PROMPT
from ..utils.glib_bridge import idle_call
from meeting_recorder.utils.meeting_scanner import (
    Meeting,
    delete_meetings,
    rename_meeting_dir,
    scan_meetings,
    write_metadata,
)

logger = logging.getLogger(__name__)


class MeetingExplorer(Gtk.Box):
    """Scrollable meeting list with AI title generation and multi-select delete."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._meeting_rows: list[dict] = []  # [{meeting, check, row, ...}, ...]

        # Toolbar
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_top(12)
        toolbar.set_margin_bottom(8)
        toolbar.set_margin_start(16)
        toolbar.set_margin_end(16)

        self._delete_btn = Gtk.Button(label="Delete Selected")
        self._delete_btn.get_style_context().add_class("destructive-action")
        self._delete_btn.set_sensitive(False)
        self._delete_btn.connect("clicked", self._on_delete_clicked)
        toolbar.pack_start(self._delete_btn, False, False, 0)

        # Spacer
        toolbar.pack_start(Gtk.Box(), True, True, 0)

        refresh_btn = Gtk.Button()
        refresh_btn.set_image(
            Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON))
        refresh_btn.set_tooltip_text("Refresh")
        refresh_btn.connect("clicked", lambda *_: self.refresh())
        toolbar.pack_end(refresh_btn, False, False, 0)

        self.pack_start(toolbar, False, False, 0)

        # Error label (for delete failures etc.)
        self._error_label = Gtk.Label(xalign=0)
        self._error_label.set_line_wrap(True)
        self._error_label.set_margin_start(16)
        self._error_label.set_margin_end(16)
        self._error_label.set_no_show_all(True)
        self.pack_start(self._error_label, False, False, 0)

        # Scrollable meeting list
        self._list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._list_box.set_margin_start(16)
        self._list_box.set_margin_end(16)
        self._list_box.set_margin_bottom(16)

        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_propagate_natural_height(True)
        self._scroll.add(self._list_box)
        self.pack_start(self._scroll, True, True, 0)

        # Empty state label
        self._empty_label = Gtk.Label(label="No meetings found")
        self._empty_label.set_vexpand(True)
        self._empty_label.set_valign(Gtk.Align.CENTER)
        self._empty_label.set_opacity(0.5)
        self._empty_label.set_no_show_all(True)
        self.pack_start(self._empty_label, True, True, 0)

    def refresh(self) -> None:
        """Rescan the output folder and rebuild the meeting list."""
        self._error_label.hide()

        # Clear existing rows
        for child in self._list_box.get_children():
            self._list_box.remove(child)
        self._meeting_rows.clear()

        cfg = settings.load()
        output_folder = cfg.get("output_folder", "~/meetings")
        meetings = scan_meetings(output_folder)

        if not meetings:
            self._empty_label.show()
            self._list_box.hide()
        else:
            self._empty_label.hide()
            self._list_box.show()
            for meeting in meetings:
                self._add_meeting_row(meeting)

        self._update_delete_sensitivity()
        self.show_all()
        # Re-hide things that should stay hidden
        if not meetings:
            self._list_box.hide()
        else:
            self._empty_label.hide()
        self._error_label.hide()

    def _add_meeting_row(self, meeting: Meeting) -> None:
        """Add a single meeting row to the list."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_top(4)
        row.set_margin_bottom(4)

        # Checkbox
        check = Gtk.CheckButton()
        check.connect("toggled", lambda *_: self._update_delete_sensitivity())
        row.pack_start(check, False, False, 0)

        # Title area
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_box.set_hexpand(True)

        primary = meeting.title or meeting.time_label
        primary_label = Gtk.Label(label=primary, xalign=0)
        primary_label.set_ellipsize(Pango.EllipsizeMode.END)

        # Wrap label in EventBox for double-click editing
        title_event_box = Gtk.EventBox()
        title_event_box.add(primary_label)
        title_box.pack_start(title_event_box, False, False, 0)

        # Secondary line: date, time, duration
        date_str = meeting.date.strftime("%b %d, %Y")
        time_str = meeting.date.strftime("%I:%M %p").lstrip("0")
        parts = [date_str, time_str]
        if meeting.duration_seconds is not None:
            dur = meeting.duration_seconds
            if dur >= 3600:
                parts.append(f"{dur // 3600}h {(dur % 3600) // 60}m")
            else:
                parts.append(f"{dur // 60}m")
        secondary_text = "  \u00b7  ".join(parts)
        secondary_label = Gtk.Label(xalign=0)
        secondary_label.set_markup(
            f'<span size="small" foreground="gray">{GLib.markup_escape_text(secondary_text)}</span>'
        )
        title_box.pack_start(secondary_label, False, False, 0)

        row.pack_start(title_box, True, True, 0)

        # AI Title button / status area
        ai_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        ai_btn = Gtk.Button()
        ai_btn.set_image(
            Gtk.Image.new_from_icon_name("starred-symbolic", Gtk.IconSize.BUTTON))
        ai_btn.set_tooltip_text("Generate a title from meeting notes")

        # Build row_data dict before connecting signals that reference it
        row_data = {
            "meeting": meeting,
            "check": check,
            "row": row,
            "primary_label": primary_label,
            "title_event_box": title_event_box,
            "title_box": title_box,
            "secondary_label": secondary_label,
            "ai_box": ai_box,
            "ai_btn": ai_btn,
        }

        title_event_box.connect(
            "button-press-event",
            lambda w, e, rd=row_data: self._on_title_double_click(w, e, rd),
        )

        ai_btn.connect("clicked", lambda *_, rd=row_data: self._on_ai_title_clicked(rd))

        # Show AI button only if notes exist and no title yet
        if meeting.has_notes and meeting.title is None:
            ai_box.pack_start(ai_btn, False, False, 0)

        row.pack_start(ai_box, False, False, 0)

        # Rename button
        rename_btn = Gtk.Button()
        rename_btn.set_image(
            Gtk.Image.new_from_icon_name("document-edit-symbolic", Gtk.IconSize.BUTTON))
        rename_btn.set_tooltip_text("Rename meeting")
        rename_btn.connect("clicked", lambda *_, rd=row_data: self._on_rename_clicked(rd))
        row.pack_start(rename_btn, False, False, 0)

        # Open folder button
        folder_btn = Gtk.Button()
        folder_btn.set_image(
            Gtk.Image.new_from_icon_name("folder-open-symbolic", Gtk.IconSize.BUTTON))
        folder_btn.set_tooltip_text("Open folder")
        folder_btn.connect("clicked", lambda *_, rd=row_data: self._open_folder(rd))
        row.pack_start(folder_btn, False, False, 0)

        # Per-row delete button
        del_btn = Gtk.Button()
        del_btn.set_image(
            Gtk.Image.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON))
        del_btn.set_tooltip_text("Delete this meeting")
        del_btn.connect("clicked", lambda *_, rd=row_data: self._on_delete_single(rd))
        row.pack_start(del_btn, False, False, 0)

        self._meeting_rows.append(row_data)
        self._list_box.pack_start(row, False, False, 0)

    def _update_delete_sensitivity(self) -> None:
        selected = any(rd["check"].get_active() for rd in self._meeting_rows)
        self._delete_btn.set_sensitive(selected)

    def _on_delete_single(self, row_data: dict) -> None:
        """Delete a single meeting via its row trash button."""
        self._confirm_and_delete([row_data])

    def _open_folder(self, row_data: dict) -> None:
        path = str(row_data["meeting"].path)
        try:
            subprocess.Popen(["xdg-open", path])
        except Exception:
            pass

    # -- Inline title editing --------------------------------------------------

    def _on_rename_clicked(self, row_data: dict) -> None:
        """Handle the 'Rename' button click."""
        self._start_inline_edit(row_data)

    def _start_inline_edit(self, row_data: dict) -> None:
        """Replace the title label with an editable entry."""
        meeting = row_data["meeting"]
        title_box = row_data["title_box"]
        title_event_box = row_data["title_event_box"]
        primary_label = row_data["primary_label"]

        # If already editing, do nothing
        if not title_event_box.get_visible():
            return

        # Save scroll position — grab_focus() on a newly added widget
        # causes the ScrolledWindow to jump to the top before layout is done.
        vadj = self._scroll.get_vadjustment()
        saved_scroll = vadj.get_value()

        # Replace event_box+label with an Entry
        title_event_box.hide()

        entry = Gtk.Entry()
        entry.set_text(meeting.title or meeting.time_label)
        entry.set_hexpand(True)
        title_box.pack_start(entry, False, False, 0)
        title_box.reorder_child(entry, 0)
        entry.show()
        entry.grab_focus()
        entry.select_region(0, -1)

        # Restore scroll position after layout settles
        GLib.idle_add(lambda: vadj.set_value(saved_scroll))

        focus_out_id = [None]

        def _commit(*_):
            if focus_out_id[0] is not None:
                entry.disconnect(focus_out_id[0])
                focus_out_id[0] = None
            new_title = entry.get_text().strip()
            title_box.remove(entry)
            title_event_box.show()

            if not new_title or new_title == (meeting.title or meeting.time_label):
                return  # no change

            # Rename in background
            def _bg():
                try:
                    write_metadata(meeting.path, {
                        "title": new_title,
                    })
                    new_path = rename_meeting_dir(meeting, new_title)
                    meeting.path = new_path
                    meeting.title = new_title
                    meeting.time_label = new_path.name
                    idle_call(_update_label, new_title)
                except Exception as exc:
                    logger.warning("Inline rename failed: %s", exc)
                    idle_call(_update_label, None)

            def _update_label(title):
                if title:
                    primary_label.set_text(title)

            threading.Thread(target=_bg, daemon=True).start()

        def _cancel(*_):
            if focus_out_id[0] is not None:
                entry.disconnect(focus_out_id[0])
                focus_out_id[0] = None
            title_box.remove(entry)
            title_event_box.show()

        def _on_key_press(widget, event):
            if event.keyval == Gdk.KEY_Escape:
                _cancel()
                return True
            return False

        entry.connect("activate", _commit)
        entry.connect("key-press-event", _on_key_press)
        focus_out_id[0] = entry.connect("focus-out-event", lambda *_: _commit())

    def _on_title_double_click(self, widget, event, row_data: dict) -> bool:
        """On double-click, start the inline editing process."""
        if event.type != Gdk.EventType._2BUTTON_PRESS:
            return False
        self._start_inline_edit(row_data)
        return True  # stop propagation

    # -- Delete ----------------------------------------------------------------

    def _on_delete_clicked(self, *_) -> None:
        selected = [rd for rd in self._meeting_rows if rd["check"].get_active()]
        if not selected:
            return
        self._confirm_and_delete(selected)

    def _confirm_and_delete(self, rows: list[dict]) -> None:
        count = len(rows)
        dialog = Gtk.MessageDialog(
            transient_for=self.get_toplevel(),
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Delete {count} meeting{'s' if count != 1 else ''}?",
        )
        dialog.format_secondary_text("This cannot be undone.")
        response = dialog.run()
        dialog.destroy()

        if response != Gtk.ResponseType.YES:
            return

        meetings_to_delete = [rd["meeting"] for rd in rows]

        def _bg():
            cfg = settings.load()
            output_folder = cfg.get("output_folder", "~/meetings")
            succeeded, failures = delete_meetings(meetings_to_delete, output_folder)
            idle_call(_done, succeeded, failures, rows)

        def _done(succeeded, failures, rows):
            succeeded_paths = {m.path for m in succeeded}
            for rd in rows:
                if rd["meeting"].path in succeeded_paths:
                    self._list_box.remove(rd["row"])
                    self._meeting_rows.remove(rd)
            if failures:
                msgs = [f"{m.time_label}: {err}" for m, err in failures]
                self._error_label.set_markup(
                    f'<span foreground="red">Failed to delete: {GLib.markup_escape_text("; ".join(msgs))}</span>'
                )
                self._error_label.show()
            self._update_delete_sensitivity()
            if not self._meeting_rows:
                self._empty_label.show()
                self._list_box.hide()

        threading.Thread(target=_bg, daemon=True).start()

    # -- AI Title Generation ---------------------------------------------------

    def _on_ai_title_clicked(self, row_data: dict) -> None:
        meeting = row_data["meeting"]
        ai_box = row_data["ai_box"]

        # Replace AI button with spinner
        for child in ai_box.get_children():
            ai_box.remove(child)
        spinner = Gtk.Spinner()
        spinner.start()
        ai_box.pack_start(spinner, False, False, 0)
        ai_box.show_all()

        def _bg():
            try:
                notes_path = meeting.path / "notes.md"
                if not notes_path.exists():
                    raise RuntimeError("notes.md not found")

                notes_text = notes_path.read_text(encoding="utf-8")
                cfg = settings.load()

                service = cfg.get("summarization_service", "gemini")
                if service == "gemini" and not cfg.get("gemini_api_key"):
                    raise RuntimeError("Gemini API key is not configured. Please open Settings.")

                # Construct provider directly with title prompt
                provider = self._build_title_provider(cfg)
                title = provider.summarize(notes_text)

                # Clean up the title
                title = title.strip().strip('"').strip("'").strip()
                if not title:
                    raise RuntimeError("LLM returned empty title")

                # Write metadata BEFORE rename (path must still be valid)
                write_metadata(meeting.path, {
                    "title": title,
                    "generated_at": datetime.now().isoformat(),
                })

                # Rename folder on disk
                new_path = rename_meeting_dir(meeting, title)
                meeting.path = new_path
                meeting.title = title
                meeting.time_label = new_path.name

                idle_call(_done, title, None)

            except Exception as exc:
                idle_call(_done, None, str(exc))

        def _done(title, error):
            for child in ai_box.get_children():
                ai_box.remove(child)

            if title:
                row_data["primary_label"].set_text(title)
            else:
                # Show error and restore AI button
                row_data["secondary_label"].set_markup(
                    f'<span size="small" foreground="red">{GLib.markup_escape_text(error or "Unknown error")}</span>'
                )
                ai_box.pack_start(row_data["ai_btn"], False, False, 0)

            ai_box.show_all()

        threading.Thread(target=_bg, daemon=True).start()

    @staticmethod
    def _build_title_provider(config: dict):
        """Construct a summarization provider with the title-generation prompt."""
        from meeting_recorder.processing.summarization import create_summarization_provider
        return create_summarization_provider({
            **config,
            "summarization_prompt": config.get("title_prompt") or TITLE_PROMPT,
        })
