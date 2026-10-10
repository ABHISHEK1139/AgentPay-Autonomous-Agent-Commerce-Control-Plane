"""Empirical stress-testing harness for SSRF Protection and CircuitBreaker Resilience (§60, §102, §103, §177).

Exhaustively probes:
1. SSRF Bypass Probing:
   - Hexadecimal IPv4 encodings (0x7f000001, 0x7f.0.0.1, 0xa000001, 0xa9fea9fe)
   - Octal IPv4 encodings (0177.0.0.1, 017700000001, 012.0.0.1, 0251.0376.0251.0376)
   - Decimal / Dword IPv4 encodings (2130706433, 2852039166, 167772161, 3232235521, 0)
   - Abbreviated IPv4 encodings (127.1, 10.1, 172.16.1)
   - Direct and embedded IPv6 encodings (loopback, ULA, link-local, multicast, IPv4-mapped, NAT64, 6to4)
   - Malformed schemes (http, ftp, file, gopher, data, javascript)
   - Port validation and URL userinfo authentication bypass tricks
   - DNS rebinding TOCTOU: single private IP, mixed public/private IPs, broadcast, CGNAT
2. CircuitBreaker Concurrency Stress:
   - 50 threads racing failures (CLOSED -> OPEN transition integrity)
   - 50 threads hitting OPEN circuit breaker (fast fail-safe validation)
   - Concurrent HALF_OPEN probing under recovery timeout expiration
   - Full recovery to CLOSED under concurrent probe successes
   - High-throughput parallel load (100 threads executing 2,000 operations without deadlocks or counter drift)
   - Alternating concurrent success/failure churn
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Ensure project paths are available
sys.path.insert(0, str(Path("apps/api/src").resolve()))

from agentpay.infrastructure.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)
from agentpay.security.ssrf import (
    DisallowedHostError,
    DisallowedPortError,
    DisallowedSchemeError,
    DNSResolutionError,
    InvalidUrlTargetError,
    OutboundUrlValidator,
    PrivateIPAddressError,
    SSRFConfig,
    SSRFError,
)


def log(msg: str) -> None:
    """Output message to stdout without triggering lint warnings."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


# ===========================================================================
# Section 1: SSRF Adversarial Probing
# ===========================================================================


def probe_ssrf_adversarial_ip_encodings() -> dict[str, Any]:
    """Test adversarial IP formats (hex, octal, decimal, abbreviated) under default configuration.

    In production/default mode (resolve_dns=True), OutboundUrlValidator must either:
    1. Parse the host directly as an IP and block it via PrivateIPAddressError, OR
    2. Attempt DNS resolution via socket.getaddrinfo which either resolves to blocked IP (PrivateIPAddressError)
       or fails with DNSResolutionError.
    Either way, the URL MUST NOT be allowed.
    """
    validator = OutboundUrlValidator(SSRFConfig(resolve_dns=True))

    adversarial_targets = [
        # Dword decimal integers
        ("https://2130706433/", "127.0.0.1 in dword decimal"),
        ("https://2852039166/", "169.254.169.254 in dword decimal"),
        ("https://167772161/", "10.0.0.1 in dword decimal"),
        ("https://3232235521/", "192.168.0.1 in dword decimal"),
        ("https://0/", "0.0.0.0 in decimal single-digit"),
        # Hexadecimal formats
        ("https://0x7f000001/", "127.0.0.1 in single hex"),
        ("https://0x7f.0.0.1/", "127.0.0.1 in dotted hex"),
        ("https://0x7f.0x0.0x0.0x1/", "127.0.0.1 in full dotted hex"),
        ("https://0xa000001/", "10.0.0.1 in hex"),
        ("https://0xa9fea9fe/", "169.254.169.254 in hex"),
        ("https://0x7f.1/", "127.1 in hex"),
        # Octal formats
        ("https://0177.0.0.1/", "127.0.0.1 in dotted octal"),
        ("https://017700000001/", "127.0.0.1 in single octal"),
        ("https://012.0.0.1/", "10.0.0.1 in dotted octal"),
        ("https://0251.0376.0251.0376/", "169.254.169.254 in octal"),
        # Abbreviated dotted IPv4
        ("https://127.1/", "127.0.0.1 abbreviated"),
        ("https://10.1/", "10.0.0.1 abbreviated"),
        ("https://172.16.1/", "172.16.0.1 abbreviated"),
        ("https://192.168.1/", "192.168.0.1 abbreviated"),
    ]

    results: list[dict[str, Any]] = []
    leaked_targets: list[str] = []

    for url, description in adversarial_targets:
        try:
            target = validator.validate_url(url)
            leaked_targets.append(url)
            results.append(
                {"url": url, "desc": description, "status": "LEAKED", "allowed_host": target.host}
            )
        except SSRFError as exc:
            results.append(
                {"url": url, "desc": description, "status": "BLOCKED", "error": type(exc).__name__}
            )
        except Exception as exc:
            results.append({"url": url, "desc": description, "status": "ERROR", "error": str(exc)})

    return {
        "total_probed": len(adversarial_targets),
        "blocked_count": len(adversarial_targets) - len(leaked_targets),
        "leaked_targets": leaked_targets,
        "passed": len(leaked_targets) == 0,
        "details": results,
    }


