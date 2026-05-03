from __future__ import annotations

import re

try:
    import chardet
except ImportError:  # pragma: no cover - dependency is declared for NekoClaw
    chardet = None  # type: ignore[assignment]


_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset=["']?\s*([a-zA-Z0-9._:-]+)|"""
    rb"""<meta[^>]+content=["'][^"']*charset=([a-zA-Z0-9._:-]+)""",
    re.IGNORECASE,
)

_ENCODING_ALIASES = {
    "gb2312": "gb18030",
    "gbk": "gb18030",
}


def _normalise_encoding(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = name.strip().strip("\"'").lower()
    return _ENCODING_ALIASES.get(cleaned, cleaned)


def _meta_charset(body: bytes) -> str | None:
    head = body[:4096]
    match = _CHARSET_RE.search(head)
    if match is None:
        return None
    raw = next((group for group in match.groups() if group), None)
    if raw is None:
        return None
    return _normalise_encoding(raw.decode("ascii", errors="ignore"))


def decode_html_body(body: bytes | str) -> str:
    """Decode HTML bytes without letting stale meta charsets corrupt UTF-8 DOM dumps."""
    if isinstance(body, str):
        return body

    detected = None
    if chardet is not None:
        result = chardet.detect(body)
        if (result.get("confidence") or 0) >= 0.5:
            detected = _normalise_encoding(result.get("encoding"))

    candidates = [
        "utf-8",
        detected,
        _meta_charset(body),
        "gb18030",
        "big5",
        "windows-1252",
    ]

    seen: set[str] = set()
    for candidate in candidates:
        encoding = _normalise_encoding(candidate)
        if not encoding or encoding in seen:
            continue
        seen.add(encoding)
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue

    return body.decode("utf-8", errors="replace")
