# Darwin Agentic Cloud

[![CI](https://github.com/vje013/darwin-agentic-cloud/actions/workflows/ci.yml/badge.svg)](https://github.com/vje013/darwin-agentic-cloud/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

> The verifiable agentic cloud. Open-source compute for AI agents with cryptographically signed attestations.

Darwin Agentic Cloud (DAC) is a programmatic compute substrate designed for **AI agents to call directly**. Every execution returns a cryptographically signed attestation containing the workload hash, output hash, substrate identity, cost, and policy compliance proof. Agents that take action based on compute results can verify that what they asked for actually happened.


## Why DAC

Agent frameworks today let an LLM call arbitrary tools and execute arbitrary code. None of them answer the question an agent operator actually needs answered:

> "Did this workload actually run? On what hardware? Under what policy? At what cost? With what output? And can I prove it to a regulator, an auditor, or another agent?"

DAC is the answer. One API. Verifiable execution. Bounded spend. Capability-based auth. Native MCP support so any Claude, GPT, or Gemini agent can call it with zero glue code.

## Architecture

```text
+--------------------------------------------------------------+
|  Agent  (Claude / GPT / Gemini / LangGraph / CrewAI / ...)   |
+------------------------------+-------------------------------+
                               |
                               |  POST /v0/run  |  MCP tool call
                               v
+--------------------------------------------------------------+
|  DAC Server  (FastAPI + MCP)                                 |
|  [ Auth ]  [ Cost Meter ]  [ Policy Engine ]  [ Router ]     |
+------------------------------+-------------------------------+
                               |
                               v
+--------------------------------------------------------------+
|  Sandbox Layer                                               |
|  Docker (v0)  ->  gVisor  ->  Firecracker  ->  TEE           |
+------------------------------+-------------------------------+
                               |
                               v
+--------------------------------------------------------------+
|  Signed Attestation  (Ed25519 -> Sigstore in production)     |
|  workload_hash | output_hash | substrate | cost | policy     |
+--------------------------------------------------------------+
```

## Quickstart

### Prerequisites

- Python 3.11+
- Docker Desktop (running)
- [`uv`](https://docs.astral.sh/uv/) for fast dependency management

### Install

```bash
git clone https://github.com/vje013/darwin-agentic-cloud.git
cd darwin-agentic-cloud
uv sync --extra dev --extra test
```

### Run your first attested workload

```bash
# Start the server
make run

# In another terminal, run a workload
dac run examples/example_workloads/hello.py

# Verify the attestation
dac attest verify ./attestations/<id>.json
```

### Use from an AI agent via MCP

Add DAC to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "dac": {
      "command": "dac",
      "args": ["mcp", "serve"]
    }
  }
}
```

Restart Claude Desktop. You can now ask Claude:

> "Run a Python script that computes the first 100 prime numbers and verify the attestation."

Claude will call DAC, execute the workload in a sandbox, return the signed attestation, and verify the signature — all without you writing any glue code.

## Core concepts

- **Workload spec** — code + inputs + cost cap + timeout. Hashed and signed.
- **Sandbox** — isolated execution environment (Docker → gVisor → Firecracker → TEE).
- **Attestation** — cryptographically signed proof of execution. Includes workload hash, output hash, substrate identity, cost, and policy compliance.
- **Capability token** — scoped, revocable auth credential. Agents present tokens, not user credentials.
- **Substrate** — the underlying compute (local Docker, leased GPU, Akash node, HPC cluster). Agents don't pick; DAC routes.

See [docs/CONCEPTS.md](docs/CONCEPTS.md) for the full model.

## Documentation

- [Quickstart](docs/QUICKSTART.md)
- [Architecture](docs/ARCHITECTURE.md)
- [MCP integration](docs/MCP.md)
- [Python SDK](docs/SDK.md)
- [Security model](docs/SECURITY.md)
- [Attestation spec (RFC-0001)](docs/rfc/RFC-0001-attestation-format.md)
- [Roadmap](docs/ROADMAP.md)

## Status

DAC is in **alpha**. The API is unstable and the attestation format may change in incompatible ways before v1.0. Production deployments are not yet supported. Follow the [CHANGELOG](CHANGELOG.md) for release notes.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the developer setup, DCO sign-off requirements, and PR process.

DAC is developed in the open. We ship in small, reviewable PRs with full test coverage. The [roadmap](docs/ROADMAP.md) is public and we welcome RFCs against the [docs/rfc/](docs/rfc/) directory.

## Influences and prior art

DAC stands on the shoulders of:

- [Modal](https://modal.com) for the developer-experience bar on serverless agentic compute
- [E2B](https://e2b.dev) for sandbox-as-a-service for AI agents
- [SkyPilot](https://skypilot.co) for cross-substrate workload routing
- [Ray](https://ray.io) for the distributed-compute primitive
- [Sigstore](https://sigstore.dev) for the keyless-signing pattern that DAC attestations will adopt in production
- [SPIFFE/SPIRE](https://spiffe.io) for the workload identity model
- [Open Policy Agent](https://openpolicyagent.org) for policy-as-code as a runtime layer
- [Hivemind](https://github.com/learning-at-home/hivemind) and [Petals](https://github.com/bigscience-workshop/petals) for decentralized training and inference
- [Model Context Protocol](https://modelcontextprotocol.io) for the agent-tool integration standard

## License

Apache License 2.0 — see [LICENSE](LICENSE).

## Citation

If you use DAC in academic work, please cite:

```bibtex
@software{dac2026,
  author = {Edouard, Vladimir J.},
  title = {Darwinic Agentic Cloud: Verifiable Compute for AI Agents},
  year = {2026},
  url = {https://github.com/vje013/darwin-agentic-cloud}
}
```

## macOS Claude Desktop setup

Claude Desktop's renderer process runs sandboxed under macOS Seatbelt
and cannot read inside `~/Documents/`. If your project lives there,
the venv must live outside it.

Recommended layout:

    mkdir -p ~/.local/share/darwin-agentic-cloud
    uv venv ~/.local/share/darwin-agentic-cloud/.venv --python 3.12
    ln -s ~/.local/share/darwin-agentic-cloud/.venv .venv
    uv pip install . --python ~/.local/share/darwin-agentic-cloud/.venv/bin/python

Then point Claude Desktop's MCP config at the symlinked venv path.
Editable installs (`-e .`) embed a path back into `~/Documents/` via
a `.pth` file, which the sandbox blocks. Use a non-editable install
for the Claude-spawned venv; keep a separate editable install for
your own dev terminal if you want hot reload.