def probe_ssrf_ipv6_embeddings_and_special() -> dict[str, Any]:
    """Test standard and esoteric IPv6 encodings (ULA, link-local, NAT64, 6to4, IPv4-mapped)."""
    validator = OutboundUrlValidator(SSRFConfig(resolve_dns=False))

    ipv6_targets = [
        ("https://[::1]/", "Loopback IPv6"),
        ("https://[::]/", "Unspecified IPv6"),
        ("https://[0:0:0:0:0:0:0:1]/", "Expanded loopback IPv6"),
        ("https://[0000:0000:0000:0000:0000:0000:0000:0001]/", "Padded loopback IPv6"),
        ("https://[::ffff:127.0.0.1]/", "IPv4-mapped 127.0.0.1 dotted"),
        ("https://[::ffff:7f00:1]/", "IPv4-mapped 127.0.0.1 hex"),
        ("https://[::ffff:169.254.169.254]/", "IPv4-mapped IMDS dotted"),
        ("https://[::ffff:a9fe:a9fe]/", "IPv4-mapped IMDS hex"),
        ("https://[::ffff:10.0.0.1]/", "IPv4-mapped RFC 1918 Class A"),
        ("https://[::ffff:172.16.0.1]/", "IPv4-mapped RFC 1918 Class B"),
        ("https://[::ffff:192.168.1.1]/", "IPv4-mapped RFC 1918 Class C"),
        ("https://[::127.0.0.1]/", "IPv4-compatible IPv6 dotted"),
        ("https://[::7f00:1]/", "IPv4-compatible IPv6 hex"),
        ("https://[64:ff9b::127.0.0.1]/", "NAT64 well-known prefix dotted"),
        ("https://[64:ff9b::7f00:1]/", "NAT64 well-known prefix hex"),
        ("https://[64:ff9b::169.254.169.254]/", "NAT64 IMDS dotted"),
        ("https://[64:ff9b::a9fe:a9fe]/", "NAT64 IMDS hex"),
        ("https://[2002:7f00:1::]/", "6to4 embedded loopback"),
        ("https://[2002:a9fe:a9fe::]/", "6to4 embedded IMDS"),
        ("https://[fe80::1]/", "Link-local IPv6"),
        ("https://[fc00::1]/", "Unique Local Address fc00::/8"),
        ("https://[fd00::1]/", "Unique Local Address fd00::/8"),
        ("https://[fd12:3456:789a::1]/", "Unique Local Address random ULA"),
        ("https://[ff02::1]/", "Multicast IPv6"),
        ("https://[2001:db8::1]/", "Documentation IPv6 prefix"),
    ]

    results: list[dict[str, Any]] = []
    leaked_targets: list[str] = []

    for url, description in ipv6_targets:
        try:
            target = validator.validate_url(url)
            leaked_targets.append(url)
            results.append(
                {"url": url, "desc": description, "status": "LEAKED", "allowed_host": target.host}
            )
        except SSRFError as exc:
            results.append(
                {"url": url, "desc": description, "status": "BLOCKED", "error": type(exc).__name__}
            )

    return {
        "total_probed": len(ipv6_targets),
        "blocked_count": len(ipv6_targets) - len(leaked_targets),
        "leaked_targets": leaked_targets,
        "passed": len(leaked_targets) == 0,
        "details": results,
    }


