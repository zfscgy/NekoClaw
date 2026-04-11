from nekoclaw.tools.web import lightsear_fetch


def test__web_fetch_text():
    url = "https://en.wikipedia.org/wiki/Theory_of_relativity"
    text = lightsear_fetch(url, mode="text")
    assert isinstance(text, str)
    assert len(text) > 0
    print(text)


def test__web_fetch_markdown():
    url = "https://en.wikipedia.org/wiki/Theory_of_relativity"
    markdown = lightsear_fetch(url, mode="markdown")
    assert isinstance(markdown, str)
    assert len(markdown) > 0
    print(markdown)


if __name__ == "__main__":
    test__web_fetch_text()
    test__web_fetch_markdown()