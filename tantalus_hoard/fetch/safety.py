"""SSRF guard: only http(s) URLs whose host resolves exclusively to public addresses may be fetched."""

from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Iterable
from urllib.parse import urlsplit

# Returned (as the error text) when the host does not resolve at all. The fetcher treats this as a plain
# network failure rather than as an unsafe URL.
UNRESOLVABLE_PREFIX = "unresolvable host"

Resolver = Callable[[str, int], Iterable[str]]


def default_resolver(host: str, port: int) -> list[str]:
    """Resolve ``host`` to a list of IP address strings (all families)."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def is_public_ip(value: str) -> bool:
    """True only for globally routable unicast addresses."""
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])  # strip an IPv6 zone id
    except ValueError:
        return False
    # An IPv4 address tunnelled inside IPv6 (::ffff:127.0.0.1, 6to4, teredo) is judged by the IPv4 part.
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        or ip.is_multicast or ip.is_unspecified or not ip.is_global
    )


def check_url(url: str, resolver: Resolver | None = None) -> str | None:
    """Return an error text when ``url`` must not be fetched, ``None`` when it is fine."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return "malformed URL"
    if parts.scheme not in ("http", "https"):
        return f"unsupported scheme {parts.scheme or '(none)'!r}: only http and https are allowed"
    host = parts.hostname
    if not host:
        return "URL has no host"
    if parts.username or parts.password:
        return "URLs with embedded credentials are not allowed"
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return "invalid port"
    # A literal IP needs no resolution.
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal:
        return None if is_public_ip(host) else f"address {host} is not a public address"
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        return f"host {host} is a local name"
    try:
        addresses = list((resolver or default_resolver)(host, port))
    except (OSError, UnicodeError) as error:
        return f"{UNRESOLVABLE_PREFIX} {host}: {error}"
    if not addresses:
        return f"{UNRESOLVABLE_PREFIX} {host}"
    for address in addresses:
        if not is_public_ip(address):
            return f"host {host} resolves to a non-public address ({address})"
    return None
