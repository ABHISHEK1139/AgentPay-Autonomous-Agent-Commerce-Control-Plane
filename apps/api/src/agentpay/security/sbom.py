"""Supply Chain Security: CycloneDX 1.5 JSON SBOM & SLSA 1.2 Provenance Service (§57, §130).

Provides programmatic access to Software Bill of Materials (SBOM) and SLSA build
provenance attestations from within the application and API layers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def get_repo_root() -> Path:
    """Resolve repository root from current module location."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "uv.lock").is_file():
            return parent
    return Path(__file__).resolve().parents[5]


def get_cyclonedx_sbom(repo_root: Path | None = None) -> dict[str, Any]:
    """Retrieve CycloneDX 1.5 JSON Software Bill of Materials."""
    from scripts.generate_sbom import generate_cyclonedx_sbom

    root = repo_root or get_repo_root()
    return generate_cyclonedx_sbom(root)


def get_slsa_provenance(repo_root: Path | None = None) -> dict[str, Any]:
    """Retrieve SLSA 1.2 Build Provenance attestation."""
    from scripts.generate_sbom import generate_cyclonedx_sbom, generate_slsa_provenance

    root = repo_root or get_repo_root()
    sbom = generate_cyclonedx_sbom(root)
    return generate_slsa_provenance(root, sbom)


def get_sbom_metadata(repo_root: Path | None = None) -> dict[str, Any]:
    """Generate combined SBOM and build provenance response for security audits."""
    root = repo_root or get_repo_root()
    sbom = get_cyclonedx_sbom(root)
    prov = get_slsa_provenance(root)

    return {
        "format": "CycloneDX-1.5",
        "spec_version": sbom.get("specVersion", "1.5"),
        "serial_number": sbom.get("serialNumber"),
        "component_count": len(sbom.get("components", [])),
        "slsa_predicate_type": prov.get("predicateType"),
        "sbom": sbom,
        "provenance": prov,
    }
