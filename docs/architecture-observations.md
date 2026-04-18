# Savvy Architecture Observations
## Horizontal vs Vertical Framework Analysis

After building 6 connectors across completely different domains, here is what
held up in the horizontal layer and what revealed itself as vertical.

---

## Connectors Built

| Connector      | Domain              | Auth          | Data Model         |
|----------------|---------------------|---------------|--------------------|
| Network Weather| Network diagnostics | OAuth2        | Findings + QoS     |
| System Health  | Machine metrics     | None (psutil) | Metrics only       |
| Monday.com     | Project management  | API token     | Items + statuses   |
| Salesforce     | CRM                 | OAuth2 + SOQL | Records + pipeline |
| Zoom           | Video conferencing  | OAuth2        | QoS + participants |
| Google Meet    | Video conferencing  | OAuth2        | Records only       |

---

## What Held Up (Horizontal)

**DiagnosticSnapshot as the data contract** — every connector produces one.
The agent never needed to change regardless of which connector fed it data.
This is the single most important architectural decision and it held across
all 6 integrations without modification.

**Finding as the unit of insight** — severity, category, title, description,
resolution, technical_detail. Every domain maps to this structure naturally.
A stalled Salesforce opportunity, a stuck Monday.com item, a Zoom participant
with bad audio, a network dropout — all express cleanly as Findings.

**Severity as the universal signal** — OK, INFO, WARNING, CRITICAL works
across every domain. The agent uses it to prioritize responses regardless
of connector. This is the lingua franca of the horizontal layer.

**The agent reasoning layer** — zero connector-specific logic. DiagnosticAgent
and MultiConnectorAgent have no knowledge of Network Weather, Salesforce,
or any other integration. This held perfectly across all 6 connectors.

---

## What Revealed Itself as Vertical

**FindingCategory** — initially had WIFI, GATEWAY, ISP, VPN in the core schema.
These are Network Weather-specific concepts. Refactored to separate horizontal
categories (SECURITY, PERFORMANCE, CONNECTIVITY, SYSTEM, AVAILABILITY,
CONFIGURATION, COLLABORATION) from vertical connector-specific ones.
This is the clearest example of vertical concerns leaking into the horizontal layer.

**NetworkQuality** — maps cleanly to network and video conferencing connectors
but is meaningless for Salesforce or Monday.com. Zoom exposes latency/loss/jitter
per participant. Google Meet exposes none of these metrics at all. The optional
field pattern in DiagnosticSnapshot handles this, but raises the question:
should NetworkQuality be a connector capability declaration rather than an
optional field on every snapshot?

**Severity thresholds** — each connector defines its own thresholds for what
constitutes WARNING vs CRITICAL. CPU at 80% is a warning for system health.
A deal 14 days without activity is a warning for Salesforce. There is no
universal threshold logic — this is inherently vertical.

**Finding generation logic** — the rules for what constitutes a finding are
completely domain-specific. This is correctly vertical but the pattern of
how findings are generated (fetch data, apply rules, produce Finding objects)
is horizontal and could be formalized further.

---

## What Needs to Change Next

1. **Connector capability declaration** — connectors should declare what fields
   of DiagnosticSnapshot they populate. A consumer of the snapshot should not
   need to check if network_quality is None to know if the connector supports it.

2. **Threshold configuration** — severity thresholds should be configurable per
   connector instance, not hardcoded. This enables enterprise customers to tune
   what constitutes a warning for their specific context.

3. **Finding deduplication** — when running multi-connector queries, the same
   underlying issue may surface as findings in multiple connectors. The horizontal
   layer needs a deduplication or correlation mechanism.

4. **Connector registry** — as the number of connectors grows, manually wiring
   them into the API and demo script will not scale. A connector registry with
   auto-discovery would make onboarding a new connector a single-file operation.
