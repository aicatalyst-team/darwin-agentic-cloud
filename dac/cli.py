"""DAC command-line interface.

Examples:
    dac run hello.py
    dac run hello.py --timeout 30 --memory 256
    dac keys show
    dac attest verify ./attestation.json
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from dac.attestation import verify_attestation
from dac.runtime import Runtime
from dac.signing import Signer
from dac.types import WorkloadSpec

app = typer.Typer(
    name="dac",
    help="Darwinic Agentic Cloud — verifiable compute for AI agents.",
    no_args_is_help=True,
    add_completion=False,
)
keys_app = typer.Typer(help="Manage signing keys.", no_args_is_help=True)
attest_app = typer.Typer(help="Work with attestations.", no_args_is_help=True)
app.add_typer(keys_app, name="keys")
app.add_typer(attest_app, name="attest")

console = Console()
err_console = Console(stderr=True)


@app.command()
def run(
    file: Annotated[Path, typer.Argument(help="Path to the script to run.")],
    language: Annotated[str, typer.Option("--language", "-l", help="Language (python or node).")] = "python",
    timeout: Annotated[int, typer.Option("--timeout", "-t", help="Timeout in seconds.")] = 30,
    memory: Annotated[int, typer.Option("--memory", "-m", help="Memory limit in MB.")] = 512,
    cost_cap: Annotated[float, typer.Option("--cost-cap", help="Cost ceiling in USD.")] = 0.01,
    save: Annotated[Path | None, typer.Option("--save", help="Write the signed attestation to this path.")] = None,
    json_only: Annotated[bool, typer.Option("--json", help="Print only the signed attestation JSON.")] = False,
) -> None:
    """Execute a script in the DAC sandbox and emit a signed attestation."""
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

    runtime = Runtime()
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

    _print_execution(signed_dict, save)


def _print_execution(signed_dict: dict, saved_to: Path | None) -> None:
    a = signed_dict["attestation"]
    er = a["execution_result"]

    status = er["status"]
    color = {"ok": "green", "error": "red", "timeout": "yellow", "oom": "yellow"}.get(status, "white")

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

    console.print(Panel(table, title="DAC execution", border_style=color))

    if er["stdout"]:
        console.print(Panel(er["stdout"].rstrip("\n"), title="stdout", border_style="dim"))
    if er["stderr"]:
        console.print(Panel(er["stderr"].rstrip("\n"), title="stderr", border_style="yellow"))

    if saved_to is not None:
        console.print(f"[dim]Signed attestation saved to[/dim] {saved_to}")


# -------------------------------------------------------------------
# keys
# -------------------------------------------------------------------
@keys_app.command("show")
def keys_show() -> None:
    """Show the current signing key identity."""
    signer = Signer()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("key_id", signer.key_id())
    table.add_row("public_key_b64", signer.public_key_b64())
    table.add_row("key_path", str(signer.key_path))
    console.print(Panel(table, title="DAC signing key", border_style="cyan"))


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


@app.command()
def version() -> None:
    """Print the DAC version."""
    import dac
    print(dac.__version__)


if __name__ == "__main__":
    app()
