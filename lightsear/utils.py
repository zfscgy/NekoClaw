from __future__ import annotations

import re

__all__ = ["decode_url_chinese_only"]

_PERCENT_RUN_RE = re.compile(r"(?:%[0-9A-Fa-f]{2})+")

# Unicode ranges covering common Han characters. These are the only code
# points that get unescaped; everything else is left percent-encoded so the
# URL stays syntactically safe.
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x4E00, 0x9FFF),    # CJK Unified Ideographs
    (0x3400, 0x4DBF),    # CJK Extension A
    (0x20000, 0x2A6DF),  # CJK Extension B
    (0x2A700, 0x2B73F),  # CJK Extension C
    (0x2B740, 0x2B81F),  # CJK Extension D
    (0x2B820, 0x2CEAF),  # CJK Extension E
    (0xF900, 0xFAFF),    # CJK Compatibility Ideographs
)


def _is_chinese_char(ch: str) -> bool:
    code = ord(ch)
    return any(start <= code <= end for start, end in _CJK_RANGES)


def _percent_encode_bytes(data: bytes) -> str:
    return "".join(f"%{b:02X}" for b in data)


def _decode_chinese_only_in_percent_run(match: re.Match[str]) -> str:
    encoded = match.group(0)
    try:
        raw = bytes.fromhex(encoded.replace("%", ""))
        decoded = raw.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return encoded

    parts: list[str] = []
    for ch in decoded:
        if _is_chinese_char(ch):
            parts.append(ch)
        else:
            # Re-encode non-Chinese bytes so reserved/unsafe characters such as
            # '/', '?', '#', or control bytes remain escaped in the URL.
            parts.append(_percent_encode_bytes(ch.encode("utf-8")))
    return "".join(parts)


def decode_url_chinese_only(url: str) -> str:
    """Decode only percent-encoded Chinese characters within ``url``.

    Other percent-encoded bytes (reserved characters, ASCII punctuation, etc.)
    are left intact so the URL remains valid. Useful for rendering URLs in a
    human-readable form without breaking their structure.
    """
    return _PERCENT_RUN_RE.sub(_decode_chinese_only_in_percent_run, url)
