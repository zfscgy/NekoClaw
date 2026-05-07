"""Runtime management helpers exposed to channel APIs.

This package provides user-facing management operations (installing/enabling
/disabling skills, deleting persisted sessions, etc.) that need to be callable
at runtime from the NekoChat web UI.

Channels and other consumers should import the public helpers exposed here
rather than reaching into the submodules directly.

Configuration management lives in :mod:`nekoclaw.config.manager`.
"""

from nekoclaw.manager.sessions import delete_session
from nekoclaw.manager.skills import (
    SkillSource,
    SkillStatus,
    add_skill_from_directory,
    add_skill_from_zip,
    disable_skill,
    enable_skill,
    get_loader,
    list_skills,
)

__all__ = [
    "SkillSource",
    "SkillStatus",
    "add_skill_from_directory",
    "add_skill_from_zip",
    "delete_session",
    "disable_skill",
    "enable_skill",
    "get_loader",
    "list_skills",
]
