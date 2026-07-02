"""
The Background Jobs panel: an Adw.PreferencesGroup of job rows.

Extracted from MainWindow so the window only forwards actions. Which buttons
a row offers comes from the pure `actions_for_status()` policy in core/job.py;
this module only renders. All methods are main-thread only.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from ..core.job import Job, actions_for_status
from ..core.job import JobStatus as JobStatus
from ..utils.glib_bridge import assert_main_thread
from ..utils.gtk_compat import remove_all_children


class JobsPanel:
    """Renders job rows and forwards row actions to the window's handlers."""

    def __init__(
        self,
        on_cancel: Callable[[Job], None],
        on_retry: Callable[[Job], None],
        on_open_folder: Callable[[Job], None],
        on_dismiss: Callable[[Job], None],
    ) -> None:
        self._on_cancel = on_cancel
        self._on_retry = on_retry
        self._on_open_folder = on_open_folder
        self._on_dismiss = on_dismiss

        self.widget = Adw.PreferencesGroup(title="Background Jobs")
        self.widget.set_visible(False)
        self._rows: dict[int, dict] = {}

    # ------------------------------------------------------------------

    def add_job(self, job: Job) -> None:
        """Add a row for a new job."""
        assert_main_thread()

        row = Adw.ActionRow(title=job.label, subtitle="Processing…")
        row.set_title_lines(1)

        spinner = Gtk.Spinner()
        spinner.start()
        spinner.set_valign(Gtk.Align.CENTER)
        row.add_prefix(spinner)

        status_icon = Gtk.Image.new_from_icon_name("system-run-symbolic")
        status_icon.set_valign(Gtk.Align.CENTER)
        status_icon.set_visible(False)
        row.add_prefix(status_icon)

        action_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        action_box.set_valign(Gtk.Align.CENTER)
        row.add_suffix(action_box)

        self._rows[job.job_id] = {
            "row": row,
            "spinner": spinner,
            "status_icon": status_icon,
            "action_box": action_box,
        }
        self._rebuild_action_box(job)

        self.widget.add(row)
        self.widget.set_visible(True)

    def update_job(self, job: Job) -> None:
        """Refresh icon, status text, and action buttons for a status change."""
        assert_main_thread()
        widgets = self._rows.get(job.job_id)
        if not widgets:
            return

        spinner: Gtk.Spinner = widgets["spinner"]
        status_icon: Gtk.Image = widgets["status_icon"]
        row: Adw.ActionRow = widgets["row"]

        if job.status is JobStatus.PROCESSING:
            # A retried job goes back to spinning instead of keeping the
            # stale error icon and message.
            status_icon.set_visible(False)
            spinner.set_visible(True)
            spinner.start()
            row.set_subtitle("Processing…")
        else:
            spinner.stop()
            spinner.set_visible(False)
            status_icon.set_visible(True)
            if job.status is JobStatus.DONE:
                status_icon.set_from_icon_name("emblem-ok-symbolic")
                row.set_subtitle("Done")
            elif job.status is JobStatus.ERROR:
                status_icon.set_from_icon_name("dialog-error-symbolic")
                err = (job.error_msg or "Error")[:60]
                row.set_subtitle(f"Error: {err}")

        self._rebuild_action_box(job)

    def set_status_text(self, job: Job, msg: str) -> None:
        """Update a row's subtitle with pipeline progress."""
        assert_main_thread()
        widgets = self._rows.get(job.job_id)
        if widgets:
            widgets["row"].set_subtitle(msg)

    def remove_job(self, job: Job) -> None:
        """Remove a job's row; hides the panel when the last row goes."""
        assert_main_thread()
        widgets = self._rows.pop(job.job_id, None)
        if widgets:
            self.widget.remove(widgets["row"])
        if not self._rows:
            self.widget.set_visible(False)

    # ------------------------------------------------------------------

    def _rebuild_action_box(self, job: Job) -> None:
        widgets = self._rows.get(job.job_id)
        if not widgets:
            return
        action_box: Gtk.Box = widgets["action_box"]
        remove_all_children(action_box)

        handlers = {
            "cancel": ("Cancel", self._on_cancel),
            "open_folder": ("Open Folder", self._on_open_folder),
            "retry": ("Retry", self._on_retry),
        }
        for action in actions_for_status(job.status):
            if action == "dismiss":
                btn = Gtk.Button(icon_name="window-close-symbolic")
                btn.set_tooltip_text("Dismiss")
                btn.connect("clicked", lambda *_, j=job: self._on_dismiss(j))
            else:
                label, handler = handlers[action]
                btn = Gtk.Button(label=label)
                btn.connect("clicked", lambda *_, j=job, h=handler: h(j))
            btn.add_css_class("flat")
            action_box.append(btn)
