"""Best-effort checks for shell command execution."""

import re
from pathlib import Path

_DEFAULT_DENY_PATTERNS = [
    r"\brm\s+-[rf]{1,2}\b",
    r"\bdel\s+/[fq]\b",
    r"\brmdir\s+/s\b",
    r"(?:^|[;&|]\s*)format\b",
    r"\b(mkfs|diskpart)\b",
    r"\bdd\s+if=",
    r">\s*/dev/sd",
    r"\b(shutdown|reboot|poweroff)\b",
    r":\(\)\s*\{.*\};\s*:",
]


def _extract_absolute_paths(command: str) -> list[str]:
    win_paths = re.findall(r"[A-Za-z]:\\[^\s\"'|><;]+", command)
    posix_paths = re.findall(r"(?:^|[\s|>])(/[^\s\"'>]+)", command)
    return win_paths + posix_paths


def check(
    command: str,
    cwd: str,
    *,
    deny_patterns: list[str] | None = None,
    allow_patterns: list[str] | None = None,
    restrict_to_workspace: bool = False,
) -> str | None:
    """Return an error message if the command should be blocked, else None."""
    cmd = command.strip()
    lower = cmd.lower()
    denies = deny_patterns if deny_patterns is not None else list(_DEFAULT_DENY_PATTERNS)

    for pattern in denies:
        if re.search(pattern, lower):
            return "Error: Command blocked by safety guard (dangerous pattern detected)"

    allows = allow_patterns or []
    if allows and not any(re.search(p, lower) for p in allows):
        return "Error: Command blocked by safety guard (not in allowlist)"

    if restrict_to_workspace:
        if "..\\" in cmd or "../" in cmd:
            return "Error: Command blocked by safety guard (path traversal detected)"

        cwd_path = Path(cwd).resolve()
        for raw in _extract_absolute_paths(cmd):
            try:
                p = Path(raw.strip()).resolve()
            except Exception:
                continue
            if p.is_absolute() and cwd_path not in p.parents and p != cwd_path:
                return "Error: Command blocked by safety guard (path outside working dir)"
    return None
