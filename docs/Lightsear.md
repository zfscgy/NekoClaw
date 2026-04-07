# Lightsear

Lightweight web search adapters for Python. Queries multiple search engines and returns merged, structured results. 
Logic is derived from [SearXNG](https://github.com/searxng/searxng) engine implementations.
Fetching pages is done by using [Scrapling](https://github.com/D4Vinci/Scrapling) `StealthySession`.

## Supported engines


| Engine     | Key          |
| ---------- | ------------ |
| Google     | `google`     |
| Bing       | `bing`       |
| DuckDuckGo | `duckduckgo` |
| Baidu      | `baidu`      |


## Installation

(TODO)

```bash
pip install lightsear
```

Requires Python 3.10+.

## Usage

```python
import lightsear

# Search all engines
results = lightsear.search("python web scraping")

# Search specific engines
results = lightsear.search("python web scraping", sources=["google", "duckduckgo"])

# With a proxy and custom timeout
results = lightsear.search(
    "python web scraping",
    sources=["google"],
    timeout=30.0,
    proxy="http://127.0.0.1:10808",
)

for r in results:
    print(r.source, r.title, r.url)
    print(r.content)
```

Each result is a `SearchResult` dataclass:

```python
@dataclass(frozen=True)
class SearchResult:
    title: str
    content: str  # snippet / description
    url: str
    sources: str   # engine name (splitted by comma)
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

Logic adapted from SearXNG (AGPL-3.0-or-later).