from __future__ import annotations

import ipaddress
from unittest.mock import patch

import pytest

from agentpay.security.ssrf import (
    DisallowedHostError,
    DisallowedPortError,
    DisallowedSchemeError,
    DNSResolutionError,
    InvalidUrlTargetError,
    OutboundUrlValidator,
    PrivateIPAddressError,
    SSRFConfig,
    validate_outbound_url,
)


def test_validator_rejects_empty_or_malformed_url() -> None:
    validator = OutboundUrlValidator()
    with pytest.raises(InvalidUrlTargetError):
        validator.validate_url("")
    with pytest.raises(InvalidUrlTargetError):
        validator.validate_url("   ")
    with pytest.raises(InvalidUrlTargetError):
        validator.validate_url("not_a_url")


def test_validator_enforces_https_by_default() -> None:
    validator = OutboundUrlValidator()
    # http is rejected by default
    with pytest.raises(DisallowedSchemeError):
        validator.validate_url("http://example.com/api")
    # non-http schemes rejected
    with pytest.raises(DisallowedSchemeError):
        validator.validate_url("ftp://example.com/file")
    with pytest.raises(DisallowedSchemeError):
        validator.validate_url("file:///etc/passwd")
    with pytest.raises(DisallowedSchemeError):
        validator.validate_url("gopher://example.com/")
    with pytest.raises(DisallowedSchemeError):
        validator.validate_url("data:text/plain;base64,SGVsbG8=")


def test_validator_rejects_userinfo_in_url() -> None:
    validator = OutboundUrlValidator()
    with pytest.raises(InvalidUrlTargetError):
        validator.validate_url("https://user:pass@example.com/api")
    with pytest.raises(InvalidUrlTargetError):
        validator.validate_url("https://admin@example.com/api")


def test_validator_enforces_port_restrictions() -> None:
    validator = OutboundUrlValidator()
    # Default allows only 443
    with pytest.raises(DisallowedPortError):
        validator.validate_url("https://example.com:8080/api")
    with pytest.raises(DisallowedPortError):
        validator.validate_url("https://example.com:22/ssh")
    with pytest.raises(DisallowedPortError):
        validator.validate_url("https://example.com:6379/redis")
    with pytest.raises(DisallowedPortError):
        validator.validate_url("https://example.com:5432/postgres")


def test_validator_blocks_explicit_hostnames() -> None:
    validator = OutboundUrlValidator()
    with pytest.raises(DisallowedHostError):
        validator.validate_url("https://localhost/api")
    with pytest.raises(DisallowedHostError):
        validator.validate_url("https://metadata.google.internal/computeMetadata/v1/")
    with pytest.raises(DisallowedHostError):
        validator.validate_url("https://instance-data/latest/meta-data/")


def test_validator_blocks_direct_ipv4_loopback() -> None:
    validator = OutboundUrlValidator()
    loopback_ips = [
        "127.0.0.1",
        "127.0.0.2",
        "127.1.2.3",
        "127.255.255.254",
    ]
    for ip in loopback_ips:
        with pytest.raises(PrivateIPAddressError):
            validator.validate_url(f"https://{ip}/api")


def test_validator_blocks_direct_ipv4_private_rfc1918() -> None:
    validator = OutboundUrlValidator()
    private_ips = [
        "10.0.0.1",
        "10.254.12.3",
        "172.16.0.1",
        "172.20.10.5",
        "172.31.255.255",
        "192.168.0.1",
        "192.168.1.100",
    ]
    for ip in private_ips:
        with pytest.raises(PrivateIPAddressError):
            validator.validate_url(f"https://{ip}/api")


def test_validator_blocks_cloud_metadata_and_link_local() -> None:
    validator = OutboundUrlValidator()
    metadata_ips = [
        "169.254.169.254",  # AWS/GCP/Azure IMDS
        "169.254.0.1",
        "169.254.254.254",
    ]
    for ip in metadata_ips:
        with pytest.raises(PrivateIPAddressError):
            validator.validate_url(f"https://{ip}/api")


def test_validator_blocks_reserved_and_multicast_ipv4() -> None:
    validator = OutboundUrlValidator()
    special_ips = [
        "0.0.0.0",  # Unspecified / Current network
        "100.64.0.1",  # Carrier-grade NAT
        "192.0.2.1",  # TEST-NET-1
        "198.51.100.1",  # TEST-NET-2
        "203.0.113.1",  # TEST-NET-3
        "224.0.0.1",  # Multicast
        "240.0.0.1",  # Reserved / Future use
        "255.255.255.255",  # Broadcast
    ]
    for ip in special_ips:
        with pytest.raises(PrivateIPAddressError):
            validator.validate_url(f"https://{ip}/api")


