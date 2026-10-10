"""Outbound Server-Side Request Forgery (SSRF) Protection (§60).

Enforces strict scheme allowlists, host validation, private/link-local/cloud metadata
IP address blocking (RFC 1918, RFC 3927, RFC 6598, IPv6 ULA, IPv4-mapped IPv6), and
DNS resolution pre-flight validation to prevent DNS rebinding TOCTOU attacks.
"""

from __future__ import annotations

import fnmatch
import ipaddress
import socket
import urllib.parse
from dataclasses import dataclass
from typing import Final

from agentpay.domain.errors import DomainError


class SSRFError(DomainError):
    """Base exception for all outbound SSRF safety violations (§60)."""

    code = "SSRF_VIOLATION"


class DisallowedSchemeError(SSRFError):
    """Raised when an outbound URL scheme is not in the allowlist (§60)."""

    code = "DISALLOWED_URL_SCHEME"


class PrivateIPAddressError(SSRFError):
    """Raised when an outbound URL resolves or points to a private, loopback, or metadata IP."""

    code = "PRIVATE_IP_BLOCKED"


class DisallowedHostError(SSRFError):
    """Raised when an outbound URL host is blocked or not in the host allowlist."""

    code = "DISALLOWED_HOST"


class DisallowedPortError(SSRFError):
    """Raised when an outbound URL port is not permitted."""

    code = "DISALLOWED_PORT"


class InvalidUrlTargetError(SSRFError):
    """Raised when an outbound URL is malformed or contains forbidden components (e.g. userinfo)."""

    code = "INVALID_URL_TARGET"


class DNSResolutionError(SSRFError):
    """Raised when DNS resolution fails for an outbound target host."""

    code = "DNS_RESOLUTION_FAILED"


@dataclass(frozen=True, slots=True)
class SSRFConfig:
    """Configuration for outbound target URL validation (§60)."""

    allowed_schemes: frozenset[str] = frozenset({"https"})
    allowed_hosts: frozenset[str] | None = None
    blocked_hosts: frozenset[str] = frozenset(
        {
            "localhost",
            "metadata.google.internal",
            "metadata",
            "instance-data",
            "169.254.169.254",
        }
    )
    allowed_ports: frozenset[int] = frozenset({443})
    allow_private_ips: bool = False
    resolve_dns: bool = True
    dns_timeout_seconds: float = 2.0


@dataclass(frozen=True, slots=True)
class ValidatedTarget:
    """A cryptographically and structurally validated outbound network target."""

    original_url: str
    scheme: str
    host: str
    port: int
    path: str
    query: str
    resolved_ips: tuple[str, ...]


