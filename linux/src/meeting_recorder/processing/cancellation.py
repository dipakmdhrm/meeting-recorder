"""
The cancellation exception shared by the pipeline and the providers it drives.

It lives in its own module so a provider can raise it without importing
``pipeline`` (which imports providers lazily inside its own functions).
``pipeline`` re-exports ``PipelineCancelled`` for existing importers.
"""

from __future__ import annotations


class PipelineCancelled(Exception):
    """Raised when a job's CancelToken is set between pipeline stages or chunks."""
