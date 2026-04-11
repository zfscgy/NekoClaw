# Lightsear

Lightweight web search adapters for Python. Queries multiple search engines and returns merged, structured results.
Logic is derived from [SearXNG](https://github.com/searxng/searxng) engine implementations.
Page fetching is powered by [Playwright](https://playwright.dev/python/) connecting to a real Chromium instance via the Chrome DevTools Protocol (CDP).

## Supported engines

| Engine     | Key          |
| ---------- | ------------ |
| Google     | `google`     |
| Bing       | `bing`       |
| DuckDuckGo | `duckduckgo` |
| Baidu      | `baidu`      |


## Installation

```bash
pip install lightsear
```

Requires Python 3.10+.

## Usage

### 1. Initialize the pool

Call `initialize_pool` once before using `search` or `web_fetch`. It launches a
Chromium subprocess, waits for the CDP debug port to become ready, and creates an
internal session pool.

```python
import lightsear

lightsear.initialize_pool(
    chrome_executable_path=r"/usr/bin/chromium",  # path to Chrome/Chromium binary
    user_data_dir="/tmp/chrome-profile",          # persistent browser profile directory
)
```

The pool is shut down automatically on process exit via `atexit`.

#### `initialize_pool` parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `chrome_executable_path` | `str` | *(required)* | Path to the Chrome/Chromium executable |
| `user_data_dir` | `str` | *(required)* | Directory for the persistent browser profile |
| `cdp_port` | `int` | `9222` | Remote-debugging port Chromium listens on |
| `cdp_host` | `str` | `"localhost"` | Address Chromium binds the debug port to |
| `headless` | `bool` | `True` | Run in headless mode |
| `proxy` | `str \| None` | `None` | Proxy URL, e.g. `"http://127.0.0.1:10808"` |
| `timeout` | `float` | `20.0` | Per-request browser timeout in seconds |
| `pool_size` | `int \| None` | number of engines | Number of concurrent browser sessions |
| `startup_timeout` | `float` | `15.0` | Seconds to wait for Chromium to become ready |

### 2. Search

```python
# Search all engines
results = lightsear.search("python web scraping")

# Search specific engines
results = lightsear.search("python web scraping", sources=["google", "duckduckgo"])

# Run sequentially instead of in parallel
results = lightsear.search("python web scraping", parallel=False)

for r in results:
    print(r.sources, r.title, r.url)
    print(r.content)
```

Results are aggregated by URL across all queried engines. URLs returned by more than
one engine appear first (sorted by hit count descending).

### 3. Fetch a page

```python
# Returns cleaned page content as Markdown (default)
text = lightsear.web_fetch("https://example.com")

# Plain text
text = lightsear.web_fetch("https://example.com", mode="text")

# Wait longer for JS-heavy pages (milliseconds)
text = lightsear.web_fetch("https://example.com", wait=12_000)
```

## Data types

```python
@dataclass(frozen=True)
class SearchResult:
    title: str
    content: str   # snippet / description from the engine
    url: str
    sources: str   # comma-separated engine names, e.g. "google,bing"
```

## Advanced: custom session pool

You can build a `SessionPool` directly and pass a `session_factory` for full
control over the browser session (e.g. to reuse an already-running Chromium):

```python
from lightsear import SessionPool
from lightsear.playwright_client import PlaywrightCDPSession

with SessionPool(
    size=2,
    session_factory=lambda: PlaywrightCDPSession(cdp_port=9222, timeout=20_000),
) as pool:
    future = pool.submit(lambda session: session.fetch("https://example.com"))
    response = future.result()
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

Logic adapted from SearXNG (AGPL-3.0-or-later).
