from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Protocol

from .strategy import file_sha256, should_retry, validate_read_only_request


@dataclass(frozen=True)
class HttpResponse:
    requested_url: str
    final_url: str
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes
    attempt: int
    duration_ms: int

    @property
    def content_type(self) -> str | None:
        for name, value in self.headers:
            if name.lower() == "content-type":
                return value
        return None

    @property
    def sha256(self) -> str:
        return file_sha256(self.body)

    def json(self) -> object:
        return json.loads(self.body.decode("utf-8"))


class Transport(Protocol):
    def get(self, url: str, *, accept: str = "application/json") -> HttpResponse: ...


class UrllibTransport:
    """Bounded read-only HTTP transport using only the Python standard library."""

    def __init__(
        self,
        *,
        approved_hosts: list[str],
        user_agent: str,
        timeout_seconds: int,
        retry_ceiling: int,
        request_ceiling: int,
        response_size_ceiling_bytes: int,
        backoff_seconds: float = 0.5,
    ) -> None:
        self.approved_hosts = tuple(approved_hosts)
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.retry_ceiling = retry_ceiling
        self.request_ceiling = request_ceiling
        self.response_size_ceiling_bytes = response_size_ceiling_bytes
        self.backoff_seconds = backoff_seconds
        self.request_count = 0

    def get(self, url: str, *, accept: str = "application/json") -> HttpResponse:
        validate_read_only_request("GET", url, self.approved_hosts)
        if self.request_count >= self.request_ceiling:
            raise RuntimeError("request ceiling exhausted")
        self.request_count += 1

        last_error: Exception | None = None
        for attempt in range(1, self.retry_ceiling + 1):
            started = time.monotonic()
            request = urllib.request.Request(
                url,
                method="GET",
                headers={"Accept": accept, "User-Agent": self.user_agent},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    body = response.read(self.response_size_ceiling_bytes + 1)
                    if len(body) > self.response_size_ceiling_bytes:
                        raise RuntimeError("response exceeded configured size ceiling")
                    return HttpResponse(
                        requested_url=url,
                        final_url=response.geturl(),
                        status=int(response.status),
                        headers=tuple(response.headers.items()),
                        body=body,
                        attempt=attempt,
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
            except urllib.error.HTTPError as exc:
                body = exc.read(self.response_size_ceiling_bytes + 1)
                if len(body) > self.response_size_ceiling_bytes:
                    raise RuntimeError("error response exceeded configured size ceiling") from exc
                response = HttpResponse(
                    requested_url=url,
                    final_url=exc.geturl(),
                    status=int(exc.code),
                    headers=tuple(exc.headers.items()) if exc.headers else tuple(),
                    body=body,
                    attempt=attempt,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                if not should_retry(response.status, attempt, self.retry_ceiling):
                    return response
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if not should_retry(None, attempt, self.retry_ceiling):
                    break
            time.sleep(self.backoff_seconds * (2 ** (attempt - 1)))
        raise RuntimeError(f"request failed after finite retries: {last_error}")


class FixtureTransport:
    """Deterministic response transport used for repeatable integration tests."""

    def __init__(self, responses: Mapping[str, HttpResponse | Exception]) -> None:
        self.responses = dict(responses)
        self.calls: list[str] = []

    def get(self, url: str, *, accept: str = "application/json") -> HttpResponse:
        del accept
        self.calls.append(url)
        if url not in self.responses:
            raise RuntimeError(f"no fixture response for {url}")
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response