def probe_ssrf_schemes_ports_userinfo() -> dict[str, Any]:
    """Test forbidden schemes, unauthorized ports, and userinfo bypass tricks."""
    validator = OutboundUrlValidator(SSRFConfig(resolve_dns=False))

    malformed_targets = [
        # Disallowed schemes
        ("http://example.com/", DisallowedSchemeError),
        ("ftp://example.com/file", DisallowedSchemeError),
        ("file:///etc/passwd", DisallowedSchemeError),
        ("file://localhost/etc/passwd", DisallowedSchemeError),
        ("gopher://127.0.0.1:70/", DisallowedSchemeError),
        ("data:text/html,<script>alert(1)</script>", DisallowedSchemeError),
        ("javascript:alert(1)", DisallowedSchemeError),
        ("dict://127.0.0.1:2628/", DisallowedSchemeError),
        ("ldap://127.0.0.1:389/", DisallowedSchemeError),
        # Disallowed ports
        ("https://example.com:80/", DisallowedPortError),
        ("https://example.com:8080/", DisallowedPortError),
        ("https://example.com:8443/", DisallowedPortError),
        ("https://example.com:22/", DisallowedPortError),
        ("https://example.com:6379/", DisallowedPortError),
        ("https://example.com:5432/", DisallowedPortError),
        # Userinfo credentials
        ("https://user:pass@example.com/", InvalidUrlTargetError),
        ("https://admin@example.com/", InvalidUrlTargetError),
        # Explicitly blocked hosts
        ("https://localhost/", DisallowedHostError),
        ("https://metadata.google.internal/", DisallowedHostError),
        ("https://instance-data/", DisallowedHostError),
        # Malformed URLs
        ("", InvalidUrlTargetError),
        ("   ", InvalidUrlTargetError),
        ("https://", InvalidUrlTargetError),
        ("https://:443/", InvalidUrlTargetError),
        ("not_a_url", InvalidUrlTargetError),
    ]

    failures: list[dict[str, Any]] = []

    for url, expected_error in malformed_targets:
        try:
            target = validator.validate_url(url)
            failures.append(
                {
                    "url": url,
                    "expected": expected_error.__name__,
                    "actual": f"ALLOWED ({target.host})",
                }
            )
        except SSRFError as exc:
            if not isinstance(exc, expected_error):
                failures.append(
                    {
                        "url": url,
                        "expected": expected_error.__name__,
                        "actual": f"{type(exc).__name__}: {exc}",
                    }
                )
        except Exception as exc:
            failures.append(
                {
                    "url": url,
                    "expected": expected_error.__name__,
                    "actual": f"UNEXPECTED {type(exc).__name__}: {exc}",
                }
            )

    return {
        "total_probed": len(malformed_targets),
        "failures_count": len(failures),
        "passed": len(failures) == 0,
        "failures": failures,
    }


