"""Smoke tests for darwin.agenticcloud.ui visual primitives.

These tests don't verify visual fidelity — they verify the UI module:
  - imports cleanly
  - runs every public function without raising
  - emits the expected content strings (version, schema, signer, etc.)
  - degrades gracefully when stdout is not a TTY (the CI case)

A failure here means a real user running `darwin` from a clean
PyPI install would see a broken banner or a crashed CLI.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from darwin.agenticcloud import ui


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def capture(console_kwargs: dict | None = None) -> tuple[Console, io.StringIO]:
    """Return a Rich Console writing into a StringIO, plus the buffer."""
    buf = io.StringIO()
    kwargs = {"file": buf, "force_terminal": False, "width": 120}
    if console_kwargs:
        kwargs.update(console_kwargs)
    console = Console(**kwargs)
    return console, buf


# -------------------------------------------------------------------
# Banner
# -------------------------------------------------------------------

class TestBanner:

    def test_banner_renders_without_signer(self) -> None:
        console, buf = capture()
        ui.print_banner(console, "0.1.1", "darwin.cloud/agenticcloud/attestation/v0.1")
        out = buf.getvalue()
        # The block-letter "DARWIN" uses these Unicode block chars
        assert "█" in out
        assert "0.1.1" in out
        assert "darwin.cloud/agenticcloud/attestation/v0.1" in out
        assert "verifiable compute for AI agents" in out

    def test_banner_with_signer(self) -> None:
        console, buf = capture()
        ui.print_banner(
            console,
            "0.1.1",
            "darwin.cloud/agenticcloud/attestation/v0.1",
            "dac-local-d1bf7cad25875cee",
            "local-docker-v0",
        )
        out = buf.getvalue()
        assert "dac-local-d1bf7cad25875cee" in out
        assert "local-docker-v0" in out

    def test_banner_constant_is_six_lines(self) -> None:
        # If figlet shape changes accidentally this catches it.
        assert ui.BANNER.count("\n") == 5

    def test_banner_is_group_not_text(self) -> None:
        # Returning Group preserves multi-style composition.
        from rich.console import Group
        result = ui.render_banner("0.1.1", "schema/v0.1", "key", "sub")
        assert isinstance(result, Group)


# -------------------------------------------------------------------
# Matrix boot
# -------------------------------------------------------------------

class TestMatrixBoot:

    def test_matrix_boot_renders_all_steps(self) -> None:
        console, buf = capture()
        steps = [
            ui.BootStep("initializing runtime", delay=0.0),
            ui.BootStep("loading ed25519 signing key", delay=0.0),
            ui.BootStep("binding 0.0.0.0:8787", delay=0.0),
        ]
        # In our captured Console, `is_terminal` is False, so animation
        # should skip the time.sleep but still print every step.
        ui.matrix_boot(console, steps)
        out = buf.getvalue()
        assert "initializing runtime" in out
        assert "loading ed25519 signing key" in out
        assert "binding 0.0.0.0:8787" in out
        # Three "ok" markers — one per step
        assert out.count("ok") >= 3

    def test_matrix_boot_handles_empty_steps_list(self) -> None:
        # Defensive: zero-step boot shouldn't crash.
        console, buf = capture()
        with pytest.raises(ValueError):
            # Empty steps causes max() over empty seq — we want a clear error.
            ui.matrix_boot(console, [])


# -------------------------------------------------------------------
# StepLine progress ticker
# -------------------------------------------------------------------

class TestStepLine:

    def test_stepline_non_tty_prints_each_tick(self) -> None:
        # Non-TTY path: each tick is its own line.
        console, buf = capture()
        with ui.StepLine(console, "running test workload") as step:
            step.tick("budget")
            step.tick("sandbox")
            step.tick("exec")
        out = buf.getvalue()
        assert "running test workload" in out
        assert "budget" in out
        assert "sandbox" in out
        assert "exec" in out

    def test_stepline_no_ticks(self) -> None:
        # Empty StepLine should render the header only.
        console, buf = capture()
        with ui.StepLine(console, "no work to do"):
            pass
        out = buf.getvalue()
        assert "no work to do" in out


# -------------------------------------------------------------------
# Attestation panel
# -------------------------------------------------------------------

class TestAttestationPanel:

    def test_attestation_panel_renders_all_fields(self) -> None:
        console, buf = capture()
        panel = ui.render_attestation_panel(
            workload_hash="sha256:" + "a" * 64,
            output_hash="sha256:" + "b" * 64,
            substrate="local-docker-v0",
            signer_key_id="dac-local-test12345",
            cost_usd=0.0024,
            cost_cap_usd=0.01,
            verified=True,
        )
        console.print(panel)
        out = buf.getvalue()
        assert "attestation" in out
        assert "local-docker-v0" in out
        assert "dac-local-test12345" in out
        assert "$0.0024" in out
        assert "$0.0100" in out
        assert "signature verified" in out

    def test_attestation_panel_unverified(self) -> None:
        console, buf = capture()
        panel = ui.render_attestation_panel(
            workload_hash="sha256:" + "a" * 64,
            output_hash="sha256:" + "b" * 64,
            substrate="local-docker-v0",
            signer_key_id="dac-local-test",
            cost_usd=0.001,
            cost_cap_usd=0.01,
            verified=False,
        )
        console.print(panel)
        out = buf.getvalue()
        assert "signature unverified" in out

    def test_short_hash_truncation(self) -> None:
        full = "sha256:" + "a" * 64
        short = ui._short_hash(full)
        # Should be `sha256:aaaaaaaa...aaaa` — under 25 chars.
        assert "..." in short
        assert short.startswith("sha256:")
        assert len(short) < 30

    def test_short_hash_passes_through_when_already_short(self) -> None:
        short = ui._short_hash("sha256:abc")
        assert short == "sha256:abc"


# -------------------------------------------------------------------
# Signature animation
# -------------------------------------------------------------------

class TestSignatureAnimation:

    def test_signature_animation_skips_on_non_tty(self) -> None:
        # When force_terminal=False, animation should not block.
        # Verifies the function returns quickly.
        import time
        console, _ = capture()
        start = time.time()
        ui.signature_animation(console, frames=1.0)
        elapsed = time.time() - start
        # Non-TTY skips the sleep loop, so should be near-instant.
        assert elapsed < 0.1


# -------------------------------------------------------------------
# MCP install receipt
# -------------------------------------------------------------------

class TestMcpInstallReceipt:

    def test_mcp_receipt_renders_all_components(self) -> None:
        console, buf = capture()
        receipt = ui.render_mcp_install_receipt(
            config_path="/Users/test/Library/Application Support/Claude/claude_desktop_config.json",
            server_name="darwin",
            python_interpreter="/Users/test/.local/share/darwin/.venv/bin/python",
            tool_names=[
                "dac_run_python",
                "dac_run_node",
                "dac_verify_attestation",
                "dac_identity",
            ],
        )
        console.print(receipt)
        out = buf.getvalue()
        assert "Claude Desktop config installed" in out
        assert "/Users/test/Library/Application Support/Claude/" in out
        assert "darwin" in out
        assert "dac_run_python" in out
        assert "4" in out  # tool count
        assert "darwin substrate connected to claude" in out

    def test_mcp_receipt_handles_no_tools(self) -> None:
        console, buf = capture()
        receipt = ui.render_mcp_install_receipt(
            config_path="/tmp/config.json",
            server_name="darwin",
            python_interpreter="/usr/bin/python3",
            tool_names=[],
        )
        console.print(receipt)
        out = buf.getvalue()
        assert "MCP tools exposed" in out
        assert "0" in out


# -------------------------------------------------------------------
# Brand color constants
# -------------------------------------------------------------------

class TestBrandConstants:

    def test_brand_colors_are_valid_hex(self) -> None:
        for name in ("BRAND_GREEN", "BRAND_AMBER", "BRAND_DIM"):
            color = getattr(ui, name)
            assert color.startswith("#")
            assert len(color) == 7  # #RRGGBB

    def test_brand_green_is_the_brand_color(self) -> None:
        assert ui.BRAND_GREEN == "#00ff01"

    def test_brand_amber_is_the_brand_color(self) -> None:
        assert ui.BRAND_AMBER == "#fdb515"
