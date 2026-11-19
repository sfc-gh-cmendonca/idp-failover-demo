# Handover: running the IDP failover demo

Owner: Christophe Mendonca. This doc is for whoever runs the demo while he is away.
Passwords and keys are **not** in the repo; get the `keys/` folders and the UI password from Christophe.

## 1. Current environment
| | Frankfurt (normal primary) | Ireland (normal secondary) |
|---|---|---|
| Account | `SFSEEUROPE-EU_DEMO59` | `SFSEEUROPE-CMENDONCA_DEMO_IR` (locator LU18314) |
| Region / deployment | AWS_EU_CENTRAL_1 / `eufrankfurt` | AWS_EU_WEST_1 / `ie` |

- **Table:** `IDP_DEMO.STREAMING.PRODUCT_PAYLOAD`
  - Interactive and append-only, clustered by `PRODUCT_ID`, with search optimization `EQUALITY(PRODUCT_ID)`.
  - ~29M rows.
- **Failover group:** `IDP_FG` covers database `IDP_DEMO` and refreshes every 10 minutes.
- **Warehouses:**
  - `IDP_IW_XS`: interactive, 6 × XS, `MAX_CONCURRENCY_LEVEL=32`, 24 h auto-suspend, so **suspend it after the demo**.
  - `IDP_ADMIN_WH`: XS.
- **Local `IDP_ADMIN` database** in each account (not replicated): `PROMOTE_IDP()`, `DEMO_OPS(action)`, network rules.
- **Service users:**
  - `IDP_STREAMING_SVC`: the producer.
  - `IDP_LOADTEST_SVC`: lookup traffic.
  - `IDP_DEMO_OPS_SVC`: the UI backend; it can only call `DEMO_OPS`.

**AWS** (account 968832918655, IAM user `snowflake1`; that user has no SSM rights):
| Instance | Name | Region | Purpose |
|---|---|---|---|
| `i-058d045d3fce87d3c` | rdp-test-harness-idp (m5.xlarge) | eu-central-1 | UI + backend (systemd `idp-demo`, :8080), ADBC traffic |
| `i-0b0f6686e308caeba` | idp-streaming-harness (t3.small) | eu-west-1 | Producer (systemd `idp-harness`) |
| `i-0635987c09c81082b` | rdp-test-harness | eu-central-1 | Older harness, **not needed** |

- **Security groups:**
  - UI: `rdp-harness-idp-sg` (`sg-0da56525c8bb3e61a`).
  - Producer: `sg-0d3afa748ae5ce0b6`.
- **SSH keys** (from Christophe):
  - UI host: `demo-ui/rdp-harness-key.pem`
  - Producer: `streaming-harness/keys/idp_harness_ssh`
- **Allowed operator IPs:** 213.86.140.86, 213.161.68.226, and 147.161.142.0/24 (Zscaler egress). **Add your own IP.**

## 2. Starting the demo (about 10 minutes)
There are no Elastic IPs, so **both public IPs change on every start**. Steps 3–5 are therefore mandatory.

1. Start the instances and read the new IPs:
   ```bash
   aws ec2 start-instances --region eu-central-1 --instance-ids i-058d045d3fce87d3c
   aws ec2 start-instances --region eu-west-1   --instance-ids i-0b0f6686e308caeba
   aws ec2 describe-instances --region eu-central-1 --instance-ids i-058d045d3fce87d3c --query 'Reservations[0].Instances[0].PublicIpAddress' --output text
   aws ec2 describe-instances --region eu-west-1   --instance-ids i-0b0f6686e308caeba --query 'Reservations[0].Instances[0].PublicIpAddress' --output text
   ```
2. **Stop the producer, which autostarts at boot:**
   `ssh -i streaming-harness/keys/idp_harness_ssh ec2-user@<PRODUCER_IP> 'sudo systemctl stop idp-harness'`
3. Update the Snowflake network rules in **both** accounts as ACCOUNTADMIN, using your operator IPs:
   ```sql
   ALTER NETWORK RULE IDP_ADMIN.PUBLIC.IDP_HARNESS_INGRESS_RULE  SET VALUE_LIST=('<PRODUCER_IP>','<operator ips>');
   ALTER NETWORK RULE IDP_ADMIN.PUBLIC.IDP_LOADTEST_INGRESS_RULE SET VALUE_LIST=('<UI_IP>','<operator ips>');
   ALTER NETWORK RULE IDP_ADMIN.PUBLIC.IDP_DEMO_OPS_INGRESS_RULE SET VALUE_LIST=('<UI_IP>','<operator ips>');
   ```
4. Let the UI host reach the producer over SSH:
   `aws ec2 authorize-security-group-ingress --region eu-west-1 --group-id sg-0d3afa748ae5ce0b6 --protocol tcp --port 22 --cidr <UI_IP>/32`
   Revoke the previous UI IP so old entries don't pile up.
5. Redeploy the UI, which writes the new producer IP into the backend config:
   ```bash
   cd demo-ui
   INSTANCE_NAME=rdp-test-harness-idp SG_NAME=rdp-harness-idp-sg \
   OPERATOR_CIDRS="213.86.140.86/32 213.161.68.226/32 147.161.142.0/24 <your-ip>/32" ./deploy_to_ec2.sh --demo
   ```
