from ddgs import DDGS


def search_duckduckgo(query: str, n: int) -> list[dict[str, str]]:
    """Return up to n DuckDuckGo text search results for the given query.

    Each result is a dict with keys: title, href, body.
    """
    with DDGS() as ddgs:
        return ddgs.text(query, max_results=n, backend="google,bing,brave,duckduckgo,yahoo,wkipedia,grokipedia")
