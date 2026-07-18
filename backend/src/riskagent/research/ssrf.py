"""SSRF-hardened fetcher for the ``fetch_web_source`` tool.

Security contract (all enforced in code, none prompt-only):

* only ``http``/``https`` schemes; only ports 80/443/default; no userinfo;
* IP literals and **every** DNS-resolved address must be public — loopback,
  RFC1918 private, link-local (incl. the cloud metadata address
  169.254.169.254), CGNAT, unspecified, multicast, reserved, ``::1``,
  ``fc00::/7``, ``fe80::/10`` and ``::ffff:``-mapped-private are rejected;
* redirects are followed manually (never by httpx), capped, and **every hop
  re-runs the full scheme/port/DNS/robots validation**;
* response size, allowed content types, and total wall-clock time are capped;
* ``robots.txt`` is honored (cached per host per fetcher instance); logins,
  paywalls, and site access controls are never bypassed — a 401/403 simply
  yields an inaccessible lead card.

Every expected failure returns ``accessible=False`` with a machine-readable
``inaccessible_reason`` instead of raising: the URL survives as a lead, but
the verification gate structurally never counts such cards as evidence.

Known residual risk (documented, accepted for phase 1): we validate resolved
addresses, then httpx re-resolves on connect — a DNS-rebinding attacker
controlling TTLs could swap records between the two lookups (TOCTOU).
Per-hop re-resolution narrows the window; pinning connections to the
validated IP via a custom transport is the phase-2 hardening.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import time
from typing import Awaitable, Callable, Optional
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, Field

from .config import ResearchConfig
from .html_text import extract_text
from .models import utc_now

DEFAULT_USER_AGENT = "ProspectAlphaResearchBot/1.0"

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ALLOWED_PORTS = frozenset({None, 80, 443})
_ALLOWED_CONTENT_TYPES = frozenset(
    {"text/html", "application/xhtml+xml", "text/plain"}
)
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_ROBOTS_MAX_BYTES = 64 * 1024

Resolver = Callable[[str], Awaitable[list[str]]]


class FetchOutcome(BaseModel):
    requested_url: str
    final_url: str
    redirect_chain: list[str] = Field(default_factory=list)
    accessible: bool
    inaccessible_reason: Optional[str] = None
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    title: Optional[str] = None
    text: Optional[str] = None
    published_hint: Optional[str] = None
    content_hash: Optional[str] = None
    bytes_read: int = 0
    retrieved_at: str = Field(default_factory=lambda: utc_now().isoformat())


def _address_is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_global and not ip.is_multicast


async def _default_resolver(host: str) -> list[str]:
    import asyncio

    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class SafeFetcher:
    def __init__(
        self,
        config: Optional[ResearchConfig] = None,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        resolver: Optional[Resolver] = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._config = config or ResearchConfig()
        self._transport = transport
        self._resolver = resolver or _default_resolver
        self._user_agent = user_agent
        # robots decisions cached per scheme://host:port for this instance's
        # lifetime (one instance per run in the pipeline).
        self._robots_cache: dict[str, RobotFileParser] = {}

    async def fetch(self, url: str) -> FetchOutcome:
        deadline = time.monotonic() + self._config.fetch_total_seconds
        redirect_chain: list[str] = []
        current = url

        client_kwargs: dict = {}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport

        async with httpx.AsyncClient(**client_kwargs) as client:
            for _hop in range(self._config.fetch_max_redirects + 1):
                reason = await self._validate_target(current)
                if reason is not None:
                    return self._failure(url, current, redirect_chain, reason)

                allowed, robots_reason = await self._robots_allows(
                    client, current, deadline
                )
                if robots_reason is not None:
                    return self._failure(url, current, redirect_chain, robots_reason)
                if not allowed:
                    return self._failure(
                        url, current, redirect_chain, "robots_disallow"
                    )

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._failure(url, current, redirect_chain, "timeout")

                try:
                    async with client.stream(
                        "GET",
                        current,
                        headers={"User-Agent": self._user_agent},
                        timeout=httpx.Timeout(remaining),
                    ) as response:
                        status = response.status_code
                        if status in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                return self._failure(
                                    url, current, redirect_chain,
                                    "http_error", status_code=status,
                                )
                            redirect_chain.append(current)
                            current = urljoin(current, location)
                            continue
                        return await self._read_terminal(
                            url, current, redirect_chain, response, deadline
                        )
                except httpx.TimeoutException:
                    return self._failure(url, current, redirect_chain, "timeout")
                except httpx.HTTPError:
                    return self._failure(
                        url, current, redirect_chain, "network_error"
                    )
            return self._failure(url, current, redirect_chain, "too_many_redirects")

    # ------------------------------------------------------------------
    # validation

    async def _validate_target(self, url: str) -> Optional[str]:
        """Return a rejection reason, or None if the URL may be fetched."""
        try:
            parsed = urlsplit(url)
            port = parsed.port  # raises ValueError on absurd ports
        except ValueError:
            return "invalid_url"
        if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
            return "unsupported_scheme"
        if parsed.username or parsed.password:
            return "userinfo_present"
        if port not in _ALLOWED_PORTS:
            return "unsupported_port"
        host = parsed.hostname
        if not host:
            return "invalid_url"

        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass  # not an IP literal — resolve below
        else:
            return None if _address_is_public(host) else "private_address"

        try:
            addresses = await self._resolver(host)
        except (OSError, socket.gaierror):
            return "dns_error"
        if not addresses:
            return "dns_error"
        if not all(_address_is_public(addr) for addr in addresses):
            return "private_address"
        return None

    # ------------------------------------------------------------------
    # robots

    async def _robots_allows(
        self, client: httpx.AsyncClient, url: str, deadline: float
    ) -> tuple[bool, Optional[str]]:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        parser = self._robots_cache.get(origin)
        if parser is None:
            parser = RobotFileParser()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False, "timeout"
            try:
                response = await client.get(
                    f"{origin}/robots.txt",
                    headers={"User-Agent": self._user_agent},
                    timeout=httpx.Timeout(remaining),
                    follow_redirects=False,
                )
            except httpx.TimeoutException:
                return False, "timeout"
            except httpx.HTTPError:
                # robots endpoint unreachable: treat as absent (allow), the
                # actual page fetch will surface real network problems.
                parser.allow_all = True
            else:
                if response.status_code == 200:
                    body = response.content[:_ROBOTS_MAX_BYTES]
                    parser.parse(body.decode("utf-8", errors="replace").splitlines())
                elif response.status_code in (401, 403):
                    # mirrors urllib.robotparser: access-controlled robots
                    # means the site does not want to be crawled.
                    parser.disallow_all = True
                else:
                    parser.allow_all = True
            self._robots_cache[origin] = parser
        return parser.can_fetch(self._user_agent, url), None

    # ------------------------------------------------------------------
    # terminal response handling

    async def _read_terminal(
        self,
        requested_url: str,
        current: str,
        redirect_chain: list[str],
        response: httpx.Response,
        deadline: float,
    ) -> FetchOutcome:
        status = response.status_code
        if status in (401, 403, 404):
            return self._failure(
                requested_url, current, redirect_chain,
                f"http_{status}", status_code=status,
            )
        if not (200 <= status < 300):
            return self._failure(
                requested_url, current, redirect_chain,
                "http_error", status_code=status,
            )

        raw_content_type = response.headers.get("content-type", "")
        media_type = raw_content_type.split(";")[0].strip().lower()
        if media_type not in _ALLOWED_CONTENT_TYPES:
            # application/pdf lands here by design: PDF parsing is explicitly
            # out of scope for phase 1.
            return self._failure(
                requested_url, current, redirect_chain,
                "unsupported_content_type",
                status_code=status, content_type=media_type or None,
            )

        body = bytearray()
        try:
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > self._config.fetch_max_bytes:
                    return self._failure(
                        requested_url, current, redirect_chain, "oversize",
                        status_code=status, content_type=media_type,
                        bytes_read=len(body),
                    )
                if time.monotonic() > deadline:
                    return self._failure(
                        requested_url, current, redirect_chain, "timeout",
                        status_code=status, content_type=media_type,
                        bytes_read=len(body),
                    )
        except httpx.TimeoutException:
            return self._failure(
                requested_url, current, redirect_chain, "timeout",
                status_code=status, content_type=media_type,
            )
        except httpx.HTTPError:
            return self._failure(
                requested_url, current, redirect_chain, "network_error",
                status_code=status, content_type=media_type,
            )

        charset = response.charset_encoding or "utf-8"
        try:
            decoded = bytes(body).decode(charset, errors="replace")
        except LookupError:
            decoded = bytes(body).decode("utf-8", errors="replace")

        if media_type == "text/plain":
            title, text, published_hint = None, decoded.strip(), None
        else:
            doc = extract_text(decoded)
            title, text, published_hint = doc.title, doc.text, doc.published_hint

        return FetchOutcome(
            requested_url=requested_url,
            final_url=current,
            redirect_chain=list(redirect_chain),
            accessible=True,
            status_code=status,
            content_type=media_type,
            title=title,
            text=text[: self._config.extract_max_chars],
            published_hint=published_hint,
            content_hash=hashlib.sha256(bytes(body)).hexdigest(),
            bytes_read=len(body),
        )

    @staticmethod
    def _failure(
        requested_url: str,
        current: str,
        redirect_chain: list[str],
        reason: str,
        *,
        status_code: Optional[int] = None,
        content_type: Optional[str] = None,
        bytes_read: int = 0,
    ) -> FetchOutcome:
        return FetchOutcome(
            requested_url=requested_url,
            final_url=current,
            redirect_chain=list(redirect_chain),
            accessible=False,
            inaccessible_reason=reason,
            status_code=status_code,
            content_type=content_type,
            bytes_read=bytes_read,
        )
