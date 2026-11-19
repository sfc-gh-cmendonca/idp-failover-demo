# IDP Cross-Region Failover on Snowflake: Demo Overview

## Purpose
JPMC's Investor Data Platform (IDP) serves **product (instrument) reference data**: a high-rate stream of
full-state product updates, and low-latency point lookups by product ID from downstream APIs.
The customer needs to see that this workload can run on Snowflake **interactive tables** and survive the
loss of a region with:
- **a measured, short RTO**: minutes, not hours;
- **zero data loss after reconciliation**, even though replication is asynchronous;
- **a clear operating procedure** for failover and failback, using standard Snowflake SQL.

The demo runs live in two Snowflake accounts, Frankfurt and Ireland, with real ingest and real query
traffic running throughout. Every number on screen is measured, not simulated.

## Architecture
| Layer | Component | Notes |
|---|---|---|
| Ingest | Producer on EC2 (eu-west-1), **Snowpipe Streaming high-performance SDK** | Synthetic product UDM (ISIN/CUSIP/SEDOL, pricing, corporate actions, status). 50 msg/s in the demo. Versions are epoch microseconds, so they always increase across regions |
| Source journal | JSONL journal on the producer, 24 h retention | Stands in for the upstream system's replay capability (Kafka retention, AMPS transaction log) |
| Storage | `PRODUCT_PAYLOAD` **interactive table**, clustered by `PRODUCT_ID`, **search optimization** on `PRODUCT_ID` | Key fields are flattened into columns; the full message is kept as `PAYLOAD` |
| Serving | `IDP_IW_XS` **interactive warehouse**, 6 × XS, concurrency 32 | Point lookups for the latest version of a product |
| DR | **Failover group** `IDP_FG`, Frankfurt → Ireland, 10-minute schedule | Asynchronous. Search optimization is rebuilt on the replica. Warehouses and roles are configured in each account |
| Clients | 100 ADBC connections from EC2 (eu-central-1) | Reports QPS and p50/p95 per second |
| Control | React + FastAPI storyline UI | Calls an owner's-rights procedure `DEMO_OPS` with a locked-down service user; controls the producer over a restricted SSH command |

## The storyline
1. **Ingest on Frankfurt.** Data streams in; every message is journaled at source.
2. **Traffic on Frankfurt.** Live lookups; the chart shows p95 latency for the serving region.
3. **Stop replication.** The Ireland replica is frozen at its last snapshot, while Frankfurt keeps writing.
4. **Stop Integration and API Traffic.** "Frankfurt is lost." Rows written after the snapshot form the RPO gap.
5. **Fail over.** Ireland's failover group is promoted to primary, and its interactive warehouse is created or resumed.
6. **Replay and reconcile.** The last replicated point is read from Ireland's refresh history. Journal rows
   after that point (minus a safety margin) are diffed against Ireland on `(PRODUCT_ID, PRODUCT_VERSION)`,
   and only the missing rows are streamed. This is exactly-once on an append-only table. The demo then
   checks three things: Ireland's row count equals Frankfurt's final count, no rows are missing, and no new
   duplicates exist.
7. **Ingest to Ireland.** The producer is repointed; writes land in the new primary.
8. **Traffic to Ireland.** Lookups reconnect to Ireland; the chart line switches region.
9. **Fail back.** Frankfurt refreshes from Ireland, is promoted again, and replication to Ireland resumes.

## Measured results
| Metric | Result |
|---|---|
| RTO (writer stop → Ireland primary) | 39–57 s |
| RPO window before replay | 134–275 s of writes (bounded by the 10-minute schedule) |
| Data loss after replay | **0**: replays exact (e.g. 26,985 / 26,985 rows) |
| Replay idempotency | Killed mid-replay; the re-run sent exactly the remaining 15,772 rows |
| Traffic switchover | 7–11 s |
| Failback | Row counts identical; zero loss |
| Lookup throughput (6 × XS, ADBC, 100 connections) | ~1,250 QPS with ingest off; ~650 QPS with ingest on. Earlier JDBC test: 1,776 QPS ≈ 153M lookups/day |

## Key messages
- **Asynchronous replication plus a source replay gives an effective RPO of zero.** Snowflake replication
  bounds the gap; the upstream journal closes it. The `(id, version)` diff makes the replay safe to repeat.
- **Failover is a single statement** (`ALTER FAILOVER GROUP … PRIMARY`). For production,
  **Client Redirect** (`ALTER CONNECTION … PRIMARY`) moves clients without configuration changes. With
  PrivateLink, the customer also updates their private DNS CNAME.
- **Interactive tables and search optimization replicate.** Warehouses, roles and grants are configured
  separately in each account, and that belongs in the runbook.

## Known limitations and next steps
- **Append-only today.** Ingest competes with lookups on the same table, roughly halving lookup throughput.
  The planned fix is a CDC current-state table (`PRIMARY KEY … INITIALLY DEFERRED` + `VERSION BY PRODUCT_VERSION`),
  which needs the Interactive Table CDC private preview (`FEATURE_INTERACTIVE_TABLE_CDC`) enabled on both accounts.
- **Customer scale is not shown.** The target is 300M updates/day, about 3,472/s on average; the demo runs at 50/s.
  Size ingest and warehouses with a dedicated load test.
- **Demo shortcuts.** The demo reads Frankfurt's final count after "losing" it; a real regional outage
  would not allow that. Clients are repointed by restarting them, not by Client Redirect.
- **Customer-facing SQL:** `streaming-harness/sql/09_customer_failover_runbook.sql`.
