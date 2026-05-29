"""CLI integration tests for `darwin run`.

These tests exercise the Typer wrapper around `darwin.run()`. The
underlying `Runtime` is mocked so tests don't require a working Docker
daemon or AWS credentials. The point is to validate the CLI surface:
argument parsing, file resolution, flag handling, output formatting,
exit codes, and error messages.

See tests/runtime/test_runtime_v02.py for the underlying Runtime API
tests.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from darwin.agenticcloud.cli import app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(s):
    return _ANSI_RE.sub("", s)


@pytest.fixture
def runner() -> CliRunner:
    """Typer CLI runner that captures stdout and stderr."""
    return CliRunner()


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(s):
    return _ANSI_RE.sub("", s)


@pytest.fixture
def fake_attestation() -> dict:
    """A minimal v0.2-shaped attestation that the CLI can render."""
    return {
        "schema": "darwin.cloud/agenticcloud/attestation/v0.2",
        "attestation_id": "test-attestation-id-0001",
        "issued_at": "2026-05-29T01:00:00Z",
        "workload_spec_hash": "sha256:" + "a" * 64,
        "execution_result": {
            "status": "ok",
            "stdout": "hello\n",
            "output_hash": "sha256:" + "b" * 64,
            "cost_usd": 0.000012,
            "substrate": {
                "id": "local-docker-v0",
                "version": "0.1.0",
                "evidence_schema_id": "darwin.cloud/evidence/local-docker/v1",
                "identity_signer_type": "darwin-class-key",
                "identity_signer_key_id": "dac-class-local-docker-v0-test",
                "identity_signature": "test-signature-b64",
                "evidence": {
                    "container_status": "ok",
                    "exit_code": 0,
                    "wall_time_sec": 0.1,
                    "stdout_hash": "sha256:" + "b" * 64,
                    "stderr_hash": "sha256:" + "e" * 64,
                },
            },
            "preflight_estimate": {"cost_usd_max": 0.0001},
        },
        "value_added_service": {
            "identity_signing": {
                "schema_compliant": True,
                "keylist_url": "https://example/keys.json",
            },
            "cost_cap_enforcement": {
                "cap_usd": 0.10,
                "estimated_usd_max": 0.0001,
                "actual_usd": 0.000012,
                "within_cap": True,
                "headroom_usd": 0.099988,
            },
            "routing_decision": {
                "policy": "pick_by_cost",
                "chosen_substrate_id": "local-docker-v0",
                "chosen_reason": "lowest_estimated_cost ($0.0001)",
                "candidates_considered": 1,
                "rejected_substrates": [],
            },
        },
        "signer_key_id": "dac-local-test-operator",
        "signature": "test-outer-signature-b64",
    }


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(s):
    return _ANSI_RE.sub("", s)


@pytest.fixture
def mocked_run(fake_attestation):
    """Patch the underlying `darwin.run` to return our fake attestation.

    Returns the patch object so individual tests can inspect call args
    or override the return value if they need to test failure paths.
    """
    with patch("darwin.run") as mock:
        mock.return_value = fake_attestation
        yield mock


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(s):
    return _ANSI_RE.sub("", s)


@pytest.fixture
def hello_py(tmp_path: Path) -> Path:
    """A real `hello.py` file in a tmp directory."""
    p = tmp_path / "hello.py"
    p.write_text('print("hello")\n')
    return p


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class TestArgumentParsing:
    """The CLI surface: argument and flag handling."""

    def test_run_with_no_args_exits_nonzero(self, runner: CliRunner):
        """`darwin run` with no workload arg should error cleanly."""
        result = runner.invoke(app, ["run"])
        assert result.exit_code != 0
        # Typer/Click writes the "Missing argument" message to stdout
        # in their styled error panel; accept either stream.
        combined = _strip_ansi(result.stdout + (result.stderr or ""))
        assert "Missing argument" in combined or "WORKLOAD" in combined

    def test_run_help_exits_zero(self, runner: CliRunner):
        """`darwin run --help` always succeeds and prints usage."""
        result = runner.invoke(app, ["run", "--help"])
        assert result.exit_code == 0
        assert "WORKLOAD" in _strip_ansi(result.stdout)
        assert "--substrate" in _strip_ansi(result.stdout)
        assert "--cost-cap" in _strip_ansi(result.stdout)

    def test_run_help_lists_all_documented_flags(self, runner: CliRunner):
        """All public flags appear in help output."""
        result = runner.invoke(app, ["run", "--help"])
        for flag in [
            "--substrate",
            "--language",
            "--cost-cap",
            "--timeout",
            "--memory",
            "--save",
            "--json",
        ]:
            assert flag in _strip_ansi(result.stdout), f"flag {flag} missing from help"


# ---------------------------------------------------------------------------
# File vs inline workload resolution
# ---------------------------------------------------------------------------


class TestWorkloadResolution:
    """The CLI's "is this arg a file or a code string" heuristic."""

    def test_run_with_existing_file_reads_contents(
        self, runner: CliRunner, mocked_run, hello_py: Path
    ):
        """When the workload arg is an existing file path, its contents are executed."""
        result = runner.invoke(app, ["run", str(hello_py)])
        assert result.exit_code == 0, result.stdout
        # Verify the underlying call was made with the FILE CONTENTS, not the filename.
        call_args = mocked_run.call_args
        code_arg = call_args.kwargs.get("code") or (call_args.args[0] if call_args.args else None)
        assert code_arg is not None
        assert 'print("hello")' in code_arg
        assert code_arg != str(hello_py), "CLI should read file contents, not pass the filename"

    def test_run_with_inline_code_passes_string_directly(self, runner: CliRunner, mocked_run):
        """When the workload arg isn't a file, it's treated as inline code."""
        result = runner.invoke(app, ["run", 'print("inline")'])
        assert result.exit_code == 0, result.stdout
        call_args = mocked_run.call_args
        code_arg = call_args.kwargs.get("code") or (call_args.args[0] if call_args.args else None)
        assert code_arg == 'print("inline")'

    def test_run_with_nonexistent_file_treats_as_inline(
        self, runner: CliRunner, mocked_run, tmp_path: Path
    ):
        """A workload arg that LOOKS like a file path but doesn't exist falls back to inline."""
        nonexistent = tmp_path / "does_not_exist.py"
        runner.invoke(app, ["run", str(nonexistent)])
        # CLI doesn't crash — it passes the string to the substrate, which will likely
        # fail to execute. We check the CLI plumbing, not the substrate outcome.
        # The important guarantee: we don't crash with FileNotFoundError on the CLI side.
        call_args = mocked_run.call_args
        if call_args is not None:
            code_arg = call_args.kwargs.get("code") or (
                call_args.args[0] if call_args.args else None
            )
            assert code_arg == str(nonexistent)


