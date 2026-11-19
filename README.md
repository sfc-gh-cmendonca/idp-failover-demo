# IDP Cross-Region Failover Demo (Snowflake Interactive Tables)

A live demo of business continuity for a product-reference-data (IDP) service on Snowflake.
Product updates stream into an **interactive table** in Frankfurt and are replicated to Ireland by a
**failover group**. A React UI then runs a 9-step storyline: stop replication, declare Frankfurt down,
fail over, replay the unreplicated gap with exact-once reconciliation, move ingest and API lookup
traffic to Ireland, and fail back.

- **What the demo is and why:** [docs/DEMO_OVERVIEW.md](docs/DEMO_OVERVIEW.md)
- **Running it (handover / runbook):** [docs/HANDOVER.md](docs/HANDOVER.md)
- **Prompt to rebuild it from scratch:** [docs/BUILD_PROMPT.md](docs/BUILD_PROMPT.md)

```
 Producer EC2 (eu-west-1)                     Demo UI EC2 (eu-central-1)
 Snowpipe Streaming SDK + journal             FastAPI + React (:8080), ADBC lookup traffic
        |  (SF_TARGET = frankfurt|ireland)          |  ops calls        |  lookups (100 conns)
        v                                           v                   v
 +----------------- Frankfurt EU_DEMO59 ------+   +-------- Ireland CMENDONCA_DEMO_IR -------+
 | PRODUCT_PAYLOAD (interactive, SO)  primary | ->| PRODUCT_PAYLOAD replica      secondary   |
 | IDP_IW_XS (6 x XS interactive WH)          |IDP_FG| IDP_IW_XS                              |
 | IDP_ADMIN.PUBLIC.DEMO_OPS (owner's rights) | 10m| IDP_ADMIN.PUBLIC.DEMO_OPS               |
 +--------------------------------------------+   +-----------------------------------------+
```

## Repository layout
| Path | Contents |
|---|---|
| `streaming-harness/harness/` | `producer.py` (Snowpipe Streaming HP SDK), `udm.py` (synthetic product UDM), `journal.py` (24 h source journal), `replay.py` (gap replay), `idp-demo-ctl.sh` (restricted control command), `idp-target.sh` |
| `streaming-harness/terraform/` | Producer EC2 (eu-west-1) with systemd service `idp-harness` |
| `streaming-harness/sql/` | Snowflake setup, failover group, `PROMOTE_IDP`, `DEMO_OPS`, customer runbook (`09_…`) |
| `demo-ui/backend/` | FastAPI app; `app/demo/` holds the storyline orchestrator, Snowflake ops, producer control and traffic engine |
| `demo-ui/frontend/` | React + Vite UI ("Failover storyline" and "Load test" tabs) |
| `demo-ui/adbc_traffic.py` | Continuous ADBC lookup traffic (multi-process) |
| `demo-ui/java-harness/` | JDBC load test (`--continuous` mode is the fallback traffic engine) |
| `demo-ui/deploy_to_ec2.sh` | Provisions or reuses the UI EC2 and deploys everything (`--demo`) |
| `tools/` | `irsql.py` (run SQL in Ireland), `drive.py` (drive the UI API headlessly), `failback.py` |

## Installing from scratch

**No secrets are in this repo.** Keys, passwords, Terraform state and JARs are git-ignored; get the
keys from the demo owner or generate new ones (step 1).

### Prerequisites
- Two Snowflake accounts in the same org, **Business Critical** (or Enterprise with replication), in
  different regions. As built: `SFSEEUROPE-EU_DEMO59` (AWS eu-central-1) and `SFSEEUROPE-CMENDONCA_DEMO_IR`
  (AWS eu-west-1). Interactive tables and interactive warehouses must be available in both.
- ACCOUNTADMIN in both accounts.
- AWS account with EC2 rights in eu-central-1 and eu-west-1; AWS CLI configured.
- Locally: Terraform ≥ 1.5, Node 20+, Python 3.11, OpenSSL, `ssh`.

### 1. Keys (local, git-ignored)
```bash
# Snowflake key pairs: streaming user, load-test user, ops user
for k in streaming-harness/keys/rsa_key demo-ui/keys/rsa_key demo-ui/keys/ops_key; do
  mkdir -p "$(dirname $k)"
  openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out $k.p8
  openssl rsa -in $k.p8 -pubout -out $k.pub
done
ssh-keygen -t ed25519 -N '' -f streaming-harness/keys/idp_harness_ssh   # admin SSH to the producer
```
Paste each `.pub` body (without the BEGIN/END lines) into the matching `RSA_PUBLIC_KEY` in the SQL scripts.

### 2. Snowflake objects
Run in order, Frankfurt (primary) first, then the Ireland sections:

