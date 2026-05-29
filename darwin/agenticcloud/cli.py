"""Darwin Agentic Cloud command-line interface.

Examples:
    darwin run hello.py
    darwin run hello.py --timeout 30 --memory 256
    darwin keys show
    darwin attest verify ./attestation.json
    darwin serve
    darwin mcp serve
    darwin history list
    darwin history stats
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from darwin.agenticcloud.admin_cli import admin_app
from darwin.agenticcloud.attestation import verify_attestation
from darwin.agenticcloud.runtime import Runtime
from darwin.agenticcloud.signing import Signer
from darwin.agenticcloud.types import WorkloadSpec

app = typer.Typer(
    name="darwin",
    help="Darwin Agentic Cloud — verifiable compute for AI agents.",
    no_args_is_help=True,
    add_completion=False,
)


# -------------------------------------------------------------------
# First-run welcome
# -------------------------------------------------------------------

_WELCOME_MARKER = Path.home() / ".darwin" / "welcomed"


def _show_welcome_if_first_run() -> None:
    """Print the welcome banner exactly once per user install.

    Suppressed in non-interactive shells, when piped, or when the user
    sets DARWIN_SUPPRESS_WELCOME=1. The marker file at
    ~/.darwin/welcomed records that the user has been welcomed.
    """
    try:
        if not sys.stdout.isatty():
            return
        if os.environ.get("DARWIN_SUPPRESS_WELCOME"):
            return
        if _WELCOME_MARKER.exists():
            return

        from darwin import __version__ as _v
        from darwin.agenticcloud.ui import print_welcome

        print_welcome(Console(), version=_v)

        _WELCOME_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _WELCOME_MARKER.touch()
    except Exception:
        # Welcome banner must never block real commands. If anything
        # goes wrong here (terminal weirdness, IO error, etc.), swallow
        # silently and proceed with the user's command.
        pass


@app.callback()
def _root(ctx: typer.Context) -> None:
    """Darwin Agentic Cloud — verifiable compute for AI agents."""
    # The `welcome` command renders the banner itself; skip the
    # auto-trigger here to avoid double-rendering.
    if ctx.invoked_subcommand == "welcome":
        return
    _show_welcome_if_first_run()


@app.command()
def welcome() -> None:
    """Show the Darwin welcome banner."""
    from darwin import __version__ as _v
    from darwin.agenticcloud.ui import print_welcome

    print_welcome(Console(), version=_v)
keys_app = typer.Typer(help="Manage signing keys.", no_args_is_help=True)
attest_app = typer.Typer(help="Work with attestations.", no_args_is_help=True)
history_app = typer.Typer(help="Query attestation history.", no_args_is_help=True)
mcp_app = typer.Typer(help="Model Context Protocol (MCP) server.", no_args_is_help=True)
substrates_app = typer.Typer(
    help="Inspect and demo substrates (Phase 2).",
    no_args_is_help=True,
)

app.add_typer(keys_app, name="keys")
app.add_typer(attest_app, name="attest")
app.add_typer(history_app, name="history")
app.add_typer(mcp_app, name="mcp")
app.add_typer(substrates_app, name="substrates")
app.add_typer(admin_app, name="admin")

console = Console()
err_console = Console(stderr=True)


# -------------------------------------------------------------------
# Top-level commands
# -------------------------------------------------------------------

# -------------------------------------------------------------------
# v0.2 headline command
# -------------------------------------------------------------------


@app.command()
def run(
    workload: Annotated[
        str,
        typer.Argument(
            help="Code string or path to a script file. "
            "If the argument exists as a file path, it is read; "
            "otherwise it is executed inline.",
        ),
    ],
    substrate: Annotated[
        str | None,
        typer.Option(
            "--substrate",
            "-s",
            help="Substrate to use (short name like 'aws-batch' or full ID). "
            "Omit for auto-routing (cheapest available).",
        ),
    ] = None,
    language: Annotated[
        str, typer.Option("--language", "-l", help="Workload language.")
    ] = "python",
    cost_cap: Annotated[
        float,
        typer.Option("--cost-cap", help="Cost ceiling in USD."),
    ] = 0.10,
    timeout: Annotated[int, typer.Option("--timeout", "-t", help="Timeout in seconds.")] = 30,
    memory: Annotated[int, typer.Option("--memory", "-m", help="Memory limit in MB.")] = 512,
    save: Annotated[
        Path | None,
        typer.Option("--save", help="Write the signed v0.2 attestation JSON to this path."),
    ] = None,
    json_only: Annotated[
        bool,
        typer.Option("--json", help="Print only the signed attestation JSON (no cert panel)."),
    ] = False,
) -> None:
    """Execute a workload and emit a signed v0.2 attestation receipt.

    Default behavior auto-routes to the cheapest available substrate.
    Override with --substrate to pick a specific one.

    Examples:

        darwin run "print('hi')"
        darwin run hello.py
        darwin run hello.py --substrate aws-batch
        darwin run hello.py --cost-cap 1.0 --timeout 300
    """
    # Resolve workload arg: file path if it exists, else literal code.
    candidate = Path(workload)
    if candidate.exists() and candidate.is_file():
        code = candidate.read_text(encoding="utf-8")
        source_label = candidate.name
    else:
        code = workload
        source_label = "<inline>"

    from darwin import run as _darwin_run

    if json_only:
        attestation = _darwin_run(
            code,
            substrate=substrate,
            language=language,
            cost_cap=cost_cap,
            timeout=timeout,
            memory_mb=memory,
        )
    else:
        from darwin.agenticcloud.ui import StepLine, signature_animation

        with StepLine(console, f"darwin.agenticcloud · running {source_label}") as step:
            step.tick("discover")
            step.tick("route")
            step.tick("execute")
            attestation = _darwin_run(
                code,
                substrate=substrate,
                language=language,
                cost_cap=cost_cap,
                timeout=timeout,
                memory_mb=memory,
            )
            step.tick("attest")
            signature_animation(console, frames=0.4)
            step.tick("sign")

    if save is not None:
        save.write_text(json.dumps(attestation, indent=2), encoding="utf-8")

    if json_only:
        print(json.dumps(attestation, indent=2))
        return

    # Cert panel
    from darwin.agenticcloud.ui import render_attestation_panel_auto

    console.print()
    console.print(render_attestation_panel_auto(attestation))
    console.print()

    # Echo workload stdout / stderr so users see their output
    result = attestation.get("execution_result", {})
    if result.get("stdout"):
        console.print(
            result["stdout"],
            end="" if result["stdout"].endswith("\n") else "\n",
        )
    if result.get("stderr"):
        err_console.print(
            result["stderr"],
            end="" if result["stderr"].endswith("\n") else "\n",
        )

    if save is not None:
        console.print(f"[dim]signed attestation saved to[/dim] {save}")


@attest_app.command("run-v01")
def attest_run_v01(
    file: Annotated[Path, typer.Argument(help="Path to the script to run.")],
    language: Annotated[
        str, typer.Option("--language", "-l", help="Language (python or node).")
    ] = "python",
    timeout: Annotated[int, typer.Option("--timeout", "-t", help="Timeout in seconds.")] = 30,
    memory: Annotated[int, typer.Option("--memory", "-m", help="Memory limit in MB.")] = 512,
    cost_cap: Annotated[float, typer.Option("--cost-cap", help="Cost ceiling in USD.")] = 0.01,
    save: Annotated[
        Path | None,
        typer.Option("--save", help="Write the signed attestation to this path."),
    ] = None,
    json_only: Annotated[
        bool, typer.Option("--json", help="Print only the signed attestation JSON.")
    ] = False,
) -> None:
    """Legacy v0.1 attestation runner — execute a script, emit a v0.1 signed attestation.

    Kept for backward compatibility. New workloads should use `darwin run`, which
    produces v0.2 attestations with the VAS block and cert-style receipt.
    """
    _run_v01_impl(
        file=file,
        language=language,
        timeout=timeout,
        memory=memory,
        cost_cap=cost_cap,
        save=save,
        json_only=json_only,
    )


def _run_v01_impl(
    file: Annotated[Path, typer.Argument(help="Path to the script to run.")],
    language: Annotated[
        str, typer.Option("--language", "-l", help="Language (python or node).")
    ] = "python",
    timeout: Annotated[int, typer.Option("--timeout", "-t", help="Timeout in seconds.")] = 30,
    memory: Annotated[int, typer.Option("--memory", "-m", help="Memory limit in MB.")] = 512,
    cost_cap: Annotated[float, typer.Option("--cost-cap", help="Cost ceiling in USD.")] = 0.01,
    save: Annotated[
        Path | None, typer.Option("--save", help="Write the signed attestation to this path.")
    ] = None,
    json_only: Annotated[
        bool, typer.Option("--json", help="Print only the signed attestation JSON.")
    ] = False,
) -> None:
    """Execute a script in the darwin.agenticcloud sandbox and emit a signed attestation."""
    if not file.exists():
        err_console.print(f"[red]File not found:[/red] {file}")
        raise typer.Exit(code=2)

    code = file.read_text(encoding="utf-8")
    spec = WorkloadSpec(
        code=code,
        language=language,
        timeout_sec=timeout,
        memory_mb=memory,
        cost_cap_usd=cost_cap,
    )

    from darwin.agenticcloud.ui import (
        StepLine,
        render_attestation_panel,
        signature_animation,
    )

    runtime = Runtime()

    # Single-line progressive ticker shown only when not in --json mode
    if not json_only:
        with StepLine(console, f"darwin.agenticcloud · running {file.name}") as step:
            step.tick("budget")
            step.tick("sandbox")
            step.tick("exec")
            signed = runtime.run(spec)
            step.tick("hash")
            signature_animation(console, frames=0.4)
            step.tick("sign")
    else:
        signed = runtime.run(spec)

    signed_dict = {
        "attestation": signed.attestation,
        "signature_b64": signed.signature_b64,
        "public_key_b64": signed.public_key_b64,
    }

    if save is not None:
        save.write_text(json.dumps(signed_dict, indent=2), encoding="utf-8")

    if json_only:
        print(json.dumps(signed_dict, indent=2))
        return

    # Branded attestation panel
    att = signed.attestation
    result = att.get("execution_result", {})

    import darwin as _darwin

    panel = render_attestation_panel(
        workload_hash="sha256:" + att.get("workload_spec_hash", "?"),
        output_hash="sha256:" + result.get("output_hash", "?"),
        substrate=result.get("substrate_id", "local-docker-v0"),
        signer_key_id=att.get("signer_key_id", signed.public_key_b64[:16]),
        cost_usd=result.get("cost_usd", 0.0),
        cost_cap_usd=spec.cost_cap_usd,
        verified=True,
        attestation_id=att.get("attestation_id"),
        issued_at=att.get("issued_at"),
        public_key_b64=signed.public_key_b64,
        schema_full=att.get("schema", "darwin.cloud/agenticcloud/attestation/v0.1"),
        version=_darwin.__version__,
    )
    console.print(panel)

    # Echo workload stdout / stderr so users see their output
    if result.get("stdout"):
        console.print(result["stdout"], end="" if result["stdout"].endswith("\n") else "\n")
    if result.get("stderr"):
        err_console.print(result["stderr"], end="" if result["stderr"].endswith("\n") else "\n")


def _print_execution(signed_dict: dict, saved_to: Path | None) -> None:
    a = signed_dict["attestation"]
    er = a["execution_result"]

    status = er["status"]
    color = {
        "ok": "green",
        "error": "red",
        "timeout": "yellow",
        "oom": "yellow",
        "cost_exceeded": "red",
    }.get(status, "white")

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("status", f"[{color}]{status}[/{color}]")
    table.add_row("exit_code", str(er["exit_code"]))
    table.add_row("wall_time", f"{er['wall_time_sec']:.3f} s")
    table.add_row("cost", f"${er['cost_usd']:.8f}")
    table.add_row("substrate", er["substrate_id"])
    table.add_row("workload_id", er["workload_id"])
    table.add_row("attestation_id", a["attestation_id"])
    table.add_row("signer_key_id", a["signer_key_id"])
    table.add_row("output_hash", er["output_hash"][:16] + "…")

    console.print(Panel(table, title="darwin.agenticcloud execution", border_style=color))

    if er["stdout"]:
        console.print(Panel(er["stdout"].rstrip("\n"), title="stdout", border_style="dim"))
    if er["stderr"]:
        console.print(Panel(er["stderr"].rstrip("\n"), title="stderr", border_style="yellow"))

    if saved_to is not None:
        console.print(f"[dim]Signed attestation saved to[/dim] {saved_to}")


# -------------------------------------------------------------------
# v0.2 verbs: verify, price, list, sign, try, who
# -------------------------------------------------------------------


@app.command()
def verify(
    attestation_file: Annotated[
        Path,
        typer.Argument(
            help="Path to a v0.2 attestation JSON file to verify.",
        ),
    ],
    keylist: Annotated[
        str,
        typer.Option(
            "--keylist",
            help="Override the keylist URL (default: production darwin.cloud keylist).",
        ),
    ] = "https://darwin-agentic-cloud.fly.dev/.well-known/substrate-keys.json",
    json_only: Annotated[
        bool,
        typer.Option("--json", help="Print verification result as JSON."),
    ] = False,
) -> None:
    """Verify a v0.2 attestation cryptographically.

    Fetches the public keylist, verifies the substrate identity signature
    against the published class key, and re-renders the attestation panel
    with verification status.
    """
    import urllib.request

    from darwin.agenticcloud.hashing import canonical_json
    from darwin.agenticcloud.signing import verify_signature
    from darwin.agenticcloud.substrate.base import build_identity_payload
    from darwin.agenticcloud.ui import render_attestation_panel_auto

    if not attestation_file.exists():
        err_console.print(f"[red]File not found:[/red] {attestation_file}")
        raise typer.Exit(code=2)

    try:
        attestation = json.loads(attestation_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        err_console.print(f"[red]Invalid JSON:[/red] {e}")
        raise typer.Exit(code=2) from e

    # Fetch the keylist
    try:
        with urllib.request.urlopen(keylist, timeout=10) as resp:
            keylist_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        err_console.print(f"[red]Failed to fetch keylist {keylist}:[/red] {e}")
        raise typer.Exit(code=3) from e

    # Find the substrate identity key
    substrate_block = attestation.get("execution_result", {}).get("substrate", {})
    target_signer_key_id = substrate_block.get("identity_signer_key_id")
    if not target_signer_key_id:
        err_console.print("[red]No identity_signer_key_id in attestation[/red]")
        raise typer.Exit(code=4)

    matching_key = None
    for k in keylist_data.get("keys", []):
        if k.get("signer_key_id") == target_signer_key_id:
            matching_key = k
            break

    if matching_key is None:
        err_console.print(
            f"[red]Key {target_signer_key_id} not in keylist[/red] "
            f"(have {len(keylist_data.get('keys', []))} keys)"
        )
        raise typer.Exit(code=5)

    # Reconstruct the signed payload and verify
    identity_payload = build_identity_payload(
        substrate_id=substrate_block.get("id", ""),
        substrate_version=substrate_block.get("version", ""),
        workload_spec_hash=attestation.get("workload_spec_hash", ""),
        output_hash=attestation.get("execution_result", {}).get("output_hash", ""),
        evidence_schema_id=substrate_block.get("evidence_schema_id", ""),
        issued_at=attestation.get("issued_at", ""),
    )
    canonical = canonical_json(identity_payload)
    sig_b64 = substrate_block.get("identity_signature", "")
    pub_b64 = matching_key.get("public_key_b64", "")

    verified = verify_signature(canonical, sig_b64, pub_b64)

    result_dict = {
        "verified": verified,
        "signer_key_id": target_signer_key_id,
        "signer_status": matching_key.get("status", "unknown"),
        "keylist_url": keylist,
        "attestation_id": attestation.get("attestation_id"),
    }

    if json_only:
        print(json.dumps(result_dict, indent=2))
        return

    console.print()
    console.print(render_attestation_panel_auto(attestation))
    console.print()
    if verified:
        console.print(
            f"[bold green]✓ identity signature verified[/bold green] "
            f"against keylist key [{matching_key.get('status', '?')}]"
        )
    else:
        console.print(
            "[bold red]✗ identity signature FAILED to verify[/bold red] against published key"
        )
        raise typer.Exit(code=10)


@app.command()
def price(
    workload: Annotated[
        str,
        typer.Argument(
            help="Code string or path to a script file.",
        ),
    ],
    substrate: Annotated[
        str | None,
        typer.Option(
            "--substrate",
            "-s",
            help="Only price a specific substrate (short name or full ID).",
        ),
    ] = None,
    language: Annotated[
        str, typer.Option("--language", "-l", help="Workload language.")
    ] = "python",
    cost_cap: Annotated[
        float,
        typer.Option("--cost-cap", help="Cost ceiling in USD."),
    ] = 0.10,
    timeout: Annotated[int, typer.Option("--timeout", "-t", help="Timeout in seconds.")] = 30,
    memory: Annotated[int, typer.Option("--memory", "-m", help="Memory limit in MB.")] = 512,
    json_only: Annotated[
        bool,
        typer.Option("--json", help="Print prices as JSON."),
    ] = False,
) -> None:
    """Preflight-only — see what each substrate would cost without running.

    Asks every available substrate for a cost estimate. Returns a sorted
    list (cheapest first) so you can see your options before committing.
    """
    from darwin.agenticcloud.router import discover_substrates, resolve_short_name
    from darwin.agenticcloud.substrate.base import PreflightRejected

    candidate = Path(workload)
    if candidate.exists() and candidate.is_file():
        code = candidate.read_text(encoding="utf-8")
    else:
        code = workload

    spec = WorkloadSpec(
        code=code,
        language=language,
        cost_cap_usd=cost_cap,
        timeout_sec=timeout,
        memory_mb=memory,
    )

    substrates = discover_substrates()
    if not substrates:
        err_console.print(
            "[red]No substrates discoverable.[/red] "
            "Install Docker or set AWS_PROFILE / MODAL_TOKEN_ID."
        )
        raise typer.Exit(code=1)

    if substrate is not None:
        try:
            substrates = [resolve_short_name(substrate, substrates)]
        except Exception as e:
            err_console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=2) from e

    quotes = []
    for sub in substrates:
        try:
            est = sub.preflight(spec)
            quotes.append(
                {
                    "substrate_id": sub.substrate_id,
                    "cost_usd_max": float(est.cost_usd_max),
                    "status": "ok",
                }
            )
        except PreflightRejected as e:
            quotes.append(
                {
                    "substrate_id": sub.substrate_id,
                    "cost_usd_max": None,
                    "status": "rejected",
                    "reason": str(e),
                }
            )
        except Exception as e:
            quotes.append(
                {
                    "substrate_id": sub.substrate_id,
                    "cost_usd_max": None,
                    "status": "error",
                    "reason": f"{type(e).__name__}: {e}",
                }
            )

    quotes.sort(key=lambda q: (q["cost_usd_max"] is None, q["cost_usd_max"] or 0.0))

    if json_only:
        print(json.dumps(quotes, indent=2))
        return

    table = Table(title="darwin.agenticcloud · price quotes", show_lines=False)
    table.add_column("substrate", style="bold")
    table.add_column("cost_usd_max", justify="right")
    table.add_column("status")
    for q in quotes:
        cost_str = f"${q['cost_usd_max']:.6f}" if q["cost_usd_max"] is not None else "—"
        status_color = (
            "green" if q["status"] == "ok" else "yellow" if q["status"] == "rejected" else "red"
        )
        table.add_row(
            q["substrate_id"],
            cost_str,
            f"[{status_color}]{q['status']}[/{status_color}]",
        )
    console.print()
    console.print(table)
    console.print()


@app.command("list")
def list_substrates(
    json_only: Annotated[
        bool,
        typer.Option("--json", help="Print as JSON."),
    ] = False,
) -> None:
    """Show every substrate this environment can use right now.

    Auto-discovery checks for credentials, environment variables, and
    daemon availability (Docker). Substrates that fail any check are
    omitted with a reason.
    """
    from darwin.agenticcloud.router import discover_substrates

    discovered = discover_substrates()

    # Hard-coded UX metadata (cold start, region, type) since the substrate
    # base class doesn't expose these as a public interface yet.
    meta = {
        "local-docker-v0": ("local", "~2s", "no cost"),
        "aws-lambda-us-east-1": ("aws-east1", "~500ms", "~$0.0001/job"),
        "aws-lambda-us-west-2": ("aws-west2", "~500ms", "~$0.0001/job"),
        "aws-lambda-eu-west-1": ("aws-eu", "~500ms", "~$0.0001/job"),
        "aws-lambda-ap-northeast-1": ("aws-tokyo", "~500ms", "~$0.0001/job"),
        "modal-v0": ("modal", "~1s", "~$0.0001/job"),
        "aws-batch-ec2-spot-v0-us-east-1": (
            "aws-east1",
            "~3min cold",
            "$0.001-0.10/job",
        ),
    }

    rows = []
    for sub in discovered:
        m = meta.get(sub.substrate_id, ("?", "?", "?"))
        rows.append(
            {
                "substrate_id": sub.substrate_id,
                "region": m[0],
                "cold_start": m[1],
                "cost_band": m[2],
            }
        )

    if json_only:
        print(json.dumps(rows, indent=2))
        return

    table = Table(title="darwin.agenticcloud · available substrates")
    table.add_column("substrate", style="bold")
    table.add_column("region")
    table.add_column("cold start")
    table.add_column("cost band")
    for r in rows:
        table.add_row(r["substrate_id"], r["region"], r["cold_start"], r["cost_band"])
    console.print()
    console.print(table)
    console.print(f"\n[dim]{len(rows)} substrate(s) discovered[/dim]")


@app.command()
def sign(
    substrate_id: Annotated[
        str,
        typer.Argument(
            help="Substrate ID to mint a class signing key for "
            "(e.g. 'aws-batch-ec2-spot-v0-us-east-1').",
        ),
    ],
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="Output path for the PEM file (default: class-keys/{id}.pem).",
        ),
    ] = None,
) -> None:
    """Generate a class signing key for a substrate.

    The private key is written to disk; the public key id is printed.
    Upload the PEM contents as a secret to your hosted signer, then
    publish the public key in the substrate keylist.
    """
    from darwin.agenticcloud.admin_cli import _cmd_generate

    _cmd_generate(substrate_id=substrate_id, out_path=out)


@app.command(name="try")
def try_workload(
    workload: Annotated[
        str,
        typer.Argument(
            help="Code string or path to a script file.",
        ),
    ],
    language: Annotated[
        str, typer.Option("--language", "-l", help="Workload language.")
    ] = "python",
    cost_cap: Annotated[
        float,
        typer.Option("--cost-cap", help="Cost ceiling in USD."),
    ] = 0.10,
    timeout: Annotated[int, typer.Option("--timeout", "-t", help="Timeout in seconds.")] = 30,
    memory: Annotated[int, typer.Option("--memory", "-m", help="Memory limit in MB.")] = 512,
    save: Annotated[
        Path | None,
        typer.Option("--save", help="Write the signed attestation to this path."),
    ] = None,
    json_only: Annotated[
        bool,
        typer.Option("--json", help="Print only the signed attestation JSON."),
    ] = False,
) -> None:
    """Run on the safest local substrate (local-docker) — never escalates to cloud.

    Use when you want to test a workload without paying any cloud cost.
    Same output shape as `darwin run`; just locked to local-docker-v0.
    """
    # Delegate to `run` with substrate forced to local-docker-v0.
    run(
        workload=workload,
        substrate="local-docker-v0",
        language=language,
        cost_cap=cost_cap,
        timeout=timeout,
        memory=memory,
        save=save,
        json_only=json_only,
    )


@app.command()
def who(
    attestation_file: Annotated[
        Path,
        typer.Argument(
            help="Path to a v0.2 attestation JSON file.",
        ),
    ],
    keylist: Annotated[
        str,
        typer.Option(
            "--keylist",
            help="Keylist URL (default: production darwin.cloud keylist).",
        ),
    ] = "https://darwin-agentic-cloud.fly.dev/.well-known/substrate-keys.json",
    json_only: Annotated[
        bool,
        typer.Option("--json", help="Print as JSON."),
    ] = False,
) -> None:
    """Show whose keys signed an attestation.

    Lighter-weight than `verify` — does not run cryptographic verification.
    Just looks up the substrate identity signer and outer signer in the
    keylist and reports their status.
    """
    import urllib.request

    if not attestation_file.exists():
        err_console.print(f"[red]File not found:[/red] {attestation_file}")
        raise typer.Exit(code=2)

    try:
        attestation = json.loads(attestation_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        err_console.print(f"[red]Invalid JSON:[/red] {e}")
        raise typer.Exit(code=2) from e

    try:
        with urllib.request.urlopen(keylist, timeout=10) as resp:
            keylist_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        err_console.print(f"[red]Failed to fetch keylist {keylist}:[/red] {e}")
        raise typer.Exit(code=3) from e

    substrate_block = attestation.get("execution_result", {}).get("substrate", {})
    sub_signer_id = substrate_block.get("identity_signer_key_id", "?")
    outer_signer_id = attestation.get("signer_key_id", "?")

    keys = {k.get("signer_key_id"): k for k in keylist_data.get("keys", [])}
    sub_key = keys.get(sub_signer_id)

    output = {
        "attestation_id": attestation.get("attestation_id"),
        "substrate_signer": {
            "signer_key_id": sub_signer_id,
            "in_keylist": sub_key is not None,
            "status": sub_key.get("status") if sub_key else "unknown",
        },
        "outer_signer": {
            "signer_key_id": outer_signer_id,
            "note": "operator-local key; not anchored to public keylist by design",
        },
    }

    if json_only:
        print(json.dumps(output, indent=2))
        return

    console.print()
    console.print(f"[bold]attestation[/bold]  {output['attestation_id']}")
    console.print(
        f"[bold]substrate signer[/bold]  {sub_signer_id}  "
        f"[dim]({'in keylist' if sub_key else 'NOT in keylist'}, "
        f"status={output['substrate_signer']['status']})[/dim]"
    )
    console.print(
        f"[bold]outer signer[/bold]      {outer_signer_id}  "
        f"[dim](operator-local; not in public keylist by design)[/dim]"
    )
    console.print()


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Bind port.")] = 8787,
    reload: Annotated[
        bool, typer.Option("--reload", help="Reload on file changes (dev only).")
    ] = False,
    quiet: Annotated[bool, typer.Option("--quiet", help="Suppress branded boot banner.")] = False,
) -> None:
    """Run the darwin.agenticcloud HTTP server."""
    import uvicorn

    if not quiet:
        import darwin
        from darwin.agenticcloud import ATTESTATION_SCHEMA
        from darwin.agenticcloud.signing import Signer
        from darwin.agenticcloud.ui import BootStep, matrix_boot, print_banner

        signer = Signer()
        matrix_boot(
            console,
            [
                BootStep("initializing darwin.agenticcloud runtime"),
                BootStep("loading ed25519 signing key"),
                BootStep(f"verifying schema {ATTESTATION_SCHEMA}"),
                BootStep("opening sqlite at ~/.darwin/agenticcloud/attestations.db"),
                BootStep("preparing docker sandbox (lazy)"),
                BootStep(f"binding {host}:{port}"),
            ],
        )
        print_banner(
            console,
            version=darwin.__version__,
            schema=ATTESTATION_SCHEMA,
            signer_key_id=signer.key_id(),
            substrate_id="local-docker-v0",
        )
        console.print(f"  [bold #00ff01]READY[/bold #00ff01]  http://{host}:{port}/docs")
        console.print("  [dim]press ctrl-c to terminate[/dim]\n")

    uvicorn.run(
        "darwin.agenticcloud.server:app", host=host, port=port, reload=reload, log_level="info"
    )


@app.command()
def version() -> None:
    """Print the Darwin version and banner."""
    import darwin
    from darwin.agenticcloud import ATTESTATION_SCHEMA
    from darwin.agenticcloud.ui import print_banner

    print_banner(
        console,
        version=darwin.__version__,
        schema=ATTESTATION_SCHEMA,
    )


# -------------------------------------------------------------------
# keys
# -------------------------------------------------------------------
@keys_app.command("show")
def keys_show() -> None:
    """Show the current signing key identity."""
    import darwin
    from darwin.agenticcloud import ATTESTATION_SCHEMA
    from darwin.agenticcloud.ui import print_banner

    signer = Signer()
    print_banner(
        console,
        version=darwin.__version__,
        schema=ATTESTATION_SCHEMA,
        signer_key_id=signer.key_id(),
        substrate_id="local-docker-v0",
    )

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("key_id", signer.key_id())
    table.add_row("public_key_b64", signer.public_key_b64())
    table.add_row("key_path", str(signer.key_path))
    console.print(Panel(table, title="darwin.agenticcloud signing key", border_style="#00ff01"))


@keys_app.command("init")
def keys_init() -> None:
    """Initialize the signing keypair (no-op if one already exists)."""
    signer = Signer()
    console.print(f"[green]Key ready:[/green] {signer.key_id()}")
    console.print(f"[dim]Path:[/dim] {signer.key_path}")


# -------------------------------------------------------------------
# attest
# -------------------------------------------------------------------
@attest_app.command("verify")
def attest_verify(
    file: Annotated[Path, typer.Argument(help="Path to a signed attestation JSON file.")],
) -> None:
    """Verify a signed attestation."""
    if not file.exists():
        err_console.print(f"[red]File not found:[/red] {file}")
        raise typer.Exit(code=2)

    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        err_console.print(f"[red]Invalid JSON:[/red] {e}")
        raise typer.Exit(code=2) from e

    ok = verify_attestation(data)
    if ok:
        a = data.get("attestation", {})
        console.print("[green]✓ verified[/green]")
        console.print(f"  attestation_id: {a.get('attestation_id', '?')}")
        console.print(f"  signer_key_id:  {a.get('signer_key_id', '?')}")
        console.print(f"  schema:         {a.get('schema', '?')}")
        raise typer.Exit(code=0)
    else:
        console.print("[red]✗ verification failed[/red]")
        raise typer.Exit(code=1)


@attest_app.command("show")
def attest_show(
    file: Annotated[Path, typer.Argument(help="Path to a signed attestation JSON file.")],
) -> None:
    """Pretty-print a signed attestation."""
    if not file.exists():
        err_console.print(f"[red]File not found:[/red] {file}")
        raise typer.Exit(code=2)
    data = json.loads(file.read_text(encoding="utf-8"))
    print(json.dumps(data, indent=2))


# -------------------------------------------------------------------
# history
# -------------------------------------------------------------------
@history_app.command("list")
def history_list(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max rows to return.")] = 20,
    status: Annotated[str | None, typer.Option("--status", help="Filter by status.")] = None,
) -> None:
    """List recent attestations."""
    from darwin.agenticcloud.storage import AttestationStore

    store = AttestationStore()
    rows = store.list_by_status(status, limit=limit) if status else store.list_recent(limit=limit)

    if not rows:
        console.print("[dim]No attestations stored yet.[/dim]")
        return

    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("issued_at", style="dim")
    table.add_column("status")
    table.add_column("workload_id")
    table.add_column("cost", justify="right")
    table.add_column("wall_time", justify="right")
    table.add_column("substrate")
    table.add_column("id (short)", style="dim")

    from datetime import datetime

    for r in rows:
        color = {"ok": "green", "error": "red", "timeout": "yellow", "cost_exceeded": "red"}.get(
            r.status, "white"
        )
        ts = datetime.fromtimestamp(r.issued_at, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
        table.add_row(
            ts,
            f"[{color}]{r.status}[/{color}]",
            r.workload_id,
            f"${r.cost_usd:.8f}",
            f"{r.wall_time_sec:.3f}s",
            r.substrate_id,
            r.attestation_id[:8],
        )

    console.print(table)


@history_app.command("stats")
def history_stats() -> None:
    """Show aggregate stats across stored attestations."""
    from darwin.agenticcloud.storage import AttestationStore

    store = AttestationStore()
    total_count = store.count()
    total_cost = store.total_cost_usd()

    ok_count = len(store.list_by_status("ok", limit=10**9))
    err_count = len(store.list_by_status("error", limit=10**9))
    timeout_count = len(store.list_by_status("timeout", limit=10**9))
    rejected_count = len(store.list_by_status("cost_exceeded", limit=10**9))

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("total executions", str(total_count))
    table.add_row("total cost", f"${total_cost:.8f}")
    table.add_row("status: ok", str(ok_count))
    table.add_row("status: error", str(err_count))
    table.add_row("status: timeout", str(timeout_count))
    table.add_row("status: cost_exceeded", str(rejected_count))

    console.print(
        Panel(table, title="darwin.agenticcloud attestation history", border_style="cyan")
    )


@history_app.command("show")
def history_show(
    attestation_id: Annotated[str, typer.Argument(help="Attestation ID (full or first 8 chars).")],
) -> None:
    """Show the full signed attestation for a given ID."""
    from darwin.agenticcloud.storage import AttestationStore

    store = AttestationStore()

    if len(attestation_id) < 36:
        candidates = [
            a for a in store.list_recent(limit=10**9) if a.attestation_id.startswith(attestation_id)
        ]
        if not candidates:
            err_console.print(f"[red]No attestation matching prefix:[/red] {attestation_id}")
            raise typer.Exit(code=2)
        if len(candidates) > 1:
            err_console.print(
                f"[red]Ambiguous prefix:[/red] {attestation_id} matches {len(candidates)} attestations"
            )
            raise typer.Exit(code=2)
        attestation_id = candidates[0].attestation_id

    fetched = store.get(attestation_id)
    if fetched is None:
        err_console.print(f"[red]Not found:[/red] {attestation_id}")
        raise typer.Exit(code=2)

    print(json.dumps(fetched.signed_attestation, indent=2))


# -------------------------------------------------------------------
# mcp
# -------------------------------------------------------------------
@mcp_app.command("serve")
def mcp_serve() -> None:
    """Run the darwin.agenticcloud MCP server on stdio.

    Intended to be spawned by an MCP client (Claude Desktop, Cursor, etc.)
    over stdio. Do not run this manually unless you're piping JSON-RPC
    into it.
    """
    from darwin.agenticcloud.mcp_server import run as run_mcp

    run_mcp()


if __name__ == "__main__":
    app()


@mcp_app.command("install")
def mcp_install(
    client: Annotated[
        str, typer.Option("--client", help="MCP client: 'claude-desktop' or 'cursor'.")
    ] = "claude-desktop",
    name: Annotated[
        str, typer.Option("--name", help="Server entry name in the config.")
    ] = "darwin",
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite an existing entry without prompting.")
    ] = False,
) -> None:
    """Install darwin.agenticcloud as an MCP server in a supported client.

    Detects the client's config file, adds an entry that spawns
    `python -m darwin.agenticcloud.mcp_server` using the current
    Python interpreter, and writes the config back. Idempotent.
    """
    import os
    import platform
    import sys
    from pathlib import Path

    home = Path.home()
    system = platform.system()

    if client == "claude-desktop":
        if system == "Darwin":
            config_path = (
                home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
            )
        elif system == "Windows":
            appdata = os.environ.get("APPDATA")
            if not appdata:
                err_console.print("[red]APPDATA env var not set; can't locate Claude config.[/red]")
                raise typer.Exit(code=2)
            config_path = Path(appdata) / "Claude" / "claude_desktop_config.json"
        elif system == "Linux":
            config_path = home / ".config" / "Claude" / "claude_desktop_config.json"
        else:
            err_console.print(f"[red]Unsupported OS for claude-desktop:[/red] {system}")
            raise typer.Exit(code=2)
    elif client == "cursor":
        config_path = home / ".cursor" / "mcp.json"
    else:
        err_console.print(f"[red]Unknown client:[/red] {client}")
        raise typer.Exit(code=2)

    # Load existing config (or create empty)
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            err_console.print(f"[red]Config file exists but is invalid JSON:[/red] {e}")
            raise typer.Exit(code=2) from e
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config = {}

    config.setdefault("mcpServers", {})

    if name in config["mcpServers"] and not force:
        existing = config["mcpServers"][name]
        if existing.get("command") == sys.executable and existing.get("args") == [
            "-m",
            "darwin.agenticcloud.mcp_server",
        ]:
            console.print(f"[green]✓ {name} already installed in {client} (no changes).[/green]")
            console.print(f"  config: {config_path}")
            console.print(f"  python: {sys.executable}")
            raise typer.Exit(code=0)
        else:
            err_console.print(f"[yellow]Entry '{name}' already exists in {config_path}:[/yellow]")
            err_console.print(f"  {json.dumps(existing, indent=2)}")
            err_console.print("Use --force to overwrite, or pick a different --name.")
            raise typer.Exit(code=1)

    config["mcpServers"][name] = {
        "command": sys.executable,
        "args": ["-m", "darwin.agenticcloud.mcp_server"],
    }

    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    # Branded configuration receipt
    import darwin
    from darwin.agenticcloud import ATTESTATION_SCHEMA
    from darwin.agenticcloud.ui import print_banner, render_mcp_install_receipt

    print_banner(
        console,
        version=darwin.__version__,
        schema=ATTESTATION_SCHEMA,
    )

    receipt = render_mcp_install_receipt(
        config_path=str(config_path),
        server_name=name,
        python_interpreter=sys.executable,
        tool_names=[
            "dac_run_python",
            "dac_run_node",
            "dac_verify_attestation",
            "dac_identity",
            "dac_history_recent",
            "dac_history_stats",
            "dac_history_get",
        ],
    )
    console.print(receipt)


@mcp_app.command("uninstall")
def mcp_uninstall(
    client: Annotated[
        str, typer.Option("--client", help="MCP client: 'claude-desktop' or 'cursor'.")
    ] = "claude-desktop",
    name: Annotated[str, typer.Option("--name", help="Server entry name to remove.")] = "darwin",
) -> None:
    """Remove an MCP server entry from the client config."""
    import os
    import platform
    from pathlib import Path

    home = Path.home()
    system = platform.system()

    if client == "claude-desktop":
        if system == "Darwin":
            config_path = (
                home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
            )
        elif system == "Windows":
            appdata = os.environ.get("APPDATA")
            if not appdata:
                err_console.print("[red]APPDATA env var not set.[/red]")
                raise typer.Exit(code=2)
            config_path = Path(appdata) / "Claude" / "claude_desktop_config.json"
        elif system == "Linux":
            config_path = home / ".config" / "Claude" / "claude_desktop_config.json"
        else:
            err_console.print(f"[red]Unsupported OS for claude-desktop:[/red] {system}")
            raise typer.Exit(code=2)
    elif client == "cursor":
        config_path = home / ".cursor" / "mcp.json"
    else:
        err_console.print(f"[red]Unknown client:[/red] {client}")
        raise typer.Exit(code=2)

    if not config_path.exists():
        console.print(f"[dim]No config file at {config_path} (nothing to remove).[/dim]")
        raise typer.Exit(code=0)

    config = json.loads(config_path.read_text(encoding="utf-8"))
    if "mcpServers" not in config or name not in config["mcpServers"]:
        console.print(f"[dim]Entry '{name}' not found in {config_path} (nothing to remove).[/dim]")
        raise typer.Exit(code=0)

    del config["mcpServers"][name]
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    console.print(f"[green]✓ Removed MCP server '{name}' from {client}.[/green]")
    console.print(f"  config: {config_path}")


# ----------------------------------------------------------------------
# substrates demo (Phase 2 — show me the v0.2 attestation)
# ----------------------------------------------------------------------


@substrates_app.command("demo")
def substrates_demo(
    substrate_id: Annotated[
        str,
        typer.Argument(
            help="Substrate to demo: short name (local, aws-batch, aws-lambda, modal) "
            "or full id (e.g. 'aws-lambda-us-east-1').",
        ),
    ],
    code: Annotated[
        str,
        typer.Option("--code", "-c", help="Workload code to run."),
    ] = "print('Hello, agent.')",
    language: Annotated[
        str,
        typer.Option("--language", "-l", help="Workload language."),
    ] = "python",
    timeout_sec: Annotated[
        int,
        typer.Option("--timeout", help="Workload timeout in seconds."),
    ] = 30,
    memory_mb: Annotated[
        int,
        typer.Option("--memory", help="Workload memory in MB."),
    ] = 512,
    cost_cap_usd: Annotated[
        float,
        typer.Option("--cost-cap", help="Cost cap in USD."),
    ] = 0.10,
    mock: Annotated[
        bool,
        typer.Option(
            "--mock",
            help="Build a synthetic attestation without touching any substrate "
            "(for offline panel testing).",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit raw JSON instead of the branded panel."),
    ] = False,
) -> None:
    """Run a workload on a substrate and emit a real v0.2 attestation.

    Default: actually executes the workload via the named substrate.
    Use --mock to build a synthetic attestation (useful for offline
    rendering tests or when AWS/Docker are unavailable).
    """
    if mock:
        _substrates_demo_mock(
            substrate_id=substrate_id,
            code=code,
            language=language,
            timeout_sec=timeout_sec,
            memory_mb=memory_mb,
            cost_cap_usd=cost_cap_usd,
            as_json=as_json,
        )
        return

    # Real path — call into darwin.run() with the substrate override.
    from darwin import run as _darwin_run

    try:
        attestation = _darwin_run(
            code,
            substrate=substrate_id,
            language=language,
            cost_cap=cost_cap_usd,
            timeout=timeout_sec,
            memory_mb=memory_mb,
        )
    except Exception as e:
        err_console.print(f"[red]Demo failed:[/red] {type(e).__name__}: {e}")
        raise typer.Exit(code=1) from e

    if as_json:
        print(json.dumps(attestation, indent=2, sort_keys=True))
        return

    from darwin.agenticcloud.ui import render_attestation_panel_auto

    chosen = attestation.get("execution_result", {}).get("substrate", {}).get("id", "?")
    console.print()
    console.print(
        f"[dim]demo ·[/dim] [bold]{chosen}[/bold]   [dim](real substrate execution)[/dim]"
    )
    console.print()
    console.print(render_attestation_panel_auto(attestation))
    console.print()
    console.print(f"[dim]raw json:[/dim] [bold]darwin substrates demo {substrate_id} --json[/bold]")


def _substrates_demo_mock(
    substrate_id: str,
    code: str,
    language: str,
    timeout_sec: int,
    memory_mb: int,
    cost_cap_usd: float,
    as_json: bool,
) -> None:
    """Synthetic v0.2 attestation — no substrate execution, no network."""
    import os
    from dataclasses import asdict

    # Force operator-fallback signing (no network).
    os.environ["DARWIN_SIGNER_URL"] = ""

    from darwin.agenticcloud.hashing import content_hash, sha256_hex
    from darwin.agenticcloud.substrate.base import (
        RunResult,
        build_attestation_dict,
        iso8601_now,
        sign_identity,
    )
    from darwin.agenticcloud.substrate.identity import OperatorFallbackSigner
    from darwin.agenticcloud.types import WorkloadSpec

    spec = WorkloadSpec(
        code=code,
        language=language,
        timeout_sec=timeout_sec,
        memory_mb=memory_mb,
        cost_cap_usd=cost_cap_usd,
    )
    signer = OperatorFallbackSigner()
    fake_stdout = "Hello, agent.\n"
    output_hash = sha256_hex(fake_stdout.encode("utf-8"))
    stderr_hash = sha256_hex(b"")

    if substrate_id in ("local", "local-docker-v0"):
        from darwin.agenticcloud.substrate.local_docker import (
            EVIDENCE_SCHEMA_ID,
            SUBSTRATE_VERSION,
        )

        evidence = {
            "container_status": "ok",
            "exit_code": 0,
            "stdout_hash": output_hash,
            "stderr_hash": stderr_hash,
            "wall_time_sec": 0.42,
        }
        substrate_version = SUBSTRATE_VERSION
        evidence_schema_id = EVIDENCE_SCHEMA_ID
        cost_usd = 0.000042
        substrate_id_full = "local-docker-v0"
    elif substrate_id.startswith("aws-lambda") or substrate_id == "lambda":
        from darwin.agenticcloud.substrate.aws_lambda import (
            EVIDENCE_SCHEMA_ID,
            SUBSTRATE_VERSION,
            LambdaPricingClient,
        )

        region = (
            substrate_id.removeprefix("aws-lambda-")
            if substrate_id.startswith("aws-lambda-")
            else "us-east-1"
        )
        pricing = LambdaPricingClient()
        try:
            price = pricing.get(region)
        except Exception as e:
            err_console.print(f"[red]Unknown region:[/red] {e}")
            raise typer.Exit(code=1) from e
        billed_duration_ms = 423
        cost_usd = price.cost_for(
            memory_mb=memory_mb,
            billed_duration_ms=billed_duration_ms,
        )
        evidence = {
            "request_id": "req-demo-deadbeef",
            "log_group": f"/aws/lambda/darwin-runner-{language}-{region}",
            "log_stream": "2026/05/25/[$LATEST]demo-stream-id",
            "lambda_version": "$LATEST",
            "region": region,
            "billed_duration_ms": billed_duration_ms,
            "memory_size_mb": memory_mb,
            "max_memory_used_mb": 87,
            "container_status": "ok",
            "exit_code": 0,
            "stdout_hash": output_hash,
            "stderr_hash": stderr_hash,
            "wall_time_sec": 0.42,
        }
        substrate_version = SUBSTRATE_VERSION
        evidence_schema_id = EVIDENCE_SCHEMA_ID
        substrate_id_full = f"aws-lambda-{region}"
    else:
        err_console.print(
            f"[red]Mock not supported for substrate:[/red] {substrate_id!r}\n"
            "Available mock substrates: local, aws-lambda-{region}"
        )
        raise typer.Exit(code=1)

    result = RunResult(
        substrate_id=substrate_id_full,
        substrate_version=substrate_version,
        workload_spec_hash=content_hash(asdict(spec)),
        stdout=fake_stdout,
        stderr="",
        output_hash=output_hash,
        cost_usd=cost_usd,
        evidence_schema_id=evidence_schema_id,
        evidence=evidence,
        extensions={},
        tee_required=False,
        issued_at=iso8601_now(),
    )
    identity = sign_identity(result=result, signer=signer)
    attestation = build_attestation_dict(
        attestation_id=f"att_demo_{substrate_id_full}",
        result=result,
        identity=identity,
    )

    from darwin.agenticcloud.hashing import canonical_json

    outer_sig = signer._signer.sign(canonical_json(attestation))
    envelope = {
        **attestation,
        "signer_key_id": signer.signer_key_id,
        "signature": outer_sig,
    }

    if as_json:
        print(json.dumps(envelope, indent=2, sort_keys=True))
        return

    from darwin.agenticcloud.ui import render_attestation_panel_auto

    console.print()
    console.print(
        f"[dim]demo ·[/dim] [bold]{substrate_id_full}[/bold]   "
        f"[dim](mocked — no real execution)[/dim]"
    )
    console.print()
    console.print(render_attestation_panel_auto(envelope))
    console.print()
    console.print(
        f"[dim]for real execution:[/dim] [bold]darwin substrates demo {substrate_id} [/bold]"
    )
