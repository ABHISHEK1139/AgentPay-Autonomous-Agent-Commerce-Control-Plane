"""Unit tests for Milestone M10: Supply Chain, Web Frontend & Hardening (§54, §56, §74).

Verifies:
- docker-compose.yml loopback port bindings and Redis authentication.
- .github/workflows/ci.yml container image version pinning without mutable :latest tags.
- apps/web/index.html Content-Security-Policy and Subresource Integrity (SRI) on external scripts.
- apps/web/vite.config.ts security headers for dev and preview.
- apps/web/package.json Vite version vulnerability remediation.
"""

import re
from pathlib import Path


def _find_repo_root() -> Path:
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "pyproject.toml").exists() and (current / "apps").exists():
            return current
        current = current.parent
    raise RuntimeError("Repository root not found")


REPO_ROOT = _find_repo_root()


def test_docker_compose_loopback_bindings_and_auth() -> None:
    """docker-compose.yml must bind services to 127.0.0.1 and enforce Redis authentication."""
    compose_path = REPO_ROOT / "docker-compose.yml"
    assert compose_path.exists(), "docker-compose.yml must exist at repo root"

    content = compose_path.read_text(encoding="utf-8")

    # Assert loopback binding for postgres
    assert "127.0.0.1:5432:5432" in content, (
        "Postgres port must be bound to 127.0.0.1 loopback interface"
    )
    assert '"5432:5432"' not in content, (
        "Postgres port must not be exposed to 0.0.0.0 (all interfaces)"
    )

    # Assert loopback binding for redis
    assert "127.0.0.1:6379:6379" in content, (
        "Redis port must be bound to 127.0.0.1 loopback interface"
    )
    assert '"6379:6379"' not in content, (
        "Redis port must not be exposed to 0.0.0.0 (all interfaces)"
    )

    # Assert Redis password authentication is configured
    assert "--requirepass" in content, "Redis must be configured with --requirepass authentication"
    assert "REDIS_PASSWORD" in content, (
        "Redis requirepass must use REDIS_PASSWORD environment variable with fallback"
    )


def test_ci_workflow_container_image_pinning() -> None:
    """.github/workflows/ci.yml must pin container images to specific tags without :latest."""
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists(), "ci.yml must exist"

    content = ci_path.read_text(encoding="utf-8")

    # Gitleaks must not use :latest
    assert "gitleaks:latest" not in content, "CI workflow must not use mutable gitleaks:latest"
    assert re.search(r"zricethezav/gitleaks:v\d+\.\d+\.\d+", content), (
        "CI workflow must pin gitleaks to an immutable semantic version tag (e.g. v8.21.2)"
    )

    # No container runs in ci.yml should use :latest
    for line in content.splitlines():
        if "docker run" in line or "image:" in line:
            assert ":latest" not in line, f"Container image in CI line must not use :latest: {line}"


def test_web_index_html_has_csp_and_sri() -> None:
    """apps/web/index.html must include strict CSP and SRI integrity on external scripts."""
    index_path = REPO_ROOT / "apps" / "web" / "index.html"
    assert index_path.exists(), "apps/web/index.html must exist"

    content = index_path.read_text(encoding="utf-8")

    # 1. Assert Content-Security-Policy meta tag exists
    csp_match = re.search(
        r'<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]+)"',
        content,
        re.IGNORECASE,
    )
    assert csp_match is not None, "index.html must contain a Content-Security-Policy meta tag"
    csp_policy = csp_match.group(1)

    assert "default-src 'self'" in csp_policy, "CSP must specify default-src 'self'"
    assert "object-src 'none'" in csp_policy, "CSP must disable plugins with object-src 'none'"
    assert "script-src" in csp_policy, "CSP must define script-src"
    assert "style-src" in csp_policy, "CSP must define style-src"

    # 2. Assert external scripts have SRI and version pinning
    script_tags = re.findall(r"<script\s+[^>]*>", content, re.IGNORECASE)
    external_scripts_found = 0
    for tag in script_tags:
        src_match = re.search(r'src=["\']([^"\']+)["\']', tag)
        if not src_match:
            continue
        src = src_match.group(1)
        if src.startswith("http://") or src.startswith("https://"):
            external_scripts_found += 1
            # Must be version pinned
            assert re.search(r"/\d+\.\d+\.\d+", src), (
                f"External script {src} must be pinned to a semantic version"
            )
            # Must have integrity attribute
            assert re.search(r'integrity=["\']sha(256|384|512)-[^"\']+["\']', tag), (
                f"External script tag {tag} must specify a valid sha256/384/512 SRI hash"
            )
            # Must specify crossorigin
            assert 'crossorigin="anonymous"' in tag, (
                f'External script tag {tag} must specify crossorigin="anonymous"'
            )

    assert external_scripts_found >= 1, (
        "At least one external script should be tested (Tailwind CDN)"
    )


def test_vite_config_security_headers() -> None:
    """apps/web/vite.config.ts must configure security headers for dev and preview servers."""
    config_path = REPO_ROOT / "apps" / "web" / "vite.config.ts"
    assert config_path.exists(), "vite.config.ts must exist"

    content = config_path.read_text(encoding="utf-8")
    assert "X-Content-Type-Options" in content, "vite.config.ts must define X-Content-Type-Options"
    assert "X-Frame-Options" in content, "vite.config.ts must define X-Frame-Options"
    assert "Content-Security-Policy" in content, (
        "vite.config.ts must define Content-Security-Policy"
    )


def test_web_package_json_vite_version() -> None:
    """apps/web/package.json must specify Vite >= 6.2.0 to resolve GHSA-8g5g-mxqh-grgj."""
    package_json_path = REPO_ROOT / "apps" / "web" / "package.json"
    assert package_json_path.exists(), "apps/web/package.json must exist"

    content = package_json_path.read_text(encoding="utf-8")
    vite_match = re.search(r'"vite":\s*"\^?(\d+)\.(\d+)\.(\d+)"', content)
    assert vite_match is not None, "apps/web/package.json must specify vite dependency"

    major, minor, patch = (
        int(vite_match.group(1)),
        int(vite_match.group(2)),
        int(vite_match.group(3)),
    )
    assert (major, minor, patch) >= (6, 2, 0), (
        f"Vite version {major}.{minor}.{patch} must be >= 6.2.0 to remediate GHSA-8g5g-mxqh-grgj"
    )