6. Open the UI at `http://<UI_IP>:8080` (any username; the password comes from Christophe).
   - If the page is **blank**, Zscaler is blocking the JS bundle. Use a tunnel:
     `ssh -f -N -o ServerAliveInterval=30 -L 8080:localhost:8080 -i demo-ui/rdp-harness-key.pem ec2-user@<UI_IP>`
     then open `http://localhost:8080`.
   - A redeploy drops the tunnel; just re-run that command.
7. Health check: the two account cards show Frankfurt as primary and Ireland as secondary, and the harness and traffic tiles show "Stopped".
   Check that both row counts match before you start. If they don't, wait for Ireland's next 10-minute refresh.
   Then press **Reset**.

## 3. Running the storyline
Run the steps in order; each one waits for the previous one to finish. Re-running a finished step resets every step after it.
A failed step can be re-run and resumes from where it stopped. The UI shows evidence such as timings and row counts for each step.

| # | Step | What happens | What to say |
|---|---|---|---|
| 1 | Ingest on Frankfurt | Producer streams 50 msg/s into Frankfurt; every row is journaled at source | Snowpipe Streaming high-performance SDK; the journal stands in for the upstream replay buffer (Kafka or AMPS) |
| 2 | Traffic on Frankfurt | 100 ADBC connections run point lookups on `IDP_IW_XS`; the chart shows p95 latency | This is the read path we have to keep alive |
| 3 | Stop replication to Ireland | `ALTER FAILOVER GROUP IDP_FG SUSPEND` in Ireland; the last snapshot time is recorded | Ireland is frozen at its last replicated point; Frankfurt keeps writing, so the gap grows |
| 4 | Stop Integration and API Traffic | Lookup traffic and producer stop; Frankfurt's final row count is recorded | "Frankfurt is down." The rows written after the snapshot are the RPO gap |
| 5 | Fail over to Ireland | `DEMO_OPS('promote')` → `ALTER FAILOVER GROUP IDP_FG PRIMARY`, creates/resumes Ireland's warehouse | RTO from writer stop to Ireland primary has been 39–57 s |
| 6 | Replay the gap and reconcile | Journal rows since the snapshot (minus a 10-minute margin) are diffed on `(PRODUCT_ID, PRODUCT_VERSION)`; only missing rows are streamed | Exactly-once on an append-only table. Checks: Ireland rows = Frankfurt frozen count, 0 missing, no new duplicates |
| 7 | Point ingest to Ireland | Producer is repointed to Ireland and restarted | Writes continue in the new primary |
| 8 | Point traffic to Ireland | Ireland's warehouse is resumed and traffic reconnects; the chart line changes colour | Switchover has been 7–11 s. In production Client Redirect (`ALTER CONNECTION … PRIMARY`) avoids changing clients |
| 9 | Fail back to Frankfurt | Ingest stops, Frankfurt refreshes from Ireland until the counts match, Frankfurt is promoted, Ireland's refresh is resumed, ingest and traffic return to Frankfurt, Ireland's warehouse is suspended | Zero data loss on failback |

- **Customer-facing SQL:** `streaming-harness/sql/09_customer_failover_runbook.sql`.
- **Slides:** "JPMC IDP – Snowflake Architecture & 1-Year Cost Estimate" (Google Slides, ask Christophe). Slides 9–15 cover BCDR; slide 10 is the data-flow diagram.

Expected numbers on 6 × XS Frankfurt:
- ~1,250 lookups/s with ingest off.
- ~650 lookups/s with ingest on.
Ingest competes with lookups on the append-only table. The planned fix is a CDC current-state table, which needs the `FEATURE_INTERACTIVE_TABLE_CDC` private preview.

## 4. Stopping the demo (do this every time, to save cost)
```bash
aws ec2 stop-instances --region eu-central-1 --instance-ids i-058d045d3fce87d3c
aws ec2 stop-instances --region eu-west-1   --instance-ids i-0b0f6686e308caeba
```
Then in **both** accounts: `ALTER WAREHOUSE IDP_IW_XS SUSPEND;`. Its auto-suspend is 24 h, so it keeps billing if you skip this.

Make sure Frankfurt is primary again at the end, either by running step 9 or with `tools/failback.py`.

## 5. Troubleshooting
| Symptom | Cause / fix |
|---|---|
| UI page blank after login | Zscaler is blocking the JS bundle; use the SSH tunnel (section 2.6) |
| SSH hangs ("banner exchange" timeout) | Your egress IP isn't in the security group; Zscaler egress varies within 147.161.142.0/24. Find your real IP with `ssh … 'echo $SSH_CLIENT'` from a host that lets you in, then add it |
| Account card shows an error / network policy | Network rules still hold the old EC2 IPs (section 2.3) |
| Step 1/7 producer errors, 410 `ERR_RESOLVED_PIPE_IS_READ_ONLY` | Writing to a secondary. Ingest must target the current primary |
| Step 6 "Ireland is not primary" | Run step 5 first |
| Step 9 "cannot be resumed from a primary" | Transient while the new secondary registers; re-run step 9 (it resumes) |
| Duplicate check shows 4,295 | Historic baseline from 30 Oct; the check compares against it, not zero |
| Producer service commands | `sudo systemctl status/stop/start idp-harness` on the producer; switch region with `idp-target frankfurt|ireland` |
| Run SQL in Ireland from a laptop | `tools/irsql.py "<sql>"` (needs Ireland key-pair user configured in the script) |
