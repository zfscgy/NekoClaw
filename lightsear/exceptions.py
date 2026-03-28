"""Errors raised when a search engine blocks or rejects the request."""


class LightsearError(Exception):
    """Base error for lightsear."""


class CaptchaError(LightsearError):
    """The engine returned a CAPTCHA or anti-bot challenge."""
