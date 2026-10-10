"""Tests for CycloneDX 1.5 JSON SBOM & SLSA 1.2 Build Provenance generator (§57, §130)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.generate_sbom import (
    generate_cyclonedx_sbom,
    generate_slsa_provenance,
    verify_provenance,
    verify_sbom,
)


def test_generate_and_verify_cyclonedx_sbom(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    sbom = generate_cyclonedx_sbom(repo_root)

    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    assert sbom["serialNumber"].startswith("urn:uuid:")
    assert sbom["metadata"]["component"]["name"] == "agentpay"

    components = sbom["components"]
    assert len(components) > 0

    purls = [c["purl"] for c in components]
    # Check that both python and npm packages exist in components
    assert any(p.startswith("pkg:pypi/") for p in purls)
    assert any(p.startswith("pkg:npm/") for p in purls)

    # Write to tmp_path and verify
    sbom_path = tmp_path / "sbom.json"
    with sbom_path.open("w", encoding="utf-8") as f:
        json.dump(sbom, f)

    assert verify_sbom(sbom_path) is True


def test_generate_and_verify_slsa_provenance(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[4]
    sbom = generate_cyclonedx_sbom(repo_root)
    prov = generate_slsa_provenance(repo_root, sbom)

    assert prov["_type"] == "https://in-toto.io/Statement/v1"
    assert prov["predicateType"] == "https://slsa.dev/provenance/v1"
    assert len(prov["subject"]) == 1
    assert "sha256" in prov["subject"][0]["digest"]

    build_def = prov["predicate"]["buildDefinition"]
    assert len(build_def["resolvedDependencies"]) > 0

    prov_path = tmp_path / "provenance.json"
    with prov_path.open("w", encoding="utf-8") as f:
        json.dump(prov, f)

    assert verify_provenance(prov_path) is True


def test_verify_sbom_rejects_invalid(tmp_path: Path) -> None:
    bad_sbom_path = tmp_path / "bad_sbom.json"

    # Missing specVersion
    with bad_sbom_path.open("w", encoding="utf-8") as f:
        json.dump({"bomFormat": "CycloneDX", "specVersion": "1.4"}, f)
    assert verify_sbom(bad_sbom_path) is False

    # Missing components
    with bad_sbom_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "bomFormat": "CycloneDX",
                "specVersion": "1.5",
                "serialNumber": "urn:uuid:1234",
                "metadata": {"component": {"name": "test"}},
                "components": [],
            },
            f,
        )
    assert verify_sbom(bad_sbom_path) is False


def test_verify_provenance_rejects_invalid(tmp_path: Path) -> None:
    bad_prov_path = tmp_path / "bad_prov.json"

    # Missing subject digest
    with bad_prov_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "_type": "https://in-toto.io/Statement/v1",
                "predicateType": "https://slsa.dev/provenance/v1",
                "subject": [{"name": "bad"}],
                "predicate": {"buildDefinition": {"resolvedDependencies": []}},
            },
            f,
        )
    assert verify_provenance(bad_prov_path) is False
