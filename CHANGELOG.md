# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [3.0.0] — 2026-05-27

The verifiable-compute toll-booth. This release introduces the v0.2 attestation schema,
the multi-substrate router, and a clean seven-verb public CLI/MCP surface that mirrors
the Apple-grade `darwin.run()` agent API.

### Highlights

- **`from darwin import run` — one-line agent API.** Auto-discovers available substrates,
  routes to the cheapest one by default, executes workload, returns a cryptographically
  signed v0.2 attestation.
- **v0.2 attestation schema** at `darwin.cloud/agenticcloud/attestation/v0.2`. Adds the
  `value_added_service` block (cost-cap enforcement, routing decision, identity-signing
  metadata) on top of the substrate execution receipt.
- **Multi-substrate routing.** Workloads can run on `local-docker`, `aws-lambda`
  (4 regions), `modal`, or `aws-batch-ec2-spot-v0-us-east-1`. The router picks by cost,
  by explicit substrate, or by capability. Auto-discovery checks credentials and
  daemon availability at call time.
- **AWS Batch substrate adapter.** Real EC2 Spot pricing via `DescribeSpotPriceHistory`,
  60-second minimum billing, soft-fail on pricing API unreachability. Substrate identity
  signed by the hosted Darwin class key.
- **Engraved-certificate panel.** v0.2 attestations render as a Treasury-style
  certificate: double-line borders, gold seals (`✦`), diamond dividers, motto
  (`SECURITAS · STABILITAS · SIGNUM`), and full-center alignment. The cert reads as
  the receipt agents actually need.
- **HTML demo page** at `/demo` with a live signed attestation, downloadable JSON
  artifact, and three-step verification instructions.
- **Public v0.2 JSON Schema** (Draft 07) at `/.well-known/schemas/attestation/v0.2`
  for self-describing attestations.

### Added

- `darwin/__init__.py` re-exports `run`, `Runtime`, `CostCapExceeded` for ergonomic
  imports.
- `darwin.agenticcloud.runtime_v02.Runtime` — auto-discovering runtime that constructs
  the v0.2 attestation, validates against the schema registry, and signs the outer
  envelope.
- `darwin.agenticcloud.router` — substrate discovery + routing policies
  (`pick_by_cost`, `pick_by_substrate`, `pick_by_capability`). Short-name resolution
  (`aws-batch` → `aws-batch-ec2-spot-v0-us-east-1` using `AWS_REGION`).
- `darwin.agenticcloud.substrate.aws_batch` — full AWS Batch (EC2 Spot) substrate
  adapter with idempotent `boto3` deploy orchestrator (`infra/aws_runner/batch_deploy.py`).
- Seven top-level CLI verbs: `run`, `verify`, `price`, `list`, `sign`, `try`, `who`.
- Seven MCP tools mirroring the CLI: `darwin_run`, `darwin_verify`, `darwin_price`,
  `darwin_list`, `darwin_who`, `darwin_history`, `darwin_stats`.
- `infra/bake_demo_attestation.py` — bake-time generator for the hosted demo page.
- Pre-flight cost-cap enforcement (was in [Unreleased]). Workloads whose preflight
  estimate exceeds `cost_cap_usd` are rejected before any sandbox is launched;
  the rejection is signed.
- 79 new tests across substrate adapters, router, runtime, pricing client, schema
  validation. Total project tests: 388 passing.

### Changed

- `darwin run` is now the v0.2 entry point. Accepts code-as-string OR file path
  (heuristic: if the argument exists as a file, read it; otherwise execute inline).
- Legacy v0.1 `run` preserved at `darwin attest run-v01`.
- `darwin substrates demo` now executes real substrates by default; `--mock`
  preserves the prior synthetic behavior.
- Attestation panel renderer redesigned around the engraved-certificate aesthetic.
- `SUBSTRATE_KEYLIST_URL` updated to `https://darwin-agentic-cloud.fly.dev` until
  DNS for `darwin.cloud` is provisioned.
- MCP tool surface rewritten from `dac_*` prefix to `darwin_*`. Old tool names
  removed (breaking change for MCP client configs).

### Removed

- v0.1-only MCP tools (`dac_run_python`, `dac_run_node`, etc.). Their behavior is
  preserved via the new `darwin_run` tool with optional `language` parameter.

## [0.1.0] — initial alpha

### Added

- **Attestation layer.** Ed25519-signed, canonical-JSON-hashed, tamper-evident
  attestations binding workload spec, execution result, substrate identity,
  cost, and signer identity.
- **Docker sandbox.** Memory limits, CPU limits, pids limit, no network,
  read-only-friendly tmpfs, no-new-privileges, non-root user (65534:65534),
  all capabilities dropped. Python and Node.js runtimes.
- **Runtime orchestrator.** Single entry point: `Runtime().run(spec)`
  returns a `SignedAttestation`.
- **CLI** (`dac`): `run`, `attest verify`, `attest show`, `keys init`,
  `keys show`, `serve`, `mcp serve`, `version`.
- **HTTP server** (FastAPI): `GET /healthz`, `GET /v0/identity`,
  `POST /v0/run`, `POST /v0/attestations/verify`. Auto-generated OpenAPI
  docs at `/docs`.
- **MCP server**: stdio transport, four tools (`dac_run_python`,
  `dac_run_node`, `dac_verify_attestation`, `dac_identity`). Verified
  end-to-end with Claude Desktop.
- Apache 2.0 license, contributing guide with DCO sign-off, security
  policy, code of conduct, issue/PR templates.
- 16 attestation tests proving tamper-evidence under every tampering
  vector (stdout, cost, workload spec, signer key id, public key,
  signature), JSON round-trip, deepcopy survival, and malformed input
  rejection.

[Unreleased]: https://github.com/vje013/darwin-agentic-cloud/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vje013/darwin-agentic-cloud/releases/tag/v0.1.0