def probe_ssrf_dns_rebinding_and_resolution_edge_cases() -> dict[str, Any]:
    """Test DNS rebinding TOCTOU defense with mocked DNS scenarios."""
    validator = OutboundUrlValidator(SSRFConfig(resolve_dns=True))

    scenarios = [
        # 1. DNS returns single RFC 1918 private IP
        (
            "https://rebinding-1.com/pay",
            [(2, 1, 6, "", ("10.20.30.40", 443))],
            PrivateIPAddressError,
        ),
        # 2. DNS returns single loopback IP
        (
            "https://rebinding-2.com/pay",
            [(2, 1, 6, "", ("127.0.0.1", 443))],
            PrivateIPAddressError,
        ),
        # 3. DNS returns single IMDS metadata IP
        (
            "https://rebinding-3.com/pay",
            [(2, 1, 6, "", ("169.254.169.254", 443))],
            PrivateIPAddressError,
        ),
        # 4. DNS returns multiple IPs: public IP first, followed by private IP (mixed attack)
        (
            "https://mixed-ip-attack.com/pay",
            [
                (2, 1, 6, "", ("93.184.216.34", 443)),  # Public example.com
                (2, 1, 6, "", ("192.168.1.1", 443)),  # Private
            ],
            PrivateIPAddressError,
        ),
        # 5. DNS returns multiple IPs: public IP first, followed by IMDS metadata IP
        (
            "https://mixed-imds-attack.com/pay",
            [
                (2, 1, 6, "", ("1.1.1.1", 443)),  # Public
                (2, 1, 6, "", ("169.254.169.254", 443)),  # IMDS
            ],
            PrivateIPAddressError,
        ),
        # 6. DNS returns Carrier-Grade NAT (RFC 6598)
        (
            "https://cgnat-target.com/pay",
            [(2, 1, 6, "", ("100.64.0.1", 443))],
            PrivateIPAddressError,
        ),
        # 7. DNS returns broadcast IP
        (
            "https://broadcast-target.com/pay",
            [(2, 1, 6, "", ("255.255.255.255", 443))],
            PrivateIPAddressError,
        ),
        # 8. DNS returns current network / 0.0.0.0
        (
            "https://zero-target.com/pay",
            [(2, 1, 6, "", ("0.0.0.0", 443))],
            PrivateIPAddressError,
        ),
    ]

    failures: list[dict[str, Any]] = []

    for url, addr_info, expected_exc in scenarios:
        with patch("socket.getaddrinfo", return_value=addr_info):
            try:
                target = validator.validate_url(url)
                failures.append(
                    {
                        "url": url,
                        "expected": expected_exc.__name__,
                        "actual": f"ALLOWED with IPs {target.resolved_ips}",
                    }
                )
            except SSRFError as exc:
                if not isinstance(exc, expected_exc):
                    failures.append(
                        {
                            "url": url,
                            "expected": expected_exc.__name__,
                            "actual": f"{type(exc).__name__}: {exc}",
                        }
                    )
            except Exception as exc:
                failures.append(
                    {
                        "url": url,
                        "expected": expected_exc.__name__,
                        "actual": f"UNEXPECTED {type(exc).__name__}: {exc}",
                    }
                )

    # Test empty DNS response
    with patch("socket.getaddrinfo", return_value=[]):
        try:
            validator.validate_url("https://empty-dns.com/pay")
            failures.append(
                {
                    "url": "https://empty-dns.com/pay",
                    "expected": "DNSResolutionError",
                    "actual": "ALLOWED",
                }
            )
        except DNSResolutionError:
            pass
        except Exception as exc:
            failures.append(
                {
                    "url": "https://empty-dns.com/pay",
                    "expected": "DNSResolutionError",
                    "actual": str(exc),
                }
            )

    return {
        "total_probed": len(scenarios) + 1,
        "failures_count": len(failures),
        "passed": len(failures) == 0,
        "failures": failures,
    }


# ===========================================================================
# Section 2: CircuitBreaker Concurrency Stress & Race Conditions
# ===========================================================================


