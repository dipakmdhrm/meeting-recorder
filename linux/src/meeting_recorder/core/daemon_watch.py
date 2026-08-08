"""Pure policy: when should a spawned window exit because its daemon is gone?

A window is a child of the daemon that spawned it. With "keep window in memory"
(the default), closing the window only hides it — the process stays resident. If
the daemon then quits or crashes, that hidden window would be orphaned: it keeps
its D-Bus subscription and would still react to a *new* daemon's PresentWindow
broadcast, so windows accumulate one per prior daemon session.

The invariant "a spawned window must never outlive its daemon" is enforced by
watching the Engine bus name: once we've seen the daemon own it, its later
disappearance means this window is orphaned and must exit. We only act after the
name has been seen owned so a spurious "no owner yet" at startup can't kill a
window before its daemon has finished acquiring the name.
"""

from __future__ import annotations

__all__ = ["should_exit_on_owner_change"]


def should_exit_on_owner_change(daemon_seen: bool, has_owner: bool) -> bool:
    """Return True if the window should exit given a bus-name owner change.

    ``daemon_seen`` — whether the daemon has been observed owning the name at
    least once during this window's lifetime.
    ``has_owner`` — whether the name currently has an owner.
    """
    return daemon_seen and not has_owner
