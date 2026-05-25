# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Pre-flight cost cap enforcement.** Workloads whose maximum possible
  cost (`timeout_sec * rate`) would exceed `cost_cap_usd` are rejected
  before any sandbox is launched. The rejection itself is signed —
  agents receive a verifiable proof that the workload was refused and why.
- `dac/cost.py` module: rate card per substrate, `BudgetExceeded` exception,
  `check_budget`, `max_possible_cost`, `cost_for_seconds`.
- Five tests for cost math, budget boundary cases, and runtime enforcement
  with a stub sandbox that proves the sandbox is not called on rejection.

### Changed

- `Runtime.run()` now performs pre-flight budget check before invoking
  the sandbox. Budget-rejected workloads return a signed attestation
  with `status="cost_exceeded"` and `cost_usd=0`.

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