def probe_circuit_breaker_50_threads_racing_failures() -> dict[str, Any]:
    """Test 50 threads failing simultaneously.

    Verifies:
    1. Circuit transitions cleanly to OPEN when failure threshold is reached.
    2. No state corruption or race conditions in internal counters.
    3. Breaker immediately fast-fails remaining calls with CircuitBreakerOpenError.
    """
    breaker = CircuitBreaker(
        name="test_concurrent_failure",
        failure_threshold=5,
        recovery_timeout_seconds=5.0,
    )

    downstream_calls = 0
    lock = threading.Lock()

    def failing_service() -> None:
        nonlocal downstream_calls
        with lock:
            downstream_calls += 1
        raise RuntimeError("Service failure")

    errors_encountered: list[Exception] = []
    errors_lock = threading.Lock()

    def worker() -> None:
        try:
            breaker.call(failing_service)
        except Exception as exc:
            with errors_lock:
                errors_encountered.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    open_errors = [e for e in errors_encountered if isinstance(e, CircuitBreakerOpenError)]
    runtime_errors = [e for e in errors_encountered if isinstance(e, RuntimeError)]

    passed = (
        breaker.state == CircuitState.OPEN
        and len(errors_encountered) == 50
        and downstream_calls >= 5  # at least threshold executed
        and len(open_errors) > 0  # fast fail triggered for remaining
        and len(runtime_errors) == downstream_calls
    )

    return {
        "total_threads": 50,
        "downstream_invocations": downstream_calls,
        "runtime_errors": len(runtime_errors),
        "fast_fail_open_errors": len(open_errors),
        "final_state": breaker.state.value,
        "passed": passed,
    }


def probe_circuit_breaker_50_threads_hitting_open_circuit() -> dict[str, Any]:
    """Test 50 threads hitting an already OPEN circuit breaker.

    Verifies:
    1. Zero downstream invocations occur.
    2. Exactly 50/50 threads receive CircuitBreakerOpenError.
    3. retry_after_seconds is consistently > 0.
    """
    breaker = CircuitBreaker(
        name="test_fast_fail",
        failure_threshold=3,
        recovery_timeout_seconds=10.0,
    )
    breaker.trip("pre_tripped")

    downstream_calls = 0

    def service() -> str:
        nonlocal downstream_calls
        downstream_calls += 1
        return "ok"

    results: list[Exception | str] = []
    res_lock = threading.Lock()

    def worker() -> None:
        try:
            val = breaker.call(service)
            with res_lock:
                results.append(val)
        except Exception as exc:
            with res_lock:
                results.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    open_errors = [e for e in results if isinstance(e, CircuitBreakerOpenError)]

    passed = (
        downstream_calls == 0
        and len(open_errors) == 50
        and all(e.retry_after_seconds > 0.0 for e in open_errors)
    )

    return {
        "total_threads": 50,
        "downstream_calls": downstream_calls,
        "open_errors_count": len(open_errors),
        "passed": passed,
    }


def probe_circuit_breaker_half_open_concurrent_probing() -> dict[str, Any]:
    """Test concurrent thread behavior during HALF_OPEN recovery transition."""
    current_time = 100.0

    def mock_clock() -> float:
        return current_time

    breaker = CircuitBreaker(
        name="test_half_open_recovery",
        failure_threshold=3,
        recovery_timeout_seconds=10.0,
        half_open_success_threshold=2,
        clock=mock_clock,
    )

    # Trip breaker
    breaker.trip("tripped")
    assert breaker.state == CircuitState.OPEN

    # Advance time past recovery timeout
    current_time = 115.0

    # Execute 2 trial successes concurrently
    downstream_calls = 0
    call_lock = threading.Lock()

    def probe_service() -> str:
        nonlocal downstream_calls
        with call_lock:
            downstream_calls += 1
        return "success"

    errors: list[Exception] = []
    err_lock = threading.Lock()

    def worker() -> None:
        try:
            breaker.call(probe_service)
        except Exception as exc:
            with err_lock:
                errors.append(exc)

    # Launch 5 concurrent threads
    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Breaker should now have transitioned from HALF_OPEN to CLOSED
    passed = breaker.state == CircuitState.CLOSED and breaker.failure_count == 0

    return {
        "final_state": breaker.state.value,
        "downstream_calls": downstream_calls,
        "errors": [type(e).__name__ for e in errors],
        "passed": passed,
    }


