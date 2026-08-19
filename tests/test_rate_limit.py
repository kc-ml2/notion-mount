from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from notion_client.errors import RequestTimeoutError

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
    instance.retry_forever = False
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


def test_request_retries_notion_client_timeout_without_an_http_response() -> None:
    sleeps: list[float] = []
    notion = backend(sleeps)
    calls = 0

    def request() -> dict[str, bool]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RequestTimeoutError()
        return {"ok": True}

    assert notion._request(request) == {"ok": True}
    assert calls == 2
    assert len(sleeps) == 1


@pytest.mark.parametrize(
    "error_type",
    [
        httpx.ConnectError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.RemoteProtocolError,
        httpx.ProxyError,
        httpx.CloseError,
    ],
)
def test_all_httpx_transport_errors_are_retried(error_type) -> None:
    sleeps: list[float] = []
    notion = backend(sleeps)
    calls = 0

    def request() -> dict[str, bool]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise error_type("transient transport failure")
        return {"ok": True}

    assert notion._request(request) == {"ok": True}
    assert calls == 2
    assert len(sleeps) == 1


def test_retry_forever_continues_past_finite_retry_limit() -> None:
    sleeps: list[float] = []
    notion = backend(sleeps)
    notion.max_retries = 1
    notion.retry_forever = True
    calls = 0

    def request() -> dict[str, bool]:
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise RequestTimeoutError()
        return {"ok": True}

    assert notion._request(request) == {"ok": True}
    assert calls == 4
    assert len(sleeps) == 3


def test_retry_forever_still_rejects_permanent_errors_immediately() -> None:
    notion = backend([])
    notion.retry_forever = True
    calls = 0

    def request() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("invalid request")

    try:
        notion._request(request)
    except ValueError:
        pass
    else:
        raise AssertionError("permanent error was swallowed")
    assert calls == 1


def test_backoff_remains_bounded_for_very_large_attempt_counts() -> None:
    assert 30 <= NotionClientBackend._backoff(1_000_000) < 31


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