| Script | Where | What |
|---|---|---|
| `01_setup.sql` | Frankfurt | DB `IDP_DEMO`, schema `STREAMING`, interactive table `PRODUCT_PAYLOAD` (cluster by `PRODUCT_ID`), pipe, `IDP_IW_XS`, `IDP_ADMIN_WH`, streaming user, role and network policy |
| `04_failover_primary.sql` | Frankfurt | Failover group `IDP_FG` (database `IDP_DEMO`, replicate to Ireland, 10-min schedule) |
| `05_failover_secondary.sql` | Ireland | Streaming user/role, `IDP_ADMIN` DB, network rule/policy, `IDP_ADMIN_WH`, `CREATE FAILOVER GROUP IDP_FG AS REPLICA OF …` (Ireland's `IDP_IW_XS` is created by `PROMOTE_IDP` on first failover) |
| `07_promote_procedure.sql` | both | `IDP_ADMIN.PUBLIC.PROMOTE_IDP()` |
| `08_demo_ops.sql` | both | Ops user, network rules, `DEMO_OPS(action)`; Ireland section also creates the load-test user |

Then in **both** accounts:
```sql
ALTER TABLE IDP_DEMO.STREAMING.PRODUCT_PAYLOAD ADD SEARCH OPTIMIZATION ON EQUALITY(PRODUCT_ID); -- Frankfurt only (replicates)
ALTER WAREHOUSE IDP_IW_XS SET MAX_CONCURRENCY_LEVEL = 32;   -- warehouses don't replicate: set in each account
```
Network rules `IDP_HARNESS_INGRESS_RULE`, `IDP_LOADTEST_INGRESS_RULE`, `IDP_DEMO_OPS_INGRESS_RULE` must list the
EC2 public IPs (filled in at step 5) plus your operator IPs.

### 3. Producer EC2 (eu-west-1)
```bash
cd streaming-harness/terraform
cat > terraform.tfvars <<EOF
ssh_allowed_cidr = "<your-ip>/32"
EOF
terraform init && terraform apply
eval "$(terraform output -raw install_key)"   # copies the Snowflake key and starts idp-harness
```
The service streams at 50 msg/s (`rate_per_sec`) with the journal in `/opt/idp-harness/journal`.
It **autostarts at boot**; stop it until the demo: `sudo systemctl stop idp-harness`.

### 4. Demo UI EC2 (eu-central-1)
```bash
cd demo-ui
mkdir -p lib && cd lib     # JDBC fallback engine + Streamlit load-test tab (any recent versions work)
curl -LO https://repo1.maven.org/maven2/net/snowflake/snowflake-jdbc/3.19.0/snowflake-jdbc-3.19.0.jar && mv snowflake-jdbc-3.19.0.jar snowflake-jdbc.jar
curl -LO https://repo1.maven.org/maven2/org/slf4j/slf4j-api/2.0.13/slf4j-api-2.0.13.jar && mv slf4j-api-2.0.13.jar slf4j-api.jar
curl -LO https://repo1.maven.org/maven2/org/slf4j/slf4j-simple/2.0.13/slf4j-simple-2.0.13.jar && mv slf4j-simple-2.0.13.jar slf4j-simple.jar
cd .. && cp .streamlit/secrets.toml.example .streamlit/secrets.toml

INSTANCE_NAME=rdp-test-harness-idp SG_NAME=rdp-harness-idp-sg \
PRODUCER_INSTANCE=<producer-instance-id> PRODUCER_REGION=eu-west-1 \
OPERATOR_CIDRS="<your-ip>/32" ./deploy_to_ec2.sh --demo
```
`--demo` builds the frontend, generates the UI password into `keys/demo_password` and a restricted SSH key
(`keys/producer_ctl_ssh`, forced to `idp-demo-ctl`), opens :8080 to `OPERATOR_CIDRS`, and installs the
systemd unit `idp-demo` (uvicorn on :8080). Re-running it is safe and is how code changes are deployed.

### 5. Wire up IPs and open the UI
1. Put the producer IP into `IDP_HARNESS_INGRESS_RULE` and the UI host IP into `IDP_LOADTEST_INGRESS_RULE` /
   `IDP_DEMO_OPS_INGRESS_RULE`, **in both accounts**.
2. Open `http://<ui-ip>:8080` (any username, password from `keys/demo_password`).
   If a corporate proxy (e.g. Zscaler) blocks the JS bundle, tunnel it:
   `ssh -f -N -L 8080:localhost:8080 -i demo-ui/rdp-harness-key.pem ec2-user@<ui-ip>` → `http://localhost:8080`.

See [docs/HANDOVER.md](docs/HANDOVER.md) for day-to-day start/stop and running the storyline.
