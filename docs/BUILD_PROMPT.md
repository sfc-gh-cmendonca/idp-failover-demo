# Build prompt: IDP cross-region failover demo

Paste the prompt below into Cortex Code (or another coding agent) to rebuild this demo in your own
Snowflake and AWS accounts. Replace the values in `<angle brackets>`. The reference implementation is in this
repo; tell the agent to read it if you want an identical result.

---

**Prompt**

Build a live, operator-driven demo of cross-region disaster recovery for a product-reference-data service on
Snowflake **interactive tables**. Use two Snowflake accounts in the same organisation, a **primary**
`<ORG-PRIMARY_ACCOUNT>` in `<region A>` and a **secondary** `<ORG-SECONDARY_ACCOUNT>` in `<region B>`, plus an
AWS account for two small EC2 hosts. Work step by step and verify each part before moving on.

1. **Data model and Snowflake objects (primary).**
   - Database `IDP_DEMO`, schema `STREAMING`.
   - An **interactive table** `PRODUCT_PAYLOAD`, clustered by `PRODUCT_ID`, with columns:
     `PRODUCT_ID`, `PRODUCT_VERSION` (NUMBER, epoch microseconds), `EVENT_TYPE`, `EVENT_TS`, ISIN, CUSIP,
     SEDOL, ticker, asset class, currency, price, listing status, and the full message as `PAYLOAD VARIANT`.
   - Add search optimization `EQUALITY(PRODUCT_ID)`.
   - A Snowpipe Streaming pipe on the table.
   - An interactive warehouse `IDP_IW_XS` (XS, 6 clusters, `MAX_CONCURRENCY_LEVEL=32`) serving the table,
     plus a standard XS `IDP_ADMIN_WH`.
   - Key-pair service users and roles for streaming, load testing and demo operations. Each user gets its
     own network policy that allows only the EC2 IPs and operator IPs.

2. **Replication.**
   - Failover group `IDP_FG` containing database `IDP_DEMO`, replicating to the secondary on a
     10-minute schedule, with the replica created in the secondary.
   - In **each** account, a local, non-replicated database `IDP_ADMIN` with:
     - `PROMOTE_IDP()`: suspends the group if it isn't already suspended, runs
       `ALTER FAILOVER GROUP IDP_FG PRIMARY`, re-applies grants, and creates or resumes the interactive
       warehouse with the table attached.
     - `DEMO_OPS(action VARCHAR)`, `EXECUTE AS OWNER`, returning JSON. Actions:
       - `status`: is_primary, row count, max event time, last refresh snapshot time from
         `REPLICATION_GROUP_REFRESH_HISTORY`, warehouse state.
       - `dupes`: count of duplicate `(PRODUCT_ID, PRODUCT_VERSION)` keys.
       - `promote`: idempotent.
       - `suspend_fg`, `resume_fg`, `refresh_fg`, `iw_resume`, `iw_suspend`.
     - An ops service user that can **only** call `DEMO_OPS`.
   - Remember that warehouses, roles and grants don't replicate.

3. **Producer (EC2, Terraform, systemd).**
   - Python, using the **Snowpipe Streaming high-performance SDK**.
   - Generates realistic full-state product messages: valid ISIN/CUSIP/SEDOL check digits; event mix of
     roughly 80% price updates, 12% corporate actions, 6% status changes and 2% deletes.
   - Universe of 50k products, default 50 msg/s, 2 channels, with products hashed to channels.
   - Writes a local **JSONL journal** of every row handed to the SDK (hourly files, 24 h retention) and a
     `stats.json` every 2 s.
   - Target account is selected by an env var (`SF_TARGET=primary|secondary`).
   - A `replay.py`:
     - Takes a target and a since-timestamp, applies a 10-minute safety margin, and reads the journal from that point.
     - Opens a dedicated `idp_replay` channel first, to fence off stale writers.
     - Diffs the journal against the target on `(PRODUCT_ID, PRODUCT_VERSION)` and appends only the
       missing rows, in batches.
     - Waits for commit, then prints a JSON summary. Exits 2 on mismatch.
   - A restricted control command, `idp-demo-ctl`, installed as an SSH **forced command**:
     `status | start | stop | target primary|secondary | replay <target> <since> [dry]`.
   - Writing to a secondary's pipe must fail cleanly (410 read-only).

4. **Lookup traffic.**
   - A continuous **ADBC** (`adbc_driver_snowflake`) traffic engine: N connections spread over several
     processes started with `spawn` (fork breaks ADBC; the GIL caps a single process).
   - Point lookups of the latest version of a product (`QUALIFY ROW_NUMBER() … = 1`).
     80% of lookups go to a hot set of 1,000 IDs sampled from the table.
   - Prints one JSON line per second: `{ts, account, qps, p50_ms, p95_ms, errors, active, last_error}`.

5. **Storyline UI (EC2, FastAPI + React/Vite, systemd on :8080, HTTP basic auth, operator IPs only).**
   - Header cards for each account (primary/secondary, rows, seconds behind, last refresh).
   - A harness tile and a lookup-traffic tile, each showing only "Running"/"Stopped" plus its target region.
     The traffic tile has a "Stop traffic" button.
   - A live chart of **lookup p95 latency by serving region**.
   - Nine steps, each run by a button that is enabled only once the previous step is done. Each step shows
     narrative text and evidence. Re-running a done step resets all later steps; a failed step resumes.
     State persists to a JSON file. Updates are pushed over SSE.
   - The steps:
     1. Ingest on primary.
     2. Traffic on primary.
     3. Stop replication (suspend the group in the secondary; record the last snapshot time).
     4. Stop Integration and API Traffic (stop traffic and producer; wait for the primary's row count to stabilise; record it).
     5. Fail over (guard that the producer is stopped; promote; record RTO from producer stop).
     6. Replay the gap and reconcile (guard that the secondary is primary). Checks: rows = frozen count;
        a re-check finds 0 missing; duplicates equal the other region's baseline.
     7. Point ingest to the secondary.
     8. Point traffic to the secondary (record switchover seconds).
     9. Fail back. Resumable: stop ingest, refresh the original primary until counts match, promote it,
        resume the other side's refresh with retries, repoint ingest and traffic, suspend the secondary's warehouse.
   - Also provide a "Load test" tab and a re-runnable `deploy_to_ec2.sh --demo`. The script:
     - builds the frontend and generates the UI password;
     - creates a forced-command SSH key and installs it on the producer;
     - opens the security groups to the operator CIDRs and writes `/etc/idp-demo.env`.

6. **Deliverables.**
   - Customer-facing SQL runbook (setup, failover, failback).
   - A README with install steps.
   - A start/stop guide. Public IPs change on restart, so network rules and security groups must be
     updated; the producer autostarts at boot and must be stopped.
   - Never commit keys, passwords or Terraform state.

Acceptance:
- A full run shows RTO under 60 s.
- The replay is exact (journal re-check finds 0 missing).
- Row counts match after failover and after failback.
- The chart switches region within about 10 s at step 8.
