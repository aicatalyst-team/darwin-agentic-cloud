"""Smoke tests: import the package and check basic invariants.

These are the absolute floor — if these fail, nothing else can work.
"""

from __future__ import annotations

import dac


def test_package_imports() -> None:
    """The package can be imported."""
    assert dac is not None


def test_package_has_version() -> None:
    """The package exposes __version__ as a string."""
    assert isinstance(dac.__version__, str)
    assert dac.__version__
    assert dac.__version__[0].isdigit()


def test_attestation_schema_is_versioned() -> None:
    """The attestation schema identifier is set and stable."""
    assert isinstance(dac.ATTESTATION_SCHEMA, str)
    assert "dac.darwinic.cloud" in dac.ATTESTATION_SCHEMA
    assert "/v" in dac.ATTESTATION_SCHEMA