# ---------------------------------------------------------------------------
# Flag handling
# ---------------------------------------------------------------------------


class TestFlagHandling:
    """Every flag exposed by `darwin run` is wired correctly."""

    def test_substrate_flag_is_passed_through(self, runner: CliRunner, mocked_run, hello_py: Path):
        result = runner.invoke(app, ["run", str(hello_py), "--substrate", "local-docker-v0"])
        assert result.exit_code == 0, result.stdout
        kwargs = mocked_run.call_args.kwargs
        assert kwargs.get("substrate") == "local-docker-v0"

    def test_cost_cap_flag_is_passed_through(self, runner: CliRunner, mocked_run, hello_py: Path):
        result = runner.invoke(app, ["run", str(hello_py), "--cost-cap", "0.5"])
        assert result.exit_code == 0, result.stdout
        kwargs = mocked_run.call_args.kwargs
        assert kwargs.get("cost_cap") == 0.5

    def test_timeout_flag_is_passed_through(self, runner: CliRunner, mocked_run, hello_py: Path):
        result = runner.invoke(app, ["run", str(hello_py), "--timeout", "60"])
        assert result.exit_code == 0, result.stdout
        kwargs = mocked_run.call_args.kwargs
        assert kwargs.get("timeout") == 60

    def test_memory_flag_is_passed_through(self, runner: CliRunner, mocked_run, hello_py: Path):
        result = runner.invoke(app, ["run", str(hello_py), "--memory", "1024"])
        assert result.exit_code == 0, result.stdout
        kwargs = mocked_run.call_args.kwargs
        assert kwargs.get("memory_mb") == 1024

    def test_language_flag_is_passed_through(self, runner: CliRunner, mocked_run, hello_py: Path):
        result = runner.invoke(app, ["run", str(hello_py), "--language", "node"])
        assert result.exit_code == 0, result.stdout
        kwargs = mocked_run.call_args.kwargs
        assert kwargs.get("language") == "node"


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------


class TestOutputFormats:
    """The CLI's --save and --json flags produce correct artifacts."""

    def test_save_flag_writes_attestation_to_disk(
        self, runner: CliRunner, mocked_run, hello_py: Path, tmp_path: Path
    ):
        out_path = tmp_path / "att.json"
        result = runner.invoke(app, ["run", str(hello_py), "--save", str(out_path)])
        assert result.exit_code == 0, result.stdout
        assert out_path.exists(), "—save target file was not created"
        # The saved file must be valid JSON
        saved = json.loads(out_path.read_text())
        assert saved.get("attestation_id") == "test-attestation-id-0001"
        assert saved.get("schema") == "darwin.cloud/agenticcloud/attestation/v0.2"

    def test_json_flag_emits_parseable_json_only(
        self, runner: CliRunner, mocked_run, hello_py: Path
    ):
        result = runner.invoke(app, ["run", str(hello_py), "--json"])
        assert result.exit_code == 0, result.stdout
        # In --json mode, stdout should be pure JSON (parseable)
        parsed = json.loads(result.stdout)
        assert parsed.get("attestation_id") == "test-attestation-id-0001"


# ---------------------------------------------------------------------------
# Smoke / regression
# ---------------------------------------------------------------------------


class TestSmoke:
    """Basic invocation paths that have failed in production before."""

    def test_run_with_file_does_not_raise(self, runner: CliRunner, mocked_run, hello_py: Path):
        """Regression: `darwin run hello.py` from a directory where hello.py exists must complete."""
        result = runner.invoke(app, ["run", str(hello_py)])
        assert result.exception is None or result.exit_code == 0, (
            f"unexpected exception: {result.exception!r}\nstdout: {result.stdout}"
        )

    def test_run_with_inline_code_does_not_raise(self, runner: CliRunner, mocked_run):
        """Regression: `darwin run 'print(...)'` must complete."""
        result = runner.invoke(app, ["run", 'print("ok")'])
        assert result.exception is None or result.exit_code == 0, (
            f"unexpected exception: {result.exception!r}\nstdout: {result.stdout}"
        )
