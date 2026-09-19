"""Fetching pages from the web (T-501 feeds, T-502 fetch_url).

- ``HttpFetcher``: the live fetcher (``TOOLS_PROFILE=live``). Only http/https, a timeout, a size
  cap, a bounded number of redirects, and **no private addresses**: a URL (from a feed, a search
  result, or a model) that resolves to loopback, private, link-local or reserved addresses is
  refused before connecting, and every redirect is checked again. Without that, a model could be
  told to "fetch" the API on localhost or a cloud metadata endpoint.
- ``FixtureFetcher``: serves files from a directory by URL (``TOOLS_PROFILE=fixture``). No network.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "AutoraNewsroom/0.1 (+https://github.com/vince115/autora)"


@dataclass(frozen=True)
class FetchedPage:
    url: str
    """The final URL, after redirects."""
    status: int
    content_type: str
    body: bytes


class FetchError(Exception):
    retryable = False


class FetchUnavailable(FetchError):
    """Timeouts, connection errors, 429 and 5xx: may work later."""

    retryable = True


class FetchRefused(FetchError):
    """Not fetched on principle: bad scheme, private address, too large, 4xx."""


class PageFetcher(Protocol):
    async def fetch(self, url: str) -> FetchedPage: ...


Resolver = Callable[[str], Awaitable[list[str]]]


async def _resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class HttpFetcher:
    def __init__(
        self,
        *,
        timeout_s: float = 15.0,
        max_bytes: int = 5_000_000,
        max_redirects: int = 5,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver = _resolve,
    ):
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self._client = client
        self._resolve = resolver

    async def _check(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchRefused(f"only http(s) URLs can be fetched: {url[:200]}")
        host = parts.hostname
        try:
            addresses = [host] if _is_ip(host) else await self._resolve(host)
        except OSError:
            raise FetchUnavailable(f"cannot resolve {host}") from None
        if not addresses or not all(_public(a) for a in addresses):
            raise FetchRefused(f"{host} is not a public address")

    async def fetch(self, url: str) -> FetchedPage:
        if self._client is not None:
            return await self._fetch(self._client, url)
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
            return await self._fetch(client, url)

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> FetchedPage:
        current = url
        for _ in range(self.max_redirects + 1):
            await self._check(current)
            try:
                async with client.stream(
                    "GET", current, timeout=self.timeout_s, follow_redirects=False
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchRefused(f"redirect without a location from {current}")
                        current = urljoin(current, location)
                        continue
                    _raise_for_status(response.status_code, current)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) > self.max_bytes:
                            raise FetchRefused(f"{current} is larger than {self.max_bytes} bytes")
                    return FetchedPage(
                        url=str(response.url),
                        status=response.status_code,
                        content_type=response.headers.get("content-type", ""),
                        body=bytes(body),
                    )
            except httpx.TimeoutException:
                raise FetchUnavailable(
                    f"{current} did not answer within {self.timeout_s}s"
                ) from None
            except httpx.HTTPError as exc:
                raise FetchUnavailable(f"{current}: {type(exc).__name__}") from None
        raise FetchRefused(f"more than {self.max_redirects} redirects from {url}")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _raise_for_status(status: int, url: str) -> None:
    if status == 429 or status >= 500:
        raise FetchUnavailable(f"{url} answered {status}")
    if status >= 400:
        raise FetchRefused(f"{url} answered {status}")


class FixtureFetcher:
    """Serves ``root/<file>`` for each URL in ``routes`` (URL -> file name, relative to root)."""

    def __init__(self, root: Path, routes: Mapping[str, str]):
        self._root = root
        self._routes = dict(routes)

    async def fetch(self, url: str) -> FetchedPage:
        name = self._routes.get(url)
        if name is None:
            raise FetchRefused(f"{url} answered 404 (no fixture)")
        path = (self._root / name).resolve()
        if self._root.resolve() not in path.parents:
            raise FetchRefused(f"fixture {name!r} is outside the fixture directory")
        suffix = path.suffix.lower()
        content_type = {
            ".xml": "application/rss+xml",
            ".atom": "application/atom+xml",
            ".html": "text/html; charset=utf-8",
        }.get(suffix, "application/octet-stream")
        return FetchedPage(url=url, status=200, content_type=content_type, body=path.read_bytes())


__all__ = [
    "FetchError",
    "FetchRefused",
    "FetchUnavailable",
    "FetchedPage",
    "FixtureFetcher",
    "HttpFetcher",
    "PageFetcher",
]
