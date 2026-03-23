import requests

from nanobot.config import load_config


search_config = load_config().tools.web.search


def searxng_search(text: str, max_results: int = search_config.max_results) -> list[dict[str, str]]:
    """Return up to max_results search results for the given text.

    Each result is a dict with keys: title, url, body, source.
    """
    searxng_url = search_config.searxng_url
    url = f"{searxng_url}/search"
    params = {
        "q": text,
        "format": "json"
    }
    response = requests.get(url, params=params)
    results = [{
        "title": r["title"],
        "body": r["content"],
        "url": r["url"],
        "source": r["engine"]
    } for r in response.json()["results"][:max_results]]

    return results