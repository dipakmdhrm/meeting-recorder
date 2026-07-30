"""
The always-on engine daemon (GTK-free).

Owns the recording lifecycle, job queue, processing pipeline, call detection,
and the system tray. Runs a GLib main loop with only ``Gio``/``GLib`` loaded —
no GTK/libadwaita — so it sits in the tray at a fraction of the memory of the
full app. The GTK window is a separate child process (see ``ui/window_app.py``)
spawned on demand and reclaimed by the OS when closed.
"""
