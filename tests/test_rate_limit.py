from __future__ import annotations

from types import SimpleNamespace

from notion_mount.notion import NotionClientBackend


class RateLimitError(Exception):
    def __init__(self, retry_after: str = "2") -> None:
        self.response = SimpleNamespace(
            status_code=429, headers={"retry-after": retry_after}
        )
        self.code = "rate_limited"


def backend(sleeps: list[float]) -> NotionClientBackend:
    instance = object.__new__(NotionClientBackend)
    instance.requests_per_second = 0
    instance.max_retries = 3
    instance._sleep = sleeps.append
    instance._clock = lambda: 0.0
    instance._last_request_at = None
    instance._progress = None
    return instance


def test_request_retries_rate_limit_using_retry_after() -> None:
    sleeps: list[float] = []
    notion = backend(sleeps)
    calls = 0

    def request() -> dict[str, bool]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RateLimitError("1.5")
        return {"ok": True}

    assert notion._request(request) == {"ok": True}
    assert calls == 2
    assert sleeps == [1.5]


def test_request_does_not_retry_non_transient_errors() -> None:
    notion = backend([])

    def request() -> None:
        raise ValueError("invalid request")

    try:
        notion._request(request)
    except ValueError as error:
        assert str(error) == "invalid request"
    else:
        raise AssertionError("non-transient error was swallowed")
