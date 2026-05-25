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
from datetime import UTC
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

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
keys_app = typer.Typer(help="Manage signing keys.", no_args_is_help=True)
attest_app = typer.Typer(help="Work with attestations.", no_args_is_help=True)
history_app = typer.Typer(help="Query attestation history.", no_args_is_help=True)
mcp_app = typer.Typer(help="Model Context Protocol (MCP) server.", no_args_is_help=True)

app.add_typer(keys_app, name="keys")
app.add_typer(attest_app, name="attest")
app.add_typer(history_app, name="history")
app.add_typer(mcp_app, name="mcp")

console = Console()
err_console = Console(stderr=True)


# -------------------------------------------------------------------
# Top-level commands
# -------------------------------------------------------------------
@app.command()
def run(
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


@app.command()
def serve(
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Bind port.")] = 8787,
    reload: Annotated[
        bool, typer.Option("--reload", help="Reload on file changes (dev only).")
    ] = False,
) -> None:
    """Run the darwin.agenticcloud HTTP server."""
    import uvicorn

    uvicorn.run(
        "darwin.agenticcloud.server:app", host=host, port=port, reload=reload, log_level="info"
    )


@app.command()
def version() -> None:
    """Print the Darwin version."""
    import darwin

    print(darwin.__version__)


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
    console.print(Panel(table, title="darwin.agenticcloud signing key", border_style="cyan"))


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

    console.print(
        f"[green]✓ Installed darwin.agenticcloud as MCP server '{name}' in {client}.[/green]"
    )
    console.print(f"  config:  {config_path}")
    console.print(f"  command: {sys.executable}")
    console.print("  args:    -m darwin.agenticcloud.mcp_server")
    console.print()
    console.print("[dim]Restart your MCP client to pick up the change.[/dim]")


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
