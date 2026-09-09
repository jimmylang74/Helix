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


def get_download_dir() -> str:
    """Return the WeChat iLinkBot download directory as an absolute path.

    Reads the ``channels.wechat.download_dir`` config value (relative to the
    project root) and resolves it against PROJECT_ROOT so downloads always land
    inside the project regardless of the current working directory.
    """
    from modules.config.config_manager import ConfigManager

    rel = ConfigManager().get_wechat_download_dir()
    return project_path(rel)
