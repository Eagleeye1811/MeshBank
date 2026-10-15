"""Client IP resolution that cannot be spoofed by request headers.

``X-Forwarded-For`` is only consulted when the direct peer is a configured,
trusted proxy. The list is then walked from the right (the entry appended by
our own proxy) and the first address that is not itself a trusted proxy is
the client. With no trusted proxies configured, the TCP peer is always used.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_cidrs(value: str) -> list[IPNetwork]:
    networks = []
    for item in value.split(","):
        if item.strip():
            networks.append(ipaddress.ip_network(item.strip(), strict=False))
    return networks


def _is_trusted(address: str, trusted: Sequence[IPNetwork]) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in trusted)


def resolve_client_ip(
    peer: str | None, forwarded_for: str | None, trusted: Sequence[IPNetwork]
) -> str:
    peer = peer or "unknown"
    if not trusted or not forwarded_for or not _is_trusted(peer, trusted):
        return peer
    hops = [hop.strip() for hop in forwarded_for.split(",") if hop.strip()]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            break  # malformed entry: stop trusting the chain
        if not _is_trusted(hop, trusted):
            return hop
    return peer
