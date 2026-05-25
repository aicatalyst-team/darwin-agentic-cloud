# Security Policy

## Reporting a vulnerability

We take security seriously. If you discover a vulnerability in DAC, please report it privately so we can address it before public disclosure.

**Do not open a public GitHub issue for security vulnerabilities.**

### How to report

Email: **135543245+vje013@users.noreply.github.com**

Subject line prefix: `[SECURITY]`

Please include:

- A description of the vulnerability and its impact
- Steps to reproduce, or a proof-of-concept
- The version of DAC affected (commit hash if from main)
- Your name and any preferred attribution (or your wish to remain anonymous)
- Whether you intend to publicly disclose, and on what timeline

### What to expect

- **Acknowledgement** within 72 hours
- **Initial assessment** within 7 days
- **Fix timeline** depends on severity, but critical issues get same-week patches
- **Coordinated disclosure** — we agree on a public disclosure date with the reporter

We will credit reporters in the changelog and (with permission) in a public security advisory unless they prefer anonymity.

## Scope

In scope:

- The DAC server, CLI, SDK, and MCP server
- The attestation format and signing code
- The sandbox runtime and policy engine
- Capability token issuance and validation
- Any code in this repository

Out of scope:

- Vulnerabilities in upstream dependencies (report those to the upstream project; we will update once a patch is available)
- Social engineering attacks
- DoS against the public demo (if any)
- Issues requiring physical access to the user's machine

## Supported versions

DAC is currently in alpha (0.x). Only the latest 0.x release receives security updates. Once 1.0 ships we will publish a formal support matrix.

| Version | Supported |
|---------|-----------|
| 0.x     | yes (latest only) |
| < 0.1   | no        |

## Threat model

See [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) for the formal threat model — what DAC defends against, what it doesn't, and where the trust boundaries are.

## Known limitations (alpha)

DAC is alpha software. The following are known limitations and are **not** considered security vulnerabilities until DAC reaches 1.0:

- Local Ed25519 signing keys are not yet rooted in a real identity provider (Sigstore + OIDC integration is on the roadmap)
- Docker-based sandbox is not as strong as Firecracker or TEE-backed isolation
- Capability tokens are not yet revocation-checked on every call
- TEE attestation is not yet implemented

These limitations are documented and tracked in [docs/ROADMAP.md](docs/ROADMAP.md).