class OutboundUrlValidator:
    """Deterministic, zero-dependency SSRF target validator (§60)."""

    BLOCKED_IPV4_NETWORKS: Final[tuple[ipaddress.IPv4Network, ...]] = (
        ipaddress.IPv4Network("0.0.0.0/8"),  # Current network (RFC 1122)
        ipaddress.IPv4Network("10.0.0.0/8"),  # RFC 1918 Class A private
        ipaddress.IPv4Network("100.64.0.0/10"),  # RFC 6598 Carrier-Grade NAT
        ipaddress.IPv4Network("127.0.0.0/8"),  # IPv4 loopback
        ipaddress.IPv4Network("169.254.0.0/16"),  # RFC 3927 Link-local & Cloud IMDS
        ipaddress.IPv4Network("172.16.0.0/12"),  # RFC 1918 Class B private
        ipaddress.IPv4Network("192.0.0.0/24"),  # IETF Protocol Assignments
        ipaddress.IPv4Network("192.0.2.0/24"),  # TEST-NET-1 (RFC 5737)
        ipaddress.IPv4Network("192.88.99.0/24"),  # 6to4 Relay Anycast (RFC 3068)
        ipaddress.IPv4Network("192.168.0.0/16"),  # RFC 1918 Class C private
        ipaddress.IPv4Network("198.18.0.0/15"),  # Network benchmark tests (RFC 2544)
        ipaddress.IPv4Network("198.51.100.0/24"),  # TEST-NET-2 (RFC 5737)
        ipaddress.IPv4Network("203.0.113.0/24"),  # TEST-NET-3 (RFC 5737)
        ipaddress.IPv4Network("224.0.0.0/4"),  # Multicast (RFC 5771)
        ipaddress.IPv4Network("240.0.0.0/4"),  # Reserved / Future use (RFC 1112)
        ipaddress.IPv4Network("255.255.255.255/32"),  # Broadcast
    )

    BLOCKED_IPV6_NETWORKS: Final[tuple[ipaddress.IPv6Network, ...]] = (
        ipaddress.IPv6Network("::/128"),  # Unspecified
        ipaddress.IPv6Network("::1/128"),  # Loopback
        ipaddress.IPv6Network("100::/64"),  # Discard prefix (RFC 6666)
        ipaddress.IPv6Network("2001:db8::/32"),  # Documentation (RFC 3849)
        ipaddress.IPv6Network("fc00::/7"),  # Unique Local Address (ULA, RFC 4193)
        ipaddress.IPv6Network("fe80::/10"),  # Link-local (RFC 4291)
        ipaddress.IPv6Network("ff00::/8"),  # Multicast (RFC 4291)
    )

    def __init__(self, config: SSRFConfig | None = None) -> None:
        self._config = config or SSRFConfig()

    @property
    def config(self) -> SSRFConfig:
        return self._config

    def is_ip_blocked(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        """Determines if an IP address belongs to any private, loopback, or reserved network."""
        if self._config.allow_private_ips:
            return False

        # 1. Unpack IPv4-mapped IPv6 address (e.g. ::ffff:127.0.0.1 or ::ffff:169.254.169.254)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            return self.is_ip_blocked(ip.ipv4_mapped)

        # 2. Native address flags
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or ip.is_reserved
        ):
            return True

        # 3. Explicit subnet checks
        if isinstance(ip, ipaddress.IPv4Address):
            return any(ip in network for network in self.BLOCKED_IPV4_NETWORKS)
        return any(ip in network for network in self.BLOCKED_IPV6_NETWORKS)

    def validate_url(self, url: str, resolve_dns: bool | None = None) -> ValidatedTarget:
        """Validates an outbound target URL against SSRF policy rules (§60).

        Raises:
            InvalidUrlTargetError: If the URL is malformed, has userinfo, or has no host.
            DisallowedSchemeError: If the scheme is not in the allowlist.
            DisallowedPortError: If the port is not in the allowlist.
            DisallowedHostError: If the host is blocked or not in the allowlist.
            PrivateIPAddressError: If the target host or any resolved IP is blocked.
            DNSResolutionError: If DNS resolution fails when enabled.
        """
        if not url.strip():
            raise InvalidUrlTargetError("Target URL must be a non-empty string")

        url = url.strip()

        try:
            split = urllib.parse.urlsplit(url)
        except Exception as exc:
            raise InvalidUrlTargetError(f"Malformed URL: {exc}") from exc

        # 1. Check scheme
        scheme = split.scheme.lower()
        if not scheme:
            raise InvalidUrlTargetError("URL missing scheme")
        if scheme not in self._config.allowed_schemes:
            raise DisallowedSchemeError(
                f"URL scheme '{scheme}' is not permitted. Allowed: {sorted(self._config.allowed_schemes)}"
            )

        # 2. Reject credentials / userinfo in URL
        if split.username or split.password:
            raise InvalidUrlTargetError("URL userinfo credentials are not permitted")

        # 3. Extract and canonicalize host
        raw_host = split.hostname
        if not raw_host:
            raise InvalidUrlTargetError("URL missing valid host")

        # Strip trailing dot and lowercase
        canonical_host = raw_host.rstrip(".").lower()
        if not canonical_host:
            raise InvalidUrlTargetError("URL host is empty after canonicalization")

        # 4. Check if host is direct IP literal
        resolved_ips: list[str] = []
        is_direct_ip = False

        ip_candidate = canonical_host.strip("[]")
        try:
            parsed_ip = ipaddress.ip_address(ip_candidate)
            is_direct_ip = True
            if self.is_ip_blocked(parsed_ip):
                raise PrivateIPAddressError(
                    f"Direct IP target '{parsed_ip}' belongs to a blocked or private network range"
                )
            resolved_ips.append(str(parsed_ip))
        except ValueError:
            # Not an IP literal, it's a domain name
            pass

        # 5. Check against blocked hosts
        if canonical_host in self._config.blocked_hosts:
            raise DisallowedHostError(f"Host '{canonical_host}' is explicitly blocked")

        # 6. Check against allowed hosts if configured
        if self._config.allowed_hosts is not None:
            allowed = False
            for pattern in self._config.allowed_hosts:
                pattern_lower = pattern.lower()
                if (
                    fnmatch.fnmatch(canonical_host, pattern_lower)
                    or canonical_host == pattern_lower
                ):
                    allowed = True
                    break
            if not allowed:
                raise DisallowedHostError(
                    f"Host '{canonical_host}' does not match allowed host policy"
                )

        # 7. Check port
        port = split.port
        if port is None:
            port = 443 if scheme == "https" else (80 if scheme == "http" else None)

        if port is None or port not in self._config.allowed_ports:
            raise DisallowedPortError(
                f"Port {port} is not permitted. Allowed: {sorted(self._config.allowed_ports)}"
            )

        # 8. DNS Resolution Check
        should_resolve = self._config.resolve_dns if resolve_dns is None else resolve_dns
        if not is_direct_ip and should_resolve:
            resolved_ips.extend(self._resolve_and_validate_hostname(canonical_host, port))

        return ValidatedTarget(
            original_url=url,
            scheme=scheme,
            host=canonical_host,
            port=port,
            path=split.path or "/",
            query=split.query,
            resolved_ips=tuple(resolved_ips),
        )

    def _resolve_and_validate_hostname(self, host: str, port: int) -> list[str]:
        """Resolves host via DNS and validates every returned IP address."""
        old_timeout = socket.getdefaulttimeout()
        try:
            socket.setdefaulttimeout(self._config.dns_timeout_seconds)
            addr_info = socket.getaddrinfo(
                host,
                port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except (socket.gaierror, socket.herror, OSError) as exc:
            raise DNSResolutionError(f"DNS resolution failed for '{host}': {exc}") from exc
        finally:
            socket.setdefaulttimeout(old_timeout)

        if not addr_info:
            raise DNSResolutionError(f"DNS returned no IP addresses for '{host}'")

        resolved_ips: list[str] = []
        for entry in addr_info:
            sockaddr = entry[4]
            ip_str = str(sockaddr[0])
            try:
                ip_obj = ipaddress.ip_address(ip_str)
            except ValueError as exc:
                raise DNSResolutionError(f"Invalid resolved IP '{ip_str}': {exc}") from exc

            if self.is_ip_blocked(ip_obj):
                raise PrivateIPAddressError(
                    f"Host '{host}' resolved to blocked private or metadata IP '{ip_str}'"
                )
            if ip_str not in resolved_ips:
                resolved_ips.append(ip_str)

        return resolved_ips


def validate_outbound_url(url: str, config: SSRFConfig | None = None) -> ValidatedTarget:
    """Convenience function to validate an outbound URL using OutboundUrlValidator (§60)."""
    validator = OutboundUrlValidator(config)
    return validator.validate_url(url)
