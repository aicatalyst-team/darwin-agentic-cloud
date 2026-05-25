# AGENT TASK: Continuous outreach loop

@copilot — this is a continuous, multi-PR task. Do NOT close this issue until every checklist item is complete. After each PR you open, return to this issue, update the checklist below by checking the box, and pick the next unchecked item.

## Try / Fail / Retry rules

1. Before opening any PR against an external repo, search that repo for existing similar PRs from any user. If found, do NOT open a duplicate — add a comment to this issue noting the existing PR and check the box.
2. After opening any PR, return to this issue and add a comment with the PR link.
3. If a PR opened from this task fails CI, fix and retry up to 3 times. After 3 failures, add the `needs-human` label and a comment explaining the blocker.
4. If a PR is closed by a maintainer with a rationale, do NOT reopen. Add the rationale as a comment on this issue and check the box.
5. Every 6 hours, this issue gets a fresh `@copilot` mention via the outreach-loop workflow. When that happens, pick up where you left off.
6. **Every commit must be DCO signed-off.** Use `git commit -s` or run `git rebase --signoff main` before pushing. If a PR fails the DCO check, rewrite the affected commits with `Signed-off-by:` trailers and force-push. (Note: CI is configured to auto-exempt `copilot[bot]` author commits, but signing off is still good hygiene.)
7. **Fix-it-when-you-see-it rule.** If a CI check fails for any reason that is a systemic / repo-config issue (not a bug in your own code), open a separate PR to fix the underlying config, then return to the original task. Examples: missing CI job for a new file type, outdated GitHub Actions version, missing dependency in pyproject.toml, broken lint rule. Do NOT work around it; fix the root cause.

## Phase 1: MCP server registry submissions

For each, fork the repo to vje013, add an entry for darwin-agentic-cloud following the repo's existing format, and open a PR from vje013's fork back to the upstream.

Use this consistent description: "Darwin Agentic Cloud — verifiable compute substrate for AI agents with cryptographically signed Ed25519 attestations. MCP-native (`darwin mcp install` configures Claude Desktop in one command). Open source (Apache 2.0). https://github.com/vje013/darwin-agentic-cloud"

- [ ] `modelcontextprotocol/servers`
- [ ] `punkpeye/awesome-mcp-servers`
- [ ] `wong2/awesome-mcp-servers`
- [ ] `appcypher/awesome-mcp-servers`
- [ ] `habitoai/awesome-mcp-servers`

## Phase 2: Internal infrastructure

After Phase 1 PRs are open (don't wait for merges), proceed in sequence:

- [ ] PR: add `docs/AGENT_INVENTORY.md` cataloging autonomous coding agents on GitHub (Aider, Devin, Copilot Coding Agent, Cursor Composer, Codex CLI, Windsurf, Sweep, codium-pr-agent). For each, document mention syntax, setup requirements, and a paste-ready mention block.
- [ ] PR: update CONTRIBUTING.md with a "For Autonomous Agents" section explaining DCO sign-off, branch naming, PR template, CI requirements, and the mention syntax for each agent in the inventory.
- [ ] PR: open 3 good-first-issue tasks, each self-contained with clear acceptance criteria, labeled `good first issue` and `agent-friendly`. Proposed:
  - "Add Bun runtime to the Docker sandbox alongside Python and Node"
  - "Add structured logging via OpenTelemetry to runtime.py"
  - "Add a benchmarking harness comparing attestation throughput across sandbox configs"
- [ ] Open a GitHub Discussion in the "Show and tell" category titled "Call for contributors — autonomous agents welcome" linking the three good-first-issues above.

## Phase 3: Cross-link backlinks (parallel with Phase 2)

For each, identify open issues touching verifiable execution, sandboxing, agent attestation, or compute trust. Add a thoughtful, on-topic comment that links to Darwin Agentic Cloud as a partial answer. NO drive-by spam — every comment must add real technical context.

- [ ] LangChain (`langchain-ai/langchain`)
- [ ] AutoGen (`microsoft/autogen`)
- [ ] CrewAI (`crewAIInc/crewAI`)
- [ ] LangGraph (`langchain-ai/langgraph`)

## Status reporting

After each work cycle, add a comment to this issue with:
- PRs opened (with links)
- PRs merged (with links)
- PRs blocked (with reason)
- Discussions or comments posted (with links)
- What you're attempting next
