# Quickstart

Get Darwin Agentic Cloud running locally and have an AI agent calling
it in under five minutes.

## What you'll have at the end

- `darwin` CLI on your machine
- A local sandbox that runs code in Docker and signs the result
- Claude Desktop (or any MCP client) able to call Darwin as a tool
- Every execution stored in a queryable local audit trail

## Prerequisites

- **macOS, Linux, or Windows**
- **Python 3.10+** (we test on 3.12)
- **Docker Desktop** or any Docker daemon — needed for the sandbox
- **[uv](https://docs.astral.sh/uv/)** — fastest way to manage the Python env

Install uv if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## 1. Clone and install (60 seconds)

```bash
git clone https://github.com/vje013/darwin-agentic-cloud.git
cd darwin-agentic-cloud
uv sync --extra dev --extra test
```

> **macOS users:** if your shell is currently in a conda environment (`(base)` in your prompt), run `conda deactivate` first. Conda's Python interferes with uv-managed venvs.

## 2. Verify the install (10 seconds)

```bash
.venv/bin/darwin version
.venv/bin/darwin keys show
```

You should see `0.1.0` and your auto-generated Ed25519 signing key.
The key lives at `~/.darwin/agenticcloud/keys/signing.pem` and is the
identity that signs every attestation you produce.

## 3. Run signed code (30 seconds)

```bash
echo 'print("hello from darwin")' > /tmp/hello.py
.venv/bin/darwin run /tmp/hello.py
```

You'll see a panel showing the execution result (status, exit code,
wall time, cost) plus the stdout. Every field is bound into a
cryptographic signature. To see the full signed attestation:

```bash
.venv/bin/darwin run /tmp/hello.py --json
```

## 4. Verify the signature (10 seconds)

```bash
.venv/bin/darwin run /tmp/hello.py --save /tmp/att.json
.venv/bin/darwin attest verify /tmp/att.json
```

The verification will pass. To prove tamper-evidence works, try
editing the JSON file (change a single character in the stdout)
and verify again — it will fail.

## 5. Query your history (10 seconds)

```bash
.venv/bin/darwin history list
.venv/bin/darwin history stats
```

Every execution is persisted to SQLite at
`~/.darwin/agenticcloud/attestations.db`. You can query it with
the CLI, the HTTP API, or directly with the `sqlite3` command.

## 6. Connect Claude Desktop (60 seconds)

```bash
.venv/bin/darwin mcp install
```

That single command writes the Claude Desktop MCP config for you.
**Restart Claude Desktop** (Cmd+Q on macOS, then reopen), and in any
chat ask:

> Use dac_run_python to execute `print(2 + 2)` and tell me the
> substrate ID and signer key ID from the attestation.

Claude will call Darwin, get back a signed attestation, and report
the details. That's the full agentic loop: an AI agent calling
your verifiable substrate, getting tamper-evident receipts back.

### macOS-specific: venv outside ~/Documents/

Claude Desktop's renderer runs in macOS Seatbelt sandbox and **cannot
read inside `~/Documents/`**. If you cloned the repo into Documents,
move the venv outside before running `darwin mcp install`:

```bash
# from the project root
rm -rf .venv
mkdir -p ~/.local/share/darwin-agentic-cloud
uv venv ~/.local/share/darwin-agentic-cloud/.venv --python 3.12
ln -s ~/.local/share/darwin-agentic-cloud/.venv .venv
uv pip install . --python ~/.local/share/darwin-agentic-cloud/.venv/bin/python
.venv/bin/darwin mcp install --force
```

Then restart Claude Desktop.

## 7. (Optional) Run the HTTP server

If you want to call Darwin from anywhere instead of just locally:

```bash
.venv/bin/darwin serve
# in another terminal:
curl -s http://127.0.0.1:8787/healthz
curl -s http://127.0.0.1:8787/v0/identity | python3 -m json.tool
```

Interactive docs at `http://127.0.0.1:8787/docs`.

## What you just did

You ran code in an isolated Docker sandbox with cryptographic proof
that no one — including yourself, including the AI agent that called
you — can tamper with the result. You also exposed this primitive as
a tool an AI agent can call directly. That's the Darwin Agentic Cloud
substrate primitive: verifiable compute for AI agents.

## Next steps

- Read the [README](README.md) for the project overview
- Read the [architecture notes](docs/architecture.md) for design rationale
  *(coming soon)*
- File issues and PRs at https://github.com/vje013/darwin-agentic-cloud
- Try integrating Darwin into your own agent framework — see the
  examples in `examples/`

## Troubleshooting

**`No module named 'darwin'` when Claude Desktop spawns it**
The venv is inside `~/Documents/` on macOS. See section 6 above for
how to move the venv to `~/.local/share/`.

**`Cannot connect to the Docker daemon`**
Docker isn't running. Start Docker Desktop and try again.

**`(base)` is in my prompt and uv keeps using conda's Python**
Run `conda deactivate` (twice if needed) and remove conda from your
PATH for the session:

```bash
export PATH=$(echo "$PATH" | tr ':' '\n' | grep -v -i conda | tr '\n' ':' | sed 's/:$//')
```

Then `rm -rf .venv && uv sync --extra dev --extra test`.

**The cost cap rejects my workload**
The default cap is `$0.01` and the rate is `$0.0001/sec`. If your
`timeout_sec` × rate exceeds the cap, the workload is rejected
(this is the point — it's safety enforcement). Either raise the cap
with `--cost-cap` or lower the timeout with `--timeout`.
