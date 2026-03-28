"""XPath helpers adapted from SearXNG ``searx.utils`` (minimal subset)."""

from __future__ import annotations

import typing as t
from numbers import Number

from lxml import html
from lxml.etree import XPath, XPathError, XPathSyntaxError

if t.TYPE_CHECKING:
    from lxml.etree import ElementBase, _Element  # pyright: ignore[reportPrivateUsage]

    ElementType = ElementBase | _Element
else:
    from lxml.etree import _Element  # pyright: ignore[reportPrivateUsage]

    ElementType = _Element

_XPATH_CACHE: dict[str, XPath] = {}


def _get_xpath(xpath_spec: str | XPath) -> XPath:
    if isinstance(xpath_spec, XPath):
        return xpath_spec
    result = _XPATH_CACHE.get(xpath_spec)
    if result is None:
        try:
            result = XPath(xpath_spec)
        except XPathSyntaxError as e:
            raise ValueError(f"Invalid XPath: {xpath_spec}: {e}") from e
        _XPATH_CACHE[xpath_spec] = result
    return result


def eval_xpath(element: ElementType, xpath_spec: str | XPath) -> t.Any:
    xpath = _get_xpath(xpath_spec)
    try:
        return xpath(element)
    except XPathError as e:
        raise ValueError(f"XPath evaluation failed: {xpath_spec}: {e}") from e


def eval_xpath_list(element: ElementType, xpath_spec: str | XPath) -> list[t.Any]:
    result = eval_xpath(element, xpath_spec)
    if not isinstance(result, list):
        raise ValueError(f"XPath result is not a list: {xpath_spec}")
    return result


def eval_xpath_getindex(
    element: ElementType,
    xpath_spec: str | XPath,
    index: int,
    default: t.Any = None,
) -> t.Any:
    result = eval_xpath_list(element, xpath_spec)
    if -len(result) <= index < len(result):
        return result[index]
    return default


def extract_text(
    xpath_results: list[ElementType] | ElementType | str | Number | bool | None,
    allow_none: bool = False,
) -> str | None:
    if isinstance(xpath_results, list):
        out = ""
        for e in xpath_results:
            out += extract_text(e) or ""
        return out.strip()
    if isinstance(xpath_results, ElementType):
        text: str = html.tostring(
            xpath_results,
            encoding="unicode",
            method="text",
            with_tail=False,
        )
        text = text.strip().replace("\n", " ")
        return " ".join(text.split())
    if isinstance(xpath_results, (str, Number, bool)):
        return str(xpath_results)
    if xpath_results is None and allow_none:
        return None
    if xpath_results is None:
        raise ValueError("extract_text(None)")
    raise ValueError("unsupported type")
