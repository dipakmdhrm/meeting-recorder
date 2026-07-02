"""Per-tab pages for the settings dialog.

Each page class builds one tab of the Settings window (a scrolled column of
``Adw.PreferencesGroup``s), exposes it as ``.widget``, and writes its values
back into a config dict via ``.apply(cfg)`` when the dialog saves. The dialog
itself (``meeting_recorder.ui.settings_dialog``) stays a thin shell: header
chrome, page instantiation, and the save flow.
"""

from .general import GeneralPage
from .models import ModelsPage
from .prompts import PromptsPage
from .widgets import IdComboRow

__all__ = ["GeneralPage", "IdComboRow", "ModelsPage", "PromptsPage"]