def probe_circuit_breaker_high_throughput_parallel_load() -> dict[str, Any]:
    """Test 100 threads executing 2,000 operations under CLOSED state without deadlock or corruption."""
    breaker = CircuitBreaker(
        name="test_high_throughput",
        failure_threshold=10,
        recovery_timeout_seconds=5.0,
    )

    total_ops = 2000
    completed = 0
    comp_lock = threading.Lock()

    def dummy_op(val: int) -> int:
        return val * 2

    def worker(items: list[int]) -> None:
        nonlocal completed
        for item in items:
            res = breaker.call(dummy_op, item)
            assert res == item * 2
            with comp_lock:
                completed += 1

    chunk_size = total_ops // 100
    chunks = [list(range(i * chunk_size, (i + 1) * chunk_size)) for i in range(100)]

    threads = [threading.Thread(target=worker, args=(c,)) for c in chunks]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0

    passed = (
        completed == total_ops
        and breaker.state == CircuitState.CLOSED
        and breaker.failure_count == 0
    )

    return {
        "total_operations": total_ops,
        "completed": completed,
        "elapsed_seconds": round(elapsed, 4),
        "throughput_ops_per_sec": round(total_ops / max(0.0001, elapsed), 1),
        "passed": passed,
    }


def probe_circuit_breaker_alternating_success_failure_churn() -> dict[str, Any]:
    """Test 40 threads alternating failures and successes to verify state resilience."""
    breaker = CircuitBreaker(
        name="test_churn",
        failure_threshold=50,  # High enough not to trip during alternating churn
        recovery_timeout_seconds=5.0,
    )

    def success_op() -> str:
        return "ok"

    def fail_op() -> None:
        raise ValueError("transient failure")

    def worker(worker_id: int) -> None:
        for i in range(20):
            if (worker_id + i) % 2 == 0:
                breaker.call(success_op)
            else:
                with contextlib.suppress(ValueError):
                    breaker.call(fail_op)

    threads = [threading.Thread(target=worker, args=(wid,)) for wid in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # The breaker must remain valid, lock not held, state readable
    state = breaker.state
    passed = state in (CircuitState.CLOSED, CircuitState.OPEN)

    return {
        "final_state": state.value,
        "failure_count": breaker.failure_count,
        "passed": passed,
    }


# ===========================================================================
# Main Runner
# ===========================================================================


def main() -> int:
    log("==================================================================")
    log("  AgentPay Empirical Stress-Testing: SSRF & CircuitBreaker (§177)")
    log("==================================================================")

    # 1. SSRF Adversarial IP Encodings
    p1 = probe_ssrf_adversarial_ip_encodings()
    log(f"[*] Probe 1 (SSRF Adversarial IP Encodings): {'PASS' if p1['passed'] else 'FAIL'}")
    log(
        f"    Total: {p1['total_probed']}, Blocked: {p1['blocked_count']}, Leaked: {len(p1['leaked_targets'])}"
    )
    if p1["leaked_targets"]:
        log(f"    [!] Leaked targets: {p1['leaked_targets']}")

    # 2. SSRF IPv6 Embeddings and Special Encodings
    p2 = probe_ssrf_ipv6_embeddings_and_special()
    log(f"[*] Probe 2 (SSRF IPv6 Special & Embeddings): {'PASS' if p2['passed'] else 'FAIL'}")
    log(
        f"    Total: {p2['total_probed']}, Blocked: {p2['blocked_count']}, Leaked: {len(p2['leaked_targets'])}"
    )
    if p2["leaked_targets"]:
        log(f"    [!] Leaked targets: {p2['leaked_targets']}")

    # 3. SSRF Schemes, Ports, and Userinfo
    p3 = probe_ssrf_schemes_ports_userinfo()
    log(f"[*] Probe 3 (SSRF Schemes, Ports, Userinfo): {'PASS' if p3['passed'] else 'FAIL'}")
    log(f"    Total: {p3['total_probed']}, Failures: {p3['failures_count']}")
    if p3["failures"]:
        log(f"    [!] Failures: {p3['failures']}")

    # 4. SSRF DNS Rebinding & Multi-IP Resolution
    p4 = probe_ssrf_dns_rebinding_and_resolution_edge_cases()
    log(f"[*] Probe 4 (SSRF DNS Rebinding & Multi-IP): {'PASS' if p4['passed'] else 'FAIL'}")
    log(f"    Total: {p4['total_probed']}, Failures: {p4['failures_count']}")
    if p4["failures"]:
        log(f"    [!] Failures: {p4['failures']}")

    # 5. CircuitBreaker 50 Threads Racing Failures
    p5 = probe_circuit_breaker_50_threads_racing_failures()
    log(f"[*] Probe 5 (CircuitBreaker 50 Threads Racing): {'PASS' if p5['passed'] else 'FAIL'}")
    log(
        f"    Threads: {p5['total_threads']}, Downstream Calls: {p5['downstream_invocations']}, Fast-Fail Errors: {p5['fast_fail_open_errors']}"
    )

    # 6. CircuitBreaker Fast-Fail under Concurrency
    p6 = probe_circuit_breaker_50_threads_hitting_open_circuit()
    log(f"[*] Probe 6 (CircuitBreaker Fast-Fail 50 Threads): {'PASS' if p6['passed'] else 'FAIL'}")
    log(
        f"    Threads: {p6['total_threads']}, Downstream Calls: {p6['downstream_calls']}, Open Errors: {p6['open_errors_count']}"
    )

    # 7. CircuitBreaker HALF_OPEN Concurrency Recovery
    p7 = probe_circuit_breaker_half_open_concurrent_probing()
    log(f"[*] Probe 7 (CircuitBreaker HALF_OPEN Recovery): {'PASS' if p7['passed'] else 'FAIL'}")
    log(
        f"    Final State: {p7['final_state']}, Downstream Calls: {p7['downstream_calls']}, Errors: {p7['errors']}"
    )

    # 8. CircuitBreaker High-Throughput Load (100 Threads / 2,000 Ops)
    p8 = probe_circuit_breaker_high_throughput_parallel_load()
    log(f"[*] Probe 8 (CircuitBreaker High-Throughput Load): {'PASS' if p8['passed'] else 'FAIL'}")
    log(
        f"    Completed: {p8['completed']}/{p8['total_operations']} in {p8['elapsed_seconds']}s ({p8['throughput_ops_per_sec']} ops/sec)"
    )

    # 9. CircuitBreaker Alternating Churn
    p9 = probe_circuit_breaker_alternating_success_failure_churn()
    log(f"[*] Probe 9 (CircuitBreaker Alternating Churn): {'PASS' if p9['passed'] else 'FAIL'}")
    log(f"    Final State: {p9['final_state']}, Failure Count: {p9['failure_count']}")

    log("==================================================================")
    all_probes = [p1, p2, p3, p4, p5, p6, p7, p8, p9]
    failed_probes = [i + 1 for i, p in enumerate(all_probes) if not p["passed"]]

    if failed_probes:
        log(f"[!] RESULT: {len(failed_probes)} PROBE(S) FAILED: {failed_probes} -> FAILED")
        return 1

    log("[+] RESULT: ALL 9 EMPIRICAL PROBES PASSED (9/9) -> CONFIRMED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
