"""Browser-like User-Agent strings (inspired by SearXNG ``gen_useragent`` / ``gen_gsa_useragent``)."""

from __future__ import annotations

import random

_CHROME_VERSIONS = ["131.0.0.0", "130.0.0.0", "129.0.0.0"]


def gen_useragent() -> str:
    ver = random.choice(_CHROME_VERSIONS)
    return (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{ver} Safari/537.36"
    )


def gen_gsa_useragent() -> str:
    """Mobile Chrome-style UA often used for Google HTML SERP."""
    ver = random.choice(_CHROME_VERSIONS)
    return (
        f"Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{ver} Mobile Safari/537.36 "
        f"GoogleApp/{random.randint(10, 14)}.{random.randint(0, 99)}"
    )
