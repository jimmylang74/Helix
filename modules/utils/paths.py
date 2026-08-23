"""Project root path — single source of truth for path anchoring.

All modules must resolve project-relative paths via PROJECT_ROOT instead of
counting os.path.dirname levels from their own __file__, so moving files
within the project can never break path resolution.

Do NOT move this file: it sits at <root>/modules/utils/ and derives the root
from its own location.
"""

import os

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


def project_path(*parts: str) -> str:
    """Join path parts onto PROJECT_ROOT."""
    return os.path.join(PROJECT_ROOT, *parts)