def test_validator_blocks_direct_ipv6_private_and_special() -> None:
    validator = OutboundUrlValidator()
    special_ipv6 = [
        "::1",  # Loopback
        "::",  # Unspecified
        "fc00::1",  # Unique Local Address (ULA)
        "fd12:3456:789a::1",  # ULA
        "fe80::1",  # Link-local
        "ff02::1",  # Multicast
        "2001:db8::1",  # Documentation
    ]
    for ip in special_ipv6:
        with pytest.raises(PrivateIPAddressError):
            validator.validate_url(f"https://[{ip}]/api")


def test_validator_blocks_ipv4_mapped_ipv6_bypass() -> None:
    """Critical attack vector: embedding private IPv4 inside IPv6 address."""
    validator = OutboundUrlValidator()
    mapped_attacks = [
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "::ffff:10.0.0.1",
        "::ffff:192.168.1.1",
        "::ffff:172.16.0.1",
    ]
    for ip in mapped_attacks:
        with pytest.raises(PrivateIPAddressError):
            validator.validate_url(f"https://[{ip}]/api")


def test_validator_respects_host_allowlist() -> None:
    config = SSRFConfig(
        allowed_hosts=frozenset({"*.trusted-merchant.com", "api.payments.org"}),
        resolve_dns=False,
    )
    validator = OutboundUrlValidator(config)

    # Allowed by wildcard
    target1 = validator.validate_url("https://checkout.trusted-merchant.com/pay")
    assert target1.host == "checkout.trusted-merchant.com"

    # Allowed by exact match
    target2 = validator.validate_url("https://api.payments.org/tokens")
    assert target2.host == "api.payments.org"

    # Blocked by allowlist
    with pytest.raises(DisallowedHostError):
        validator.validate_url("https://untrusted-store.com/checkout")


def test_dns_resolution_detects_rebinding_to_private_ip() -> None:
    validator = OutboundUrlValidator(SSRFConfig(resolve_dns=True))

    # Mock DNS returning a private RFC 1918 IP
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [
            (
                2,
                1,
                6,
                "",
                ("10.0.0.42", 443),
            )
        ]
        with pytest.raises(PrivateIPAddressError):
            validator.validate_url("https://sneaky-domain.com/pay")

    # Mock DNS returning cloud metadata IP
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [
            (
                2,
                1,
                6,
                "",
                ("169.254.169.254", 443),
            )
        ]
        with pytest.raises(PrivateIPAddressError):
            validator.validate_url("https://imds-rebind.org/")


def test_dns_resolution_failure_raises_typed_error() -> None:
    validator = OutboundUrlValidator(SSRFConfig(resolve_dns=True))
    with (
        patch("socket.getaddrinfo", side_effect=OSError("Name or service not known")),
        pytest.raises(DNSResolutionError),
    ):
        validator.validate_url("https://nonexistent-domain-xyz123.com/pay")


def test_valid_public_domain_passes_validation() -> None:
    validator = OutboundUrlValidator(SSRFConfig(resolve_dns=True))
    with patch("socket.getaddrinfo") as mock_dns:
        mock_dns.return_value = [
            (
                2,
                1,
                6,
                "",
                ("93.184.216.34", 443),
            )
        ]
        target = validator.validate_url("https://example.com/products?id=123")
        assert target.scheme == "https"
        assert target.host == "example.com"
        assert target.port == 443
        assert target.path == "/products"
        assert target.query == "id=123"
        assert target.resolved_ips == ("93.184.216.34",)


def test_convenience_validate_outbound_url_function() -> None:
    target = validate_outbound_url(
        "https://api.merchant.com/v1/catalog",
        config=SSRFConfig(resolve_dns=False),
    )
    assert target.host == "api.merchant.com"
    assert target.scheme == "https"
    assert target.port == 443


def test_is_ip_blocked_direct_utility() -> None:
    validator = OutboundUrlValidator()
    assert validator.is_ip_blocked(ipaddress.ip_address("127.0.0.1")) is True
    assert validator.is_ip_blocked(ipaddress.ip_address("8.8.8.8")) is False
    assert validator.is_ip_blocked(ipaddress.ip_address("1.1.1.1")) is False
    assert validator.is_ip_blocked(ipaddress.ip_address("169.254.169.254")) is True
    assert validator.is_ip_blocked(ipaddress.ip_address("::1")) is True
    assert validator.is_ip_blocked(ipaddress.ip_address("2606:4700:4700::1111")) is False
