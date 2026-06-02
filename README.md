# Darwin Agentic Cloud

**Paper:** [Bounded Execution and the Containment of Agent Traps](docs/bounded-execution.pdf) (preprint, SSRN under review)

[![PyPI](https://img.shields.io/pypi/v/darwin-agentic-cloud.svg)](https://pypi.org/project/darwin-agentic-cloud/)
[![CI](https://github.com/vje013/darwin-agentic-cloud/actions/workflows/ci.yml/badge.svg)](https://github.com/vje013/darwin-agentic-cloud/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python: 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Schema: v0.2](https://img.shields.io/badge/schema-v0.2-FFB86C.svg)](https://darwin-agentic-cloud.fly.dev/.well-known/schemas/attestation/v0.2)

> **Verifiable and free cloud compute for AI agents.**
>
> We provide an agent-first environment where every autonomous action and choice is bound and signed, including what they buy. 
>
> Your agent acts only where it can provably succeed, spends only what you authorized, and brings back a signed receipt for every step it took.
> 
> No new accounts. No new API keys.
>
> Just sign in with Gmail. Your card stays in your Google account.
>
> Open source. Free to users. All agent and human contributors welcome.
>
> Built on: webMCP + AP2
>
> Sandboxed Beta + Research: 6/1/2026

**Install:** `pip install darwin-agentic-cloud`

**Live demo:** https://darwin-agentic-cloud.fly.dev/demo

**Public keylist:** https://darwin-agentic-cloud.fly.dev/.well-known/substrate-keys.json

**v0.2 Schema:** https://darwin-agentic-cloud.fly.dev/.well-known/schemas/attestation/v0.2

---
## Darwin's Mission

Increase AI adoption by increasing AI safety.

---

## Darwin's Promises (Beginning 6/1/2026)

Darwin Agentic Cloud keeps businesses in control of their agent spend by providing deterministic execution + signed receipts over every automated purchase.

Simply put, DAC dramatically increases the chances of your business’s agent giving you the purchase outcome you want.

If you want your business’s agent booking your travel, buying your office supplies, making any business purchase at all, we make sure it actually happens.


- DAC is free. You never pay us. Ever.
- All DAC needs is your Google account.
- You never give DAC your card.
- You stay in control. Your AI can never act or spend beyond what you authorized.
- We guarantee deterministic execution surfaces for your AI's actions.
- Your AI only acts where it can succeed.
- We provide receipts for your AI's actions.
- We earn from merchants, not from you.
- If a site is not agent-ready, we do not send your agent there.

---
## Threat Model

The open agentic web has a documented attack surface. A recent Google DeepMind paper, [AI Agent Traps](https://ssrn.com/abstract=6372438), maps six classes of trap that compromise an agent through its environment, not its weights, so hardening the model closes none of them.

DAC answers two of the six by construction. The companion preprint, [Bounded Execution and the Containment of Agent Traps](docs/bounded-execution.pdf) (SSRN, under review), states the claim and its limits in full. Coverage across all six classes:

| Trap class (target) | DAC mechanism | Evidencing field | Status |
|---|---|---|---|
| Content injection (perception) | No-fallback tool surface; the agent calls declared tools (WebMCP), leaving no rendered-content carrier | `evidence.surface_url`, `evidence.tool_name` | Removed |
| Behavioural control (action) | Signed AP2 mandate (spend limit + approved counterparty set), credential isolation, pre-launch cost cap, signed refusal on a gated action | `vas.mandate_enforcement.within_scope`, `refusal{reason_code, gate, requested, allowed}`, cost-cap fields | Bounded |
| Semantic manipulation (reasoning) | — | — | Deferred |
| Cognitive state (memory, learning) | — | — | Deferred |
| Systemic (multi-agent) | Pre-launch cap bounds this agent's own participation; population dynamics out of scope | cost-cap fields | Partial |
| Human-in-the-loop (overseer) | Offline-verifiable attestation available to the overseer; judgment manipulation out of scope | `signer_key_id`, `signature`, public keylist | Partial |

> Perception traps are removed, since a tool surface offers no carrier. Action traps are bounded, since the mandate caps the action and a refusal is signed. Reasoning and memory traps act on the model's cognition and are deferred, not solved; per-agent determinism measurement is the path from contained to detected.
---

## The question agents can't answer today

Your agent just executed code on its own. You don't see what happened. You can't replay it. You can't prove it ran. You can't show a regulator, an auditor, or another agent that the workload actually executed on real hardware under your policy at the cost you agreed to.

> *"Did this workload actually run? On what hardware? Under what policy? At what cost? With what output? Can I prove it to a regulator, an auditor, or another agent?"*

Existing agent frameworks let an LLM call arbitrary tools and execute arbitrary code. None of them answer that question. The compute runs. The result comes back. You trust your framework.

**Darwin is the answer.** A signed receipt of every agent execution. Cryptographically verifiable by anyone, anywhere, anytime. No trust required.

---

## The 30-second answer

```python
from darwin import run

attestation = run('print("hello, agent")')

print(attestation["execution_result"]["stdout"])
# → hello, agent

print(attestation["execution_result"]["cost_usd"])
# → 1.3e-05

print(attestation["signer_key_id"])
# → dac-local-d1bf7cad25875cee
```

That's the whole API. One function. Returns a signed, verifiable v0.2 attestation receipt. Auto-discovers what substrates are available. Routes to the cheapest one. Enforces your cost cap before execution. Signs the result.

Anyone can verify the attestation against the public Darwin keylist:

```bash
$ darwin run 'print("hi")' --save att.json
$ darwin verify att.json
✓ identity signature verified against keylist key [active]
```

---

## What you get: the receipt

Every `darwin.run()` call returns a v0.2 attestation. The CLI renders it as an engraved certificate:

```
╔═══════════════════════ ✦  ATTESTATION OF EXECUTION  ·  darwin.agenticcloud  ✦ ════════════════════════╗
║                                                                                                       ║
║                                  ✦ SECURITAS · STABILITAS · SIGNUM ✦                                  ║
║                                                                                                       ║
║                                   CERTIFICATE No. 83C235C334904751                                    ║
║                                                                                                       ║
║                                     issued  2026-05-27T09:07:56Z                                      ║
║                                   workload  sha256:f3ca129e...f4c4                                    ║
║                                    output  sha256:98ea6e4f...7be4                                     ║
║                                            cost  $0.000014                                            ║
║                                                                                                       ║
║                      ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊                      ║
║                                                                                                       ║
║                                      substrate  local-docker-v0                                       ║
║                             schema  darwin.cloud/evidence/local-docker/v1                             ║
║                        sub-signer  dac-class-local-docker-v0-ca698355dcb631e3                         ║
║                                                                                                       ║
║                                               evidence                                                ║
║                                         container_status  ok                                          ║
║                                             exit_code  0                                              ║
║                                  stderr_hash  sha256:e3b0c442...b855                                  ║
║                                  stdout_hash  sha256:98ea6e4f...7be4                                  ║
║                                         wall_time_sec  0.142s                                         ║
║                                                                                                       ║
║                                              value-added                                              ║
║                                   ✓  cost cap  $0.000014 / $0.1000                                    ║
║                          →  routed  pick_by_cost (1 picked from 1 eligible)                           ║
║                                ✓  identity  anchored to public keylist                                ║
║                                                                                                       ║
║                      ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊ ─ ◊                      ║
║                                                                                                       ║
║                                         ✓ ATTESTATION SIGNED                                          ║
║                                    by  dac-local-d1bf7cad25875cee                                     ║
║                          schema  darwin.cloud/agenticcloud/attestation/v0.2                           ║
║                                                                                                       ║
║                                                verify                                                 ║
║                 1. curl darwin-agentic-cloud.fly.dev/.well-known/substrate-keys.json                  ║
║                        2. confirm sub-signer public key is present and active                         ║
║                        3. check identity_signature against the signed payload                         ║
║                                                                                                       ║
╚══════════════════ darwin.cloud  ·  verifiable compute for AI agents  ·  v0.2.0   ✦ ═══════════════════╝
```

### Every field is verifiable

| Field | Proves |
|---|---|
| `attestation_id` | Globally unique receipt id |
| `issued_at` | When the execution happened |
| `workload` (sha256) | Exactly what code/spec was executed |
| `output` (sha256) | Exactly what stdout was produced |
| `cost` | Real wall-time cost (what you'd pay on a non-Darwin cloud) |
| `substrate` + `version` | Which substrate ran the workload |
| `evidence` | Substrate-specific receipt (container status, exit code, hashes, wall time) |
| `sub-signer` (class key) | Substrate identity signature, anchored to the public Darwin keylist |
| `value-added` block | Three commitments Darwin's signing layer made on top of the wholesale execution |
| `signer_key_id` + `signature` | Outer operator-key envelope over the entire attestation |

The signed payload is canonical JSON. Tampering with any single byte breaks verification.

---

## Install

### From PyPI (recommended)

```bash
pip install darwin-agentic-cloud
```

That gives you the `darwin` CLI, the `darwin` Python package, and the MCP server.

### From source

```bash
git clone https://github.com/vje013/darwin-agentic-cloud
cd darwin-agentic-cloud
uv pip install -e .
```

### Requirements

- **Python 3.11+**
- **Docker** for local execution (any recent Docker Desktop / Engine works)

If only Docker is available, you get `local-docker-v0`. For web based cloud compute, you also get the four Lambda regions and AWS Batch. Auto-discovery happens at every `darwin.run()` call.

---

## The 7 CLI verbs

The complete public surface. All seven verbs are top-level and produce v0.2 attestations.

### `darwin run`

Execute a workload and emit a signed v0.2 attestation receipt.

```bash
darwin run 'print("hello")'                          # inline code
darwin run hello.py                                  # script file
darwin run hello.py --substrate aws-batch            # pin to specific substrate
darwin run hello.py --cost-cap 1.00 --timeout 300    # custom caps
darwin run hello.py --save attestation.json --json   # save the JSON
```

Default behavior auto-routes to the cheapest available substrate. Override with `--substrate` (accepts short names: `local`, `aws-batch`, `aws-lambda`, `modal`).

### `darwin verify`

Cryptographically verify an attestation against the public keylist.

```bash
darwin verify ./attestation.json
darwin verify ./attestation.json --keylist https://example.com/keys.json
darwin verify ./attestation.json --json
```

Fetches the keylist, looks up the substrate identity key, verifies the signature against the canonical signed payload. Returns the cert panel with verification status.

### `darwin price`

Preflight only — see what each run environment would cost on a non-Darwin cloud.

```bash
darwin price 'print("hi")'
darwin price hello.py --memory 1024 --timeout 60
darwin price hello.py --substrate aws-batch
darwin price hello.py --json
```

Returns a table sorted by estimated cost. Useful for deciding which substrate to run a workload on later if you choose a non-Darwin cloud.

### `darwin list`

Show every substrate this environment can use right now.

```bash
darwin list
darwin list --json
```

Auto-discovery checks environment variables and daemon availability. Substrates that fail any check are omitted with a reason.

### `darwin sign`

Generate a class signing key for a substrate (admin / operator use).

```bash
darwin sign aws-batch-ec2-spot-v0-us-east-1
darwin sign aws-batch-ec2-spot-v0-us-east-1 --out ./class-keys/aws-batch.pem
```

Used when allowlisting a new substrate. Upload the resulting PEM to your hosted signer; the public key goes in the keylist.

### `darwin try`

Run on the safest local substrate (local-docker) — never escalates to cloud.

```bash
darwin try 'print("hi")'
darwin try hello.py --save att.json
```

Identical output to `darwin run`, but forces `--substrate local-docker-v0`. Use to test a workload offline.

### `darwin who`

Show whose keys signed an attestation. Lighter than `verify` — no crypto check.

```bash
darwin who ./attestation.json
darwin who ./attestation.json --json
```

Returns the substrate signer (with keylist status) and the outer signer. Useful for audit logs.

---

## The Python API

### `darwin.run()`

```python
from darwin import run

attestation = run(
    code,                          # str: code to execute (Python by default)
    substrate=None,                # Optional[str]: short name or full id
    language="python",             # str: "python" or "node"
    cost_cap=0.10,                 # float: max USD
    timeout=30,                    # int: max seconds
    memory_mb=512,                 # int: memory limit
)
```

Returns a `dict` matching the v0.2 attestation schema.

Auto-discovers available substrates at every call. Routes by cheapest estimated cost unless `substrate=` overrides. Enforces `cost_cap` pre-execution (raises `CostCapExceeded` if estimate exceeds cap). Signs both substrate identity (inner) and operator envelope (outer).

### `darwin.Runtime`

For agent loops that want to inspect routing / substrates without re-discovering on every call:

```python
from darwin import Runtime

rt = Runtime()              # auto-discovers substrates once
for task in tasks:
    att = rt.run(task)
    print(att["attestation_id"], att["execution_result"]["cost_usd"])
```

### `darwin.CostCapExceeded`

Raised when preflight estimate exceeds `cost_cap_usd`:

```python
from darwin import run, CostCapExceeded

try:
    att = run("print('expensive')", cost_cap=0.0001)
except CostCapExceeded as e:
    print(f"Workload rejected: {e}")
```

The rejection is pre-execution and pre-signing — no sandbox is launched.

### Concurrency

`darwin.run()` is thread-safe. Multiple concurrent calls produce distinct attestations with independent signatures:

```python
import concurrent.futures
from darwin import run

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
    futures = [ex.submit(run, f'print({i})') for i in range(4)]
    attestations = [f.result() for f in futures]

# 4 unique attestation_ids, 4 unique signatures, 4 unique output hashes
```

---

## MCP integration

Darwin ships with a built-in MCP server that exposes 7 tools matching the CLI verbs. Compatible with Claude Desktop, Cursor, and any other MCP client.

### Setup (Claude Desktop)

```bash
darwin mcp install
```

Or manually add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "darwin": {
      "command": "darwin",
      "args": ["mcp", "serve"]
    }
  }
}
```

### Available MCP tools

| Tool | What it does |
|---|---|
| `darwin_run` | Execute workload, return signed v0.2 attestation |
| `darwin_verify` | Verify attestation against keylist |
| `darwin_price` | Get cost quote per available substrate (what this would've cost you on a non-Darwin cloud) |
| `darwin_list` | List discovered substrates |
| `darwin_who` | Show whose keys signed an attestation |
| `darwin_history` | Recent attestations from local storage |
| `darwin_stats` | Aggregate stats |

Every tool returns the same v0.2 attestation shape as the CLI and Python API.

---

## Architecture

### The signing chain

Two independent signatures protect every attestation:

```
┌──────────────────────────────────────────────────────────────────┐
│  v0.2 ATTESTATION                                                │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  execution_result {                                        │  │
│  │    substrate {                                             │  │
│  │      id, version, evidence_schema_id,                      │  │
│  │      evidence { container_status, exit_code, hashes },     │  │
│  │      identity_signer_type: "darwin-class-key",             │  │
│  │      identity_signer_key_id: "dac-class-...",              │  │
│  │      identity_signature: <Ed25519 over inner payload> ◄────┼──┼─── 1. SUBSTRATE IDENTITY SIGNATURE
│  │    }                                                       │  │     Hosted by Darwin's signing service.
│  │  }                                                         │  │     Anchored to PUBLIC keylist.
│  │  value_added_service {                                     │  │     Verifiable by anyone, anywhere.
│  │    identity_signing, cost_cap_enforcement, routing_decision│  │
│  │  }                                                         │  │
│  │  signer_key_id: "dac-local-...",                           │  │
│  │  signature: <Ed25519 over entire attestation> ◄────────────┼──┼─── 2. OUTER OPERATOR SIGNATURE
│  └────────────────────────────────────────────────────────────┘  │     Signed by your local operator key.
└──────────────────────────────────────────────────────────────────┘     Provides non-repudiation.
```

**Inner signature (substrate identity).** Proves the substrate actually executed the workload. Signed by a class key Darwin hosts — meaning Darwin attests to the substrate's identity. Anchored to a public keylist at a well-known URL so anyone can verify.

**Outer signature (operator envelope).** Proves you (the caller) accepted the result and bound the value-added-service block to it. Signed by a key on the calling machine.

Tampering with any byte in the canonical signed payload breaks one or both signatures.

### Substrates

A substrate is any execution backend that implements the Darwin substrate interface. Currently shipping:

| Substrate ID | Region | Cold Start | Cost Band | Identity |
|---|---|---|---|---|
| `local-docker-v0` | local | ~2s | free | hosted class key |
| `aws-lambda-us-east-1` | us-east-1 | ~500ms | ~$0.0001/job | hosted class key |
| `aws-lambda-us-west-2` | us-west-2 | ~500ms | ~$0.0001/job | hosted class key |
| `aws-lambda-eu-west-1` | eu-west-1 | ~500ms | ~$0.0001/job | hosted class key |
| `aws-lambda-ap-northeast-1` | ap-northeast-1 | ~500ms | ~$0.0001/job | hosted class key |
| `modal-v0` | modal | ~1s | ~$0.0001/job | hosted class key |
| `aws-batch-ec2-spot-v0-us-east-1` | us-east-1 | ~3min cold | $0.001-0.10/job | hosted class key |

Each substrate publishes its own evidence schema. The schema URI is embedded in every attestation so verifiers can fetch the contract.

### The Value-Added Service (VAS) block

The VAS block is Darwin's economic claim — three concrete services Darwin's signing layer provides on top of the wholesale substrate execution:

```json
"value_added_service": {
  "identity_signing": {
    "schema_compliant": true,
    "keylist_url": "https://darwin-agentic-cloud.fly.dev/.well-known/substrate-keys.json"
  },
  "cost_cap_enforcement": {
    "cap_usd": 0.10,
    "estimated_usd_max": 0.003,
    "actual_usd": 0.000014,
    "within_cap": true,
    "headroom_usd": 0.099986
  },
  "routing_decision": {
    "policy": "pick_by_cost",
    "chosen_substrate_id": "local-docker-v0",
    "chosen_reason": "lowest_estimated_cost ($0.003000)",
    "candidates_considered": 1,
    "rejected_substrates": []
  }
}
```

Three claims, all auditable from the attestation alone:
1. **Identity signing.** The substrate's identity is anchored to a public keylist verifiers can fetch.
2. **Cost cap enforcement.** Pre-execution estimate, actual cost, headroom, within-cap flag.
3. **Routing decision.** What policy was used, which substrate was picked, what was rejected and why.

--- 

## Darwin is **open.**

**What's open (free, Apache 2.0):**
- The attestation schema
- The substrate interface
- The local-docker substrate
- The verification code (`darwin verify` runs offline against the public keylist)
- The public keylist endpoint
- The schema endpoint
- The cert renderer
- All client code
- The hosted signing service (substrate class keys)
- The routing layer for cloud substrates (AWS Batch, Lambda, Modal)

You can self-host Darwin entirely — every byte of the verification path is open source. 

---

## Verification

Anyone, anywhere, anytime can verify a v0.2 attestation. The path is open and the keylist is public.

### Three-step manual verification

```bash
# 1. Fetch the public substrate keylist
curl https://darwin-agentic-cloud.fly.dev/.well-known/substrate-keys.json > keylist.json

# 2. Confirm the substrate signer's public key is present and active
jq '.keys[] | select(.signer_key_id == "dac-class-local-docker-v0-...")' keylist.json

# 3. Verify the signature against the canonical signed payload
darwin verify ./attestation.json
```

### Programmatic verification

```python
from darwin.agenticcloud.signing import verify_signature
from darwin.agenticcloud.hashing import canonical_json
from darwin.agenticcloud.substrate.base import build_identity_payload
import json, urllib.request

# Load attestation + keylist
attestation = json.load(open("att.json"))
keylist = json.load(urllib.request.urlopen(
    "https://darwin-agentic-cloud.fly.dev/.well-known/substrate-keys.json"
))

# Reconstruct what was signed
substrate = attestation["execution_result"]["substrate"]
payload = build_identity_payload(
    substrate_id=substrate["id"],
    substrate_version=substrate["version"],
    workload_spec_hash=attestation["workload_spec_hash"],
    output_hash=attestation["execution_result"]["output_hash"],
    evidence_schema_id=substrate["evidence_schema_id"],
    issued_at=attestation["issued_at"],
)

# Find the public key in the keylist
pubkey = next(k["public_key_b64"] for k in keylist["keys"]
              if k["signer_key_id"] == substrate["identity_signer_key_id"])

# Verify
verified = verify_signature(
    canonical_json(payload),
    substrate["identity_signature"],
    pubkey,
)
assert verified  # ✓ substrate identity signature valid
```

### Schema validation

v0.2 attestations conform to a public JSON Schema (Draft 07):

```bash
curl https://darwin-agentic-cloud.fly.dev/.well-known/schemas/attestation/v0.2
```

Validate any attestation before cryptographic verification:

```python
import json, jsonschema, urllib.request

schema = json.load(urllib.request.urlopen(
    "https://darwin-agentic-cloud.fly.dev/.well-known/schemas/attestation/v0.2"
))
attestation = json.load(open("att.json"))
jsonschema.validate(attestation, schema)  # raises on mismatch
```

---

## Security model

### What's protected

- **Workload integrity.** Workload spec is canonical-JSON-hashed. Tampering with the code, language, timeout, memory, or cost cap changes the hash and breaks verification.
- **Output integrity.** stdout hash is signed. Tampering with what the agent thinks the workload produced breaks verification.
- **Substrate identity.** Each substrate signs evidence with a class key. Forging "this came from AWS Batch" requires compromising the hosted class key.
- **Non-repudiation of acceptance.** The outer operator signature binds you to having accepted the result and the VAS claims. You can't later say "Darwin tricked me" — the signature proves you took it as-is.
- **Cost cap enforcement.** Workloads exceeding the preflight estimate are rejected before any substrate is launched. The rejection itself is signed.

### What's not protected (and why)

- **Side channels within the substrate.** If AWS Lambda has a vulnerability that lets workloads exfiltrate data, Darwin can't prevent that. Substrate evidence reflects what the substrate reports.
- **Substrate honesty about its own evidence.** Darwin trusts the substrate's evidence (exit code, wall time, etc.). A malicious self-hosted substrate could lie about its evidence. The class-key signing constrains who can claim to *be* a given substrate, but a compromised hosted signer could mint fraudulent attestations.
- **Operator key compromise.** If your local Ed25519 operator key is stolen, attackers can sign attestations as you. Treat it like an SSH key.

### Threat model

| Attacker | Can they... |
|---|---|
| External party with no access | Forge attestations? **No.** Both signatures verify against keys they don't have. |
| External party who reads attestations | Read your code and outputs? **Yes** — attestations include workload + stdout in plaintext by default. Use `extensions.encrypted_payload=true` to redact. |
| Substrate operator (AWS, Modal) | See your workload? **Yes** — they execute it. Same trust model as any cloud provider. |
| Darwin hosted signer (us) | Forge a substrate identity? **Yes, if we wanted to.** Mitigation: multi-party signing roadmap (v4). |
| Compromised Fly host | Forge keylist responses? **Yes.** Mitigation: offline-signed keylist roadmap (v4). |

---

## Examples

### Multi-step agent workflow

```python
from darwin import run

# Step 1: parse a CSV
att1 = run('''
import pandas as pd
df = pd.read_csv("/tmp/sales.csv")
print(df.describe().to_json())
''', cost_cap=1.0, timeout=60, memory_mb=2048)

# Step 2: derive insights from parsed data
att2 = run(f'''
import json
stats = json.loads({att1["execution_result"]["stdout"]!r})
top_q = max(stats.items(), key=lambda kv: kv[1]["mean"])
print(f"Top quantile: {{top_q}}")
''', cost_cap=0.1, timeout=15)

# Both attestations are signed. The agent can prove to its operator
# that both executions happened, in what order, at what cost, on what
# substrates.
total = att1['execution_result']['cost_usd'] + att2['execution_result']['cost_usd']
print(f"Total cost: ${total:.4f}")
```

### Batch processing with cost guard (we guarantee your agent NEVER crosses your budget threshold)

```python
from darwin import run, CostCapExceeded

workloads = [...]  # 1000 tasks
total_cost = 0.0
budget = 5.00

for w in workloads:
    if total_cost > budget * 0.9:
        print(f"90% budget hit, stopping")
        break
    try:
        att = run(w, cost_cap=0.01, timeout=10)
        total_cost += att["execution_result"]["cost_usd"]
    except CostCapExceeded:
        continue  # individual task too expensive — skip
```

### Audit log of every execution

```python
from darwin import run
from datetime import datetime
import json

LOG = open("agent_audit.jsonl", "a")

def audit_run(code, **kwargs):
    att = run(code, **kwargs)
    LOG.write(json.dumps({
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "attestation_id": att["attestation_id"],
        "substrate": att["execution_result"]["substrate"]["id"],
        "cost_usd": att["execution_result"]["cost_usd"],
        "signer_key_id": att["signer_key_id"],
        "workload_hash": att["workload_spec_hash"],
    }) + "\n")
    LOG.flush()
    return att

# Every agent action is now in an append-only log with cryptographic proof
# that the execution happened.
```

---

## Roadmap

### v3.1 (next minor)

- Add Modal substrate to entrypoint materialization on hosted signer
- Fix `try` double-ticker UX nit
- Fix `verify` success-message status interpolation
- Multi-substrate live demo (rotate through aws-batch, aws-lambda variants)
- Async `darwin.run()` for high-throughput agent loops

### v4 (next major)

- **TEE (Trusted Execution Environment) substrate.** AWS Nitro Enclaves, GCP Confidential Compute. Substrate evidence includes attestation document from the hardware itself.
- **Multi-party signing for class keys.** Threshold signature scheme so no single hosted signer can forge a substrate identity.
- **Signed keylist.** Offline root key signs the keylist; verifiers check the root signature before trusting any substrate identity.
- **Encrypted attestations.** Optional end-to-end encryption of workload + output, with only hashes in the public envelope.

### v5+ (research)

- **Cross-chain anchoring.** Periodically anchor the keylist hash to a public blockchain so historical key state is provably immutable.
- **Hardware-rooted operator keys.** Outer signature from a Secure Enclave / TPM / YubiKey instead of software keys.
- **Federated keylists.** Multiple competing Darwin keylist hosts; verifiers can pick which trust roots to honor.

---

## Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the DCO sign-off process and dev setup.

```bash
git clone https://github.com/vje013/darwin-agentic-cloud
cd darwin-agentic-cloud
uv sync --extra dev --extra test
uv run pytest
```

We're particularly interested in contributions that:
- Add new substrate adapters (Akash, Fly Machines, Cloudflare Workers, Bacalhau, etc.)
- Improve the verification path documentation
- Build verifiers in non-Python languages (Go, Rust, TypeScript)
- Extend the v0.2 schema with new VAS fields (with public discussion first)

---

## Citation

If you use Darwin in research, please cite:

```bibtex
@software{darwin_agentic_cloud_2026,
  author = {Edouard, Vladimir J.},
  title = {Darwin Agentic Cloud: Verifiable Compute for AI Agents},
  url = {https://github.com/vje013/darwin-agentic-cloud},
  version = {3.0.0},
  year = {2026},
}
```

### Built on the shoulders of

```bibtex
@software{pydantic,
  title = {Pydantic: Data validation using Python type hints},
  url = {https://github.com/pydantic/pydantic},
}

@software{cryptography,
  title = {cryptography: A package designed to expose cryptographic primitives and recipes to Python developers},
  url = {https://github.com/pyca/cryptography},
}

@software{fastapi,
  author = {Ramírez, Sebastián},
  title = {FastAPI: Modern, fast (high-performance) web framework for building APIs with Python},
  url = {https://github.com/fastapi/fastapi},
}

@software{uvicorn,
  title = {Uvicorn: An ASGI web server, for Python},
  url = {https://github.com/encode/uvicorn},
}

@software{httpx,
  title = {HTTPX: A next-generation HTTP client for Python},
  url = {https://github.com/encode/httpx},
}

@software{slowapi,
  title = {SlowApi: A rate limiting extension for Starlette and FastAPI},
  url = {https://github.com/laurentS/slowapi},
}

@software{boto3,
  title = {Boto3: The AWS SDK for Python},
  url = {https://github.com/boto/boto3},
}

@software{aiobotocore,
  title = {aiobotocore: asyncio support for botocore},
  url = {https://github.com/aio-libs/aiobotocore},
}

@software{typer,
  author = {Ramírez, Sebastián},
  title = {Typer: Build great CLIs with Python},
  url = {https://github.com/fastapi/typer},
}

@software{rich,
  author = {McGugan, Will},
  title = {Rich: A Python library for rich text and beautiful formatting in the terminal},
  url = {https://github.com/Textualize/rich},
}

@software{docker_py,
  title = {Docker SDK for Python},
  url = {https://github.com/docker/docker-py},
}

@software{mcp,
  title = {Model Context Protocol: A protocol for connecting AI assistants to external data sources and tools},
  url = {https://github.com/modelcontextprotocol/python-sdk},
}

@software{sqlalchemy,
  title = {SQLAlchemy: The Database Toolkit for Python},
  url = {https://github.com/sqlalchemy/sqlalchemy},
}
```

---

## License

Apache 2.0. See [LICENSE](LICENSE).

The hosted signing service and routing layer are free to all users today; Darwin Adaptive Systems reserves the right to offer paid enterprise tiers in future.

---

✦ SECURITAS · STABILITAS · SIGNUM ✦
