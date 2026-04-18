# Savvy Orthogonal Concerns
## Cross-cutting issues that affect every connector and agent interaction

These are concerns that do not belong to any single connector or agent
but must be addressed at the framework level. Getting them wrong early
means reworking every integration later.

---

## Security

**Credential management** — each connector holds sensitive credentials
(API tokens, OAuth secrets, passwords). Currently stored as environment
variables. At enterprise scale this needs a secrets manager (HashiCorp
Vault, AWS Secrets Manager) with rotation, auditing, and least-privilege
access per connector.

**Data sensitivity** — connector snapshots may contain sensitive business
data. A Salesforce snapshot contains deal values and customer names. A
Zoom snapshot contains participant information. The agent currently logs
all conversations including snapshot context for Oumi training. This
needs a data classification layer — some fields should never be logged.

**Agent prompt injection** — a malicious actor could craft data in a
connected system (e.g. a Monday.com item name) that attempts to manipulate
the agent's behavior through the system prompt. The snapshot-to-prompt
pipeline needs sanitization.

**API surface exposure** — the REST API currently has no authentication.
Any caller can query any connector. In production this needs API key
authentication or OAuth2 at the API gateway level.

**Audit trail** — enterprise customers need to know who asked what and
when. The conversation logger captures conversations but not the caller
identity. Access logs need to be tied to authenticated identities.

---

## Globalization and Localization

**Language** — the agent currently responds only in English. Network
Weather's website already supports Spanish. Enterprise customers in
non-English markets need responses in their language. The system prompt
should accept a locale parameter and instruct the agent accordingly.

**Date and time formats** — connector data contains timestamps in ISO
format but agent responses mention dates in natural language. "3 days ago"
means different things in different time zones. The agent needs the user's
locale and timezone to express temporal information correctly.

**Currency** — Salesforce deals are in USD by default. Multi-national
deployments need currency-aware formatting in findings and responses.

**Units** — system health reports temperatures in Celsius or Fahrenheit,
network latency in milliseconds, disk in GB or GiB. These need to respect
locale preferences.

**Finding descriptions** — connector finding descriptions are currently
hardcoded in English. Full localization requires externalizing these
strings, which is a significant refactor of every connector.

---

## Coordination Across Agents

**Multi-agent consistency** — when multiple agents query the same
connector simultaneously, they each get independent snapshots captured
at slightly different times. For a fleet of 200 machines this means the
multi-connector agent is reasoning over data with different timestamps.
A snapshot coordination layer would ensure consistent point-in-time views.

**Agent memory** — currently each query is stateless. The agent has no
memory of previous queries beyond what is passed in the history parameter.
For long-running enterprise monitoring, the agent should remember patterns
it has observed over time — "this network has been degraded every Monday
morning for the past month."

**Conflict resolution** — when two connectors surface contradictory
information (Network Weather says connectivity is fine, Zoom says call
quality is poor), the multi-connector agent must reason about the
contradiction explicitly rather than averaging the two views.

**Agent specialization** — the current DiagnosticAgent is generic across
all connectors. As Savvy scales, specialized agents per domain (a network
agent, a CRM agent, a meeting quality agent) coordinated by an orchestrator
agent would produce higher quality responses for domain-specific queries.

**Rate limiting and backpressure** — if 200 machines all push snapshots
simultaneously and users query all systems at once, the agent layer will
be overwhelmed. A queue-based architecture with backpressure is needed
for fleet-scale deployments.

---

## Data Freshness and Consistency

**Snapshot staleness** — connectors fetch data on demand. For a 200-machine
fleet, fetching all machines synchronously on every query is too slow.
A background refresh pipeline that keeps snapshots warm in a cache would
make multi-connector queries fast regardless of fleet size.

**Change detection** — the agent currently describes the current state.
It should also detect and surface changes: "your network had 220 dropouts
last week, this week it has 340 — that is a 55% increase." This requires
storing historical snapshots and comparing them.

---

## Observability

**Agent reasoning transparency** — users should be able to ask "why did
you say that?" and get a reference to the specific findings that drove
the response. The sources field in AgentResponse is a start but needs
to be more precise.

**Connector health monitoring** — if a connector starts failing (API down,
credentials expired), the system should surface this proactively rather
than returning errors only when queried.

**Cost tracking** — every agent query consumes LLM tokens. At fleet scale
this becomes a significant cost. Token usage should be tracked per query,
per connector, and per user to enable cost attribution and optimization.
